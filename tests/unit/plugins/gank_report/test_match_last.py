"""/match last: the latest analysed match, rebuilt and rendered as a reply."""

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from fakes import NoArchive, NoDatabase, NoHttp, ScriptedDatabase

from guildhall.contract import Context, Database, Invocation, LinkedAccount
from guildhall.plugins.gank_report import (
    LINK_FIRST,
    NOTHING_ANALYSED_YET,
    GankReportPlugin,
    MatchLastOptions,
)
from guildhall.plugins.gank_report.events import MatchAnalyzed, MetricResult
from guildhall.plugins.gank_report.render import render_match

ME = LinkedAccount(id=3, discord_user_id="1234", external_id="1001")
PLAYED = datetime(2026, 11, 10, 21, 0, tzinfo=UTC)
LATEST = SimpleNamespace(
    match_id=5001,
    hero_id=35,
    won=True,
    mode="turbo",
    parsed=False,
    played_at=PLAYED,
    duration_seconds=1300,
)


async def ask(db: Database, account: LinkedAccount | None = ME) -> Any:
    invocation = Invocation[MatchLastOptions](
        discord_user_id="1234", account=account, options=MatchLastOptions()
    )
    context = Context(db=db, archive=NoArchive(), http=NoHttp())
    return await GankReportPlugin().match_last(invocation, context)


async def test_without_a_link_it_says_how_to_link() -> None:
    outcome = await ask(NoDatabase(), account=None)
    assert outcome.reply.content == LINK_FIRST


async def test_without_analysed_matches_it_says_reports_come_later() -> None:
    outcome = await ask(ScriptedDatabase([]))
    assert outcome.reply.content == NOTHING_ANALYSED_YET


async def test_the_latest_match_is_rendered_like_its_post_without_a_mention() -> None:
    stored = [  # in no particular order, as the database may return them
        SimpleNamespace(metric="observer_wards", value=None),
        SimpleNamespace(metric="gpm", value=1599.0),
        SimpleNamespace(metric="last_hits_10", value=None),
    ]
    averages = [SimpleNamespace(metric="gpm", average=1244.0, games=9)]
    database = ScriptedDatabase([LATEST], stored, averages)
    outcome = await ask(database)

    expected = MatchAnalyzed(
        dedupe_key="gank_report:match_analyzed:5001:3",
        match_id=5001,
        discord_user_id="1234",
        hero_id=35,
        won=True,
        mode="turbo",
        parsed=False,
        played_at=PLAYED,
        duration_seconds=1300,
        metrics=(  # in the registry's order
            MetricResult(
                name="last_hits_10", value=None, baseline=None, baseline_games=0
            ),
            MetricResult(name="gpm", value=1599.0, baseline=1244.0, baseline_games=9),
            MetricResult(
                name="observer_wards", value=None, baseline=None, baseline_games=0
            ),
        ),
    )
    assert outcome.reply == render_match(expected, mention=False)
    assert (outcome.reply.content, outcome.reply.mentions) == ("", ())
    assert outcome.writes == outcome.jobs == outcome.events == ()
    baseline_query = database.statements[2].compile()
    assert baseline_query.params["mode_1"] == "turbo"


def test_match_last_is_a_public_command() -> None:
    [command] = GankReportPlugin().commands()
    assert (command.path, command.private) == (("match", "last"), False)
