"""The shared fakes answer as preset and fail loudly on anything else."""

from typing import Any

import pytest
from fakes import (
    Call,
    DictArchive,
    FakeHttp,
    RowsDatabase,
    ScriptedDatabase,
    no_io_context,
    response,
)
from sqlalchemy import Select, column, select

URL = "https://upstream.example/items/1"


async def test_fake_http_answers_preset_calls_and_records_them() -> None:
    http = FakeHttp({("GET", URL): response({"id": 1}), ("POST", URL): response(b"{}")})
    got = await http.get(URL, params={"limit": 2})
    posted = await http.post(URL, json={"a": 1})
    assert (got.status, got.json(), posted.body) == (200, {"id": 1}, b"{}")
    assert http.calls == [
        Call("GET", URL, {"limit": 2}),
        Call("POST", URL, None, {"a": 1}),
    ]


async def test_fake_http_fails_an_unexpected_call() -> None:
    http = FakeHttp({("GET", URL): response({})})
    with pytest.raises(AssertionError, match=f"unexpected upstream call: POST {URL}"):
        await http.post(URL)


def test_response_keeps_a_status() -> None:
    assert response({"error": "Not Found"}, status=404).status == 404


async def test_dict_archive_answers_by_kind_and_id() -> None:
    archive = DictArchive({("match", "1"): b"{}"})
    assert await archive.get("match", "1") == b"{}"
    assert await archive.get("match", "2") is None
    assert archive.reads == [("match", "1"), ("match", "2")]


async def test_rows_database_answers_every_read_with_its_rows() -> None:
    row = object()
    database = RowsDatabase(rows=[row])
    statement: Select[Any] = select(column("x"))
    assert list(await database.fetch(statement)) == [row]
    assert database.statements == [statement]


async def test_the_no_io_context_fails_on_any_use() -> None:
    context = no_io_context()
    with pytest.raises(AssertionError, match="unexpected database read"):
        await context.db.fetch(select(column("x")))
    with pytest.raises(AssertionError, match="unexpected archive read"):
        await context.archive.get("match", "1")
    with pytest.raises(AssertionError, match="unexpected upstream call"):
        await context.http.get(URL)


async def test_scripted_database_answers_reads_in_order_then_fails() -> None:
    first, second = object(), object()
    database = ScriptedDatabase([first], [second])
    statement: Select[Any] = select(column("x"))
    assert list(await database.fetch(statement)) == [first]
    assert list(await database.fetch(statement)) == [second]
    with pytest.raises(AssertionError, match="unexpected database read"):
        await database.fetch(statement)
    assert len(database.statements) == 3
