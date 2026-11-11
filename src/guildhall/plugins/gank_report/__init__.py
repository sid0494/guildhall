"""Gank Report: post-match reports on the Dota 2 skills a player is practising.

Data comes from the OpenDota API. A match is fetched once, waiting for
OpenDota to parse it, and its raw response archived; each linked account in it
is then analysed from that archive (docs/adr/0043).
"""

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Final

from pydantic import PositiveInt
from sqlalchemy import Row, Select, func, select
from sqlalchemy.dialects.postgresql import Insert, insert

from guildhall.contract import (
    Accepted,
    AccountLink,
    Archive,
    Command,
    CommandOutcome,
    Context,
    ContractModel,
    Event,
    Invocation,
    Job,
    JobSpec,
    JobType,
    LinkedAccount,
    LinkRequest,
    Message,
    Outcome,
    PermanentError,
    RateLimitPolicy,
    Rejected,
    Schedule,
    ScheduledRun,
    SourcePlugin,
)
from guildhall.plugins.gank_report.account_ids import dota_account_id
from guildhall.plugins.gank_report.events import MatchAnalyzed, MetricResult
from guildhall.plugins.gank_report.metrics import METRICS
from guildhall.plugins.gank_report.modes import Mode, tracked_mode
from guildhall.plugins.gank_report.opendota import (
    Match,
    MatchList,
    Player,
    check_status,
    match_url,
    parse,
    parse_request_url,
    player_matches_url,
)
from guildhall.plugins.gank_report.render import render_match
from guildhall.plugins.gank_report.tables import (
    matches,
    metadata,
    metrics,
    player_matches,
    polled_matches,
)

POLL_CRON: Final = "*/15 * * * *"
POLL_PARAMS: Final = {"limit": 20, "date": 365, "significant": 0}
"""The 20 newest matches of the past year, Turbo included: OpenDota's default
filter would drop it, so the plug-in applies its own (docs/adr/0051, 0052)."""
BACKFILL_PARAMS: Final = {"limit": 100, "significant": 0}
"""After `/link`: the 100 newest matches, however old (docs/adr/0067)."""
VERIFY_PARAMS: Final = {"limit": 1, "significant": 0}
REPLAY_LIFETIME: Final = timedelta(days=7)
"""A match that ended longer ago than this is not worth asking OpenDota to
parse: its replay is most likely gone (docs/adr/0067)."""
NOT_AN_ACCOUNT = (
    "That doesn't look like a Dota account ID or a Steam ID. Your account ID is"
    " the number in your OpenDota profile's address: opendota.com/players/<id>."
)
LINK_FIRST = "Link your Dota account first: /link dota."
NOTHING_ANALYSED_YET = (
    "No analysed matches yet; reports appear a few minutes after a game."
)
NO_MATCHES_VISIBLE = (
    "OpenDota can't see any matches for that account. Check that Expose Public"
    " Match Data is on in Dota's settings, or play a match first."
)
ANALYZE_DELAY: Final = timedelta(minutes=5)
"""An analysis is queued this late, since its fetch is never done sooner."""
POST_WINDOW: Final = timedelta(hours=12)
"""Only a match that ended this recently is posted to Discord: older ones (a
first poll, a backfill, a recompute) are recorded quietly (docs/adr/0056)."""
BASELINE_GAMES: Final = 20
"""How many previous games on the same hero and kind a baseline averages."""

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


