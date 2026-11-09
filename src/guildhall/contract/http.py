"""Calls to a plug-in's upstream API: `Context.http` (docs/adr/0035).

Every call is rate-limited against the plug-in's `RateLimitPolicy`, shared by
every worker replica (docs/adr/0034). When a call cannot go out soon, the
platform defers the whole job without counting it as a failure
(docs/adr/0033), so handlers must not catch broad exceptions around
`ctx.http` calls. A `5xx`, a timeout or a connection failure raises
`RetryableError`; every other status, `404` included, is returned for the
handler to judge.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

type Params = Mapping[str, str | int]


@dataclass(frozen=True)
class HttpResponse:
    """An upstream response. Header names are lower case; `body` is as received."""

    status: int
    headers: Mapping[str, str]
    body: bytes = field(repr=False)

    def json(self) -> Any:
        """The body parsed as JSON."""
        return json.loads(self.body)


class Http(Protocol):
    """Rate-limited HTTP for one plug-in."""

    async def get(self, url: str, *, params: Params | None = None) -> HttpResponse:
        """GET `url`, with `params` added to its query string."""
        ...

    async def post(
        self, url: str, *, params: Params | None = None, json: Any = None
    ) -> HttpResponse:
        """POST `url`, with `json` as the body if given."""
        ...
