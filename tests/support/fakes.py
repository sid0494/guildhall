"""Fakes of the services a handler gets in `Context`, shared by every test layer.

Each one implements a contract protocol (`Database`, `RawArchive`, `Http`), and
mypy checks it against that protocol wherever it is passed to `Context`, so a
fake cannot drift from the interface it stands in for (docs/adr/0049).

The `No*` fakes fail the test if they are used at all; the others answer from
preset data and record what was asked of them.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Row, Select

from guildhall.contract import Context, HttpResponse
from guildhall.contract.http import Params


class NoDatabase:
    """`Context.db` for code that must not read: fails the test if it does."""

    async def fetch(self, statement: Select[Any]) -> Sequence[Row[Any]]:
        raise AssertionError("unexpected database read")


class NoArchive:
    """`Context.archive` for code that must not read archives."""

    async def get(self, kind: str, id: str) -> bytes | None:
        raise AssertionError("unexpected archive read")


class NoHttp:
    """`Context.http` for code that must not call upstream."""

    async def get(self, url: str, *, params: Params | None = None) -> HttpResponse:
        raise AssertionError("unexpected upstream call")

    async def post(
        self, url: str, *, params: Params | None = None, json: Any = None
    ) -> HttpResponse:
        raise AssertionError("unexpected upstream call")


def no_io_context() -> Context:
    """A context whose every service fails the test if used."""
    return Context(db=NoDatabase(), archive=NoArchive(), http=NoHttp())


@dataclass
class RowsDatabase:
    """`Context.db` that answers every read with the same preset rows.

    It records each statement but cannot tell whether a query is right: the
    real SQL is tested against Postgres in integration tests. Rows can be any
    objects with the attributes the handler reads.
    """

    rows: Sequence[Any] = ()
    statements: list[Select[Any]] = field(default_factory=list)

    async def fetch(self, statement: Select[Any]) -> Sequence[Row[Any]]:
        self.statements.append(statement)
        return self.rows


@dataclass
class DictArchive:
    """`Context.archive` backed by a dict of `(kind, id)` to body."""

    bodies: dict[tuple[str, str], bytes] = field(default_factory=dict)
    reads: list[tuple[str, str]] = field(default_factory=list)

    async def get(self, kind: str, id: str) -> bytes | None:
        self.reads.append((kind, id))
        return self.bodies.get((kind, id))


@dataclass(frozen=True)
class Call:
    """One call a handler made through `Context.http`."""

    method: str
    url: str
    params: Params | None = None
    json: Any = None


class FakeHttp:
    """`Context.http` that answers from preset responses and records each call.

    Responses are keyed by method and URL, without the query string; a call
    with no preset response fails the test.
    """

    def __init__(self, responses: Mapping[tuple[str, str], HttpResponse]) -> None:
        self._responses = responses
        self.calls: list[Call] = []

    async def get(self, url: str, *, params: Params | None = None) -> HttpResponse:
        return self._answer(Call("GET", url, params))

    async def post(
        self, url: str, *, params: Params | None = None, json: Any = None
    ) -> HttpResponse:
        return self._answer(Call("POST", url, params, json))

    def _answer(self, call: Call) -> HttpResponse:
        self.calls.append(call)
        response = self._responses.get((call.method, call.url))
        if response is None:
            raise AssertionError(f"unexpected upstream call: {call.method} {call.url}")
        return response


def response(body: bytes | Any, status: int = 200) -> HttpResponse:
    """A JSON response; `body` is sent as is if bytes, or encoded as JSON."""
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    return HttpResponse(
        status=status, headers={"content-type": "application/json"}, body=raw
    )
