"""Gank Report: post-match reports on the Dota 2 skills a player is practising.

Data comes from the OpenDota API. A match is fetched once, waiting for
OpenDota to parse it, and its raw response archived; each linked account in it
is then analysed from that archive (docs/adr/0043).
"""

from datetime import timedelta
from typing import Any, Final

from pydantic import PositiveInt
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import Insert, insert

from guildhall.contract import (
    Archive,
    Context,
    ContractModel,
    Job,
    JobType,
    LinkedAccount,
    Outcome,
    PermanentError,
    RateLimitPolicy,
    SourcePlugin,
)
from guildhall.plugins.gank_report.modes import Mode, tracked_mode
from guildhall.plugins.gank_report.opendota import (
    Match,
    Player,
    check_status,
    match_url,
    parse,
    parse_request_url,
)
from guildhall.plugins.gank_report.tables import matches, metadata, player_matches

PARSE_RETRY_DELAYS: Final = (timedelta(minutes=5), timedelta(minutes=10))
"""The waits after the first and second unparsed fetches. The attempt after
the last wait finishes with whatever it gets (docs/adr/0044)."""

MATCH_ARCHIVE_KIND: Final = "match"

ANALYZE_RETRY_DELAY: Final = timedelta(minutes=5)
ANALYZE_MAX_ATTEMPTS: Final = 24
"""An analysis waits for its match's fetch for about two hours, enough for a
backfill burst, then fails so the stuck fetch gets looked at (docs/adr/0046)."""


class FetchMatch(ContractModel):
    """Fetch one match, wait for its parse, archive it and record it."""

    match_id: PositiveInt


class AnalyzeMatch(ContractModel):
    """Record one linked account's part in a fetched match."""

    match_id: PositiveInt
    account: LinkedAccount


class GankReportPlugin(SourcePlugin):
    name = "gank_report"
    # OpenDota allows 60 calls a minute and 3,000 a day per IP address without
    # a key; the headroom absorbs window mismatch and calls made by hand
    # (docs/adr/0048).
    rate_limit = RateLimitPolicy(per_minute=50, per_day=2_000)
    tables = metadata

    def job_types(self) -> list[JobType[Any]]:
        return [
            JobType("fetch_match", FetchMatch, self.fetch_match),
            JobType("analyze_match", AnalyzeMatch, self.analyze_match),
        ]

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
        mode = tracked_mode(match.game_mode, match.lobby_type, match.duration)
        if mode is None:
            # The poll never asks for one, so this is a bug or a changed rule;
            # fail before spending calls on a parse.
            raise PermanentError("match is not in a tracked mode")

        if not match.parsed and job.attempt <= len(PARSE_RETRY_DELAYS):
            # Best effort: an error here leaves the retry to ask again, and the
            # last attempt records the match unparsed anyway.
            await ctx.http.post(parse_request_url(match_id))
            return Outcome(retry_in=PARSE_RETRY_DELAYS[job.attempt - 1])

        return Outcome(
            writes=(_record_match(match, mode),),
            archives=(
                Archive(kind=MATCH_ARCHIVE_KIND, id=str(match_id), body=response.body),
            ),
        )

    async def analyze_match(self, job: Job[AnalyzeMatch], ctx: Context) -> Outcome:
        """Once the match's fetch is final, record this account's part in it.

        It reads the archived response, never OpenDota, so analysing a match
        again (for a new metric, say) costs no upstream calls.
        """
        match_id = job.payload.match_id
        account_id = _dota_account_id(job.payload.account)
        fetched = await ctx.db.fetch(
            select(matches.c.match_id).where(matches.c.match_id == match_id)
        )
        if not fetched:
            if job.attempt < ANALYZE_MAX_ATTEMPTS:
                return Outcome(retry_in=ANALYZE_RETRY_DELAY)
            raise PermanentError("match was never fetched")

        body = await ctx.archive.get(MATCH_ARCHIVE_KIND, str(match_id))
        if body is None:
            raise PermanentError("match is recorded but its archive is missing")
        match = parse(Match, body)
        player = match.player(account_id)
        if player is None:
            raise PermanentError("the linked account is not among the match's players")
        return Outcome(writes=(_record_player(match_id, account_id, player),))


def _dota_account_id(account: LinkedAccount) -> int:
    if not account.external_id.isdecimal():
        raise PermanentError("the linked account is not a Dota account ID")
    return int(account.external_id)


def _record_match(match: Match, mode: Mode) -> Insert:
    """Insert the match row, or update it if the match is fetched again."""
    values = {
        "played_at": match.played_at,
        "duration_seconds": match.duration,
        "game_mode": match.game_mode,
        "lobby_type": match.lobby_type,
        "mode": mode.value,
        "parsed": match.parsed,
    }
    statement = insert(matches).values(match_id=match.match_id, **values)
    return statement.on_conflict_do_update(
        index_elements=[matches.c.match_id],
        set_={**values, "fetched_at": func.now()},
    )


def _record_player(match_id: int, account_id: int, player: Player) -> Insert:
    """Insert the player row, or update it when the match is analysed again."""
    values = {
        "hero_id": player.hero_id,
        "player_slot": player.player_slot,
        "won": player.win,
    }
    statement = insert(player_matches).values(
        match_id=match_id, account_id=account_id, **values
    )
    return statement.on_conflict_do_update(
        index_elements=[player_matches.c.match_id, player_matches.c.account_id],
        set_={**values, "analyzed_at": func.now()},
    )