class MatchLastOptions(ContractModel):
    """`/match last` takes no options."""


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

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))

    def job_types(self) -> list[JobType[Any]]:
        return [
            JobType("fetch_match", FetchMatch, self.fetch_match),
            JobType("analyze_match", AnalyzeMatch, self.analyze_match),
            JobType("poll_matches", ScheduledRun, self.poll_matches),
            JobType("backfill", LinkedAccount, self.backfill),
        ]

    def event_types(self) -> list[type[Event]]:
        return [MatchAnalyzed]

    def commands(self) -> list[Command[Any]]:
        return [
            Command(
                ("match", "last"),
                "Your latest analysed match",
                MatchLastOptions,
                self.match_last,
            )
        ]

    async def match_last(
        self, invocation: Invocation[MatchLastOptions], ctx: Context
    ) -> CommandOutcome:
        """The latest analysed match's report, rebuilt from what is stored,
        with the averages as they stood for that match (docs/adr/0073)."""
        account = invocation.account
        if account is None:
            return CommandOutcome(reply=Message(content=LINK_FIRST))
        account_id = _dota_account_id(account)
        latest = await ctx.db.fetch(
            select(
                player_matches.c.match_id,
                player_matches.c.hero_id,
                player_matches.c.won,
                matches.c.mode,
                matches.c.parsed,
                matches.c.played_at,
                matches.c.duration_seconds,
            )
            .join(matches, matches.c.match_id == player_matches.c.match_id)
            .where(player_matches.c.account_id == account_id)
            .order_by(matches.c.played_at.desc())
            .limit(1)
        )
        if not latest:
            return CommandOutcome(reply=Message(content=NOTHING_ANALYSED_YET))
        match = latest[0]
        stored = await ctx.db.fetch(
            select(metrics.c.metric, metrics.c.value).where(
                metrics.c.match_id == match.match_id,
                metrics.c.account_id == account_id,
            )
        )
        by_name = {row.metric: row.value for row in stored}
        values = {m.name: by_name[m.name] for m in METRICS if m.name in by_name}
        mode = Mode(match.mode)
        averages = await ctx.db.fetch(
            _baselines(account_id, match.hero_id, mode, match.played_at)
        )
        event = MatchAnalyzed(
            dedupe_key=f"gank_report:match_analyzed:{match.match_id}:{account.id}",
            match_id=match.match_id,
            discord_user_id=account.discord_user_id,
            hero_id=match.hero_id,
            won=match.won,
            mode=mode.value,
            parsed=match.parsed,
            played_at=match.played_at,
            duration_seconds=match.duration_seconds,
            metrics=_results(values, averages),
        )
        return CommandOutcome(reply=render_match(event, mention=False))

    def render(self, event: Event) -> Message | None:
        if isinstance(event, MatchAnalyzed):
            return render_match(event)
        return None

    def account_link(self) -> AccountLink | None:
        return AccountLink(
            kind="dota",
            description="Your Dota account ID (opendota.com/players/<id>) or Steam ID",
            verify=self.verify_account,
            on_link="backfill",
        )

    async def verify_account(
        self, request: LinkRequest, ctx: Context
    ) -> Accepted | Rejected:
        """Accept an account OpenDota can see matches for (docs/adr/0067).

        OpenDota answers with a profile even for IDs that cannot exist, so the
        test is the one that matters to Gank Report: a visible match.
        """
        account_id = dota_account_id(request.external_id)
        if account_id is None:
            return Rejected(reason=NOT_AN_ACCOUNT)
        response = await ctx.http.get(
            player_matches_url(account_id), params=VERIFY_PARAMS
        )
        check_status(response, "player")
        if not parse(MatchList, response.body).root:
            return Rejected(reason=NO_MATCHES_VISIBLE)
        return Accepted(external_id=str(account_id))

    async def backfill(self, job: Job[LinkedAccount], ctx: Context) -> Outcome:
        """After `/link`: queue the account's 100 newest tracked matches.

        The same as a poll with a wider net; matches a poll already queued are
        skipped, and old ones are recorded without waiting for a parse.
        """
        return await _queue_new_matches(job.payload, ctx, BACKFILL_PARAMS)

    def schedules(self) -> list[Schedule]:
        return [Schedule(job_type="poll_matches", cron=POLL_CRON)]

    async def poll_matches(self, job: Job[ScheduledRun], ctx: Context) -> Outcome:
        """Queue a fetch and an analysis for each new tracked match of an account.

        "New" means not yet in `gank_report_polled_matches` for the account;
        the matches queued are added to it in the same transaction as their
        jobs, so each is queued once (docs/adr/0052).
        """
        account = job.payload.account
        if account is None:
            raise PermanentError("poll_matches runs once per linked account")
        return await _queue_new_matches(account, ctx, POLL_PARAMS)

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

        ended = match.played_at + timedelta(seconds=match.duration)
        worth_parsing = self._clock() - ended <= REPLAY_LIFETIME
        if (
            not match.parsed
            and worth_parsing
            and job.attempt <= len(PARSE_RETRY_DELAYS)
        ):
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
        account = job.payload.account
        account_id = _dota_account_id(account)
        fetched = await ctx.db.fetch(
            select(matches.c.mode).where(matches.c.match_id == match_id)
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

        values = {metric.name: metric.compute(match, player) for metric in METRICS}
        writes = (
            _record_player(match_id, account_id, player),
            _record_metrics(match_id, account_id, values),
        )
        ended = match.played_at + timedelta(seconds=match.duration)
        if self._clock() - ended > POST_WINDOW:
            return Outcome(writes=writes)

        mode = Mode(fetched[0].mode)
        averages = await ctx.db.fetch(
            _baselines(account_id, player.hero_id, mode, match.played_at)
        )
        event = MatchAnalyzed(
            dedupe_key=f"gank_report:match_analyzed:{match_id}:{account.id}",
            match_id=match_id,
            discord_user_id=account.discord_user_id,
            hero_id=player.hero_id,
            won=player.win,
            mode=mode.value,
            parsed=match.parsed,
            played_at=match.played_at,
            duration_seconds=match.duration,
            metrics=_results(values, averages),
        )
        return Outcome(writes=writes, events=(event,))


async def _queue_new_matches(
    account: LinkedAccount, ctx: Context, params: Mapping[str, int]
) -> Outcome:
    """Queue a fetch and an analysis for each tracked match in the account's
    list that is not yet polled for it, marking them polled in the same
    outcome, so each is queued once (docs/adr/0052)."""
    account_id = _dota_account_id(account)
    response = await ctx.http.get(player_matches_url(account_id), params=params)
    check_status(response, "player")
    listed = parse(MatchList, response.body).root
    tracked = list(
        dict.fromkeys(
            match.match_id
            for match in listed
            if tracked_mode(match.game_mode, match.lobby_type, match.duration)
        )
    )
    if not tracked:
        return Outcome()

    seen = await ctx.db.fetch(
        select(polled_matches.c.match_id).where(
            polled_matches.c.account_id == account_id,
            polled_matches.c.match_id.in_(tracked),
        )
    )
    already = {row.match_id for row in seen}
    new = [match_id for match_id in tracked if match_id not in already]
    if not new:
        return Outcome()
    return Outcome(
        writes=(_record_polled(account_id, new),),
        jobs=tuple(spec for match_id in new for spec in _jobs_for(match_id, account)),
    )


def _jobs_for(match_id: int, account: LinkedAccount) -> tuple[JobSpec, JobSpec]:
    """Fetch the match (once, whoever polls it) and analyse it for this account.

    Keys carry the link's row ID, never the Dota account ID (docs/adr/0043).
    """
    return (
        JobSpec(
            type="fetch_match",
            payload=FetchMatch(match_id=match_id),
            dedupe_key=f"gank_report:match:{match_id}",
        ),
        JobSpec(
            type="analyze_match",
            payload=AnalyzeMatch(match_id=match_id, account=account),
            dedupe_key=f"gank_report:analyze:{match_id}:{account.id}",
            delay=ANALYZE_DELAY,
        ),
    )


def _record_polled(account_id: int, match_ids: list[int]) -> Insert:
    """Mark matches as queued; one already marked by an overlapping poll is kept."""
    return (
        insert(polled_matches)
        .values(
            [{"account_id": account_id, "match_id": match_id} for match_id in match_ids]
        )
        .on_conflict_do_nothing()
    )


def _record_metrics(
    match_id: int, account_id: int, values: Mapping[str, float | None]
) -> Insert:
    """One row per metric, NULL where the match gives no value (docs/adr/0053)."""
    statement = insert(metrics).values(
        [
            {
                "match_id": match_id,
                "account_id": account_id,
                "metric": name,
                "value": value,
            }
            for name, value in values.items()
        ]
    )
    return statement.on_conflict_do_update(
        index_elements=[metrics.c.match_id, metrics.c.account_id, metrics.c.metric],
        set_={"value": statement.excluded.value},
    )


def _baselines(
    account_id: int, hero_id: int, mode: Mode, before: datetime
) -> Select[Any]:
    """Each metric's average over the player's previous games on this hero and
    kind (at most `BASELINE_GAMES`), and how many of them had a value.

    `AVG` and `COUNT(value)` skip NULLs, so a game without a value neither
    drags the average down nor counts towards it.
    """
    recent = (
        select(player_matches.c.match_id)
        .join(matches, matches.c.match_id == player_matches.c.match_id)
        .where(
            player_matches.c.account_id == account_id,
            player_matches.c.hero_id == hero_id,
            matches.c.mode == mode.value,
            matches.c.played_at < before,
        )
        .order_by(matches.c.played_at.desc())
        .limit(BASELINE_GAMES)
    )
    return (
        select(
            metrics.c.metric,
            func.avg(metrics.c.value).label("average"),
            func.count(metrics.c.value).label("games"),
        )
        .where(metrics.c.account_id == account_id, metrics.c.match_id.in_(recent))
        .group_by(metrics.c.metric)
    )


def _results(
    values: Mapping[str, float | None], averages: Sequence[Row[Any]]
) -> tuple[MetricResult, ...]:
    by_metric = {row.metric: row for row in averages}
    results = []
    for name, value in values.items():
        row = by_metric.get(name)
        games = 0 if row is None else int(row.games)
        average = None if row is None or games == 0 else float(row.average)
        results.append(
            MetricResult(name=name, value=value, baseline=average, baseline_games=games)
        )
    return tuple(results)


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
