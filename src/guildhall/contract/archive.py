"""The raw archive: upstream responses kept exactly as received (docs/adr/0031).

A handler archives a response by returning an `Archive` in `Outcome.archives`;
the platform uploads it under `raw/<plug-in>/<kind>/<id>.json` before the job
commits, so a committed job's archives always exist. Archiving the same kind
and id again replaces the earlier body. `Context.archive` reads bodies back,
so metrics can be recomputed without calling the upstream again.
"""

from typing import Protocol


class RawArchive(Protocol):
    """Read access to the plug-in's own archived responses."""

    async def get(self, kind: str, id: str) -> bytes | None:
        """The body archived under `kind` and `id`, or None if there is none."""
        ...
