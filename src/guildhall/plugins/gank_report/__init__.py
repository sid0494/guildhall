"""Gank Report: post-match reports on the Dota 2 skills a player is practising.

Data comes from the OpenDota API. A match is fetched once, waiting for
OpenDota to parse it, and its raw response archived; each linked account in it
is then analysed from that archive (docs/adr/0043).
"""

from datetime import timedelta
from typing import Any, Final

from pydantic import PositiveInt
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import Insert, insert

from guildhall.contract import (
    Archive,
    Context,
    ContractModel,
    Job,
    JobType,
    Outcome,
    PermanentError,
    RateLimitPolicy,
    SourcePlugin,
)
from guildhall.plugins.gank_report.opendota import (
    Match,
    check_status,
    match_url,
    parse,
    parse_request_url,
)
from guildhall.plugins.gank_report.tables import matches, metadata

PARSE_RETRY_DELAYS: Final = (timedelta(minutes=5), timedelta(minutes=10))
"""The waits after the first and second unparsed fetches. The attempt after
the last wait finishes with whatever it gets (docs/adr/0044)."""

MATCH_ARCHIVE_KIND: Final = "match"


class FetchMatch(ContractModel):
    """Fetch one match, wait for its parse, archive it and record it."""

    match_id: PositiveInt


class GankReportPlugin(SourcePlugin):
    name = "gank_report"
    # OpenDota allows 60 calls a minute and 3,000 a day per IP address without
    # a key; the headroom absorbs window mismatch and calls made by hand
    # (docs/adr/0048).
    rate_limit = RateLimitPolicy(per_minute=50, per_day=2_000)
    tables = metadata

    def job_types(self) -> list[JobType[Any]]:
        return [JobType("fetch_match", FetchMatch, self.fetch_match)]

    async def fetch_match(self, job: Job[FetchMatch], ctx: Context) -> Outcome:
        """Fetch the match; while it is unparsed, ask for a parse and wait.

        Every unparsed fetch but the last asks again, so a lost request or an
        earlier failed attempt is made up for. The last attempt records the
        match unparsed, and parsed-only metrics will be missing.
        """
        match_id = job.payload.match_id
        response = await ctx.http.get(match_url(match_id))
        check_status(response, "match")
        match = parse(Match, response.body)
        if match.match_id != match_id:
            raise PermanentError("OpenDota answered with a different match")

        if not match.parsed and job.attempt <= len(PARSE_RETRY_DELAYS):
            # Best effort: an error here leaves the retry to ask again, and the
            # last attempt records the match unparsed anyway.
            await ctx.http.post(parse_request_url(match_id))
            return Outcome(retry_in=PARSE_RETRY_DELAYS[job.attempt - 1])

        return Outcome(
            writes=(_record_match(match),),
            archives=(
                Archive(kind=MATCH_ARCHIVE_KIND, id=str(match_id), body=response.body),
            ),
        )


def _record_match(match: Match) -> Insert:
    """Insert the match row, or update it if the match is fetched again."""
    values = {
        "played_at": match.played_at,
        "duration_seconds": match.duration,
        "game_mode": match.game_mode,
        "lobby_type": match.lobby_type,
        "parsed": match.parsed,
    }
    statement = insert(matches).values(match_id=match.match_id, **values)
    return statement.on_conflict_do_update(
        index_elements=[matches.c.match_id],
        set_={**values, "fetched_at": func.now()},
    )
