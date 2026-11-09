"""Which tick is due, and how scheduled jobs are keyed (ADR 0036, 0038)."""

from datetime import UTC, datetime, timedelta

import pytest

from guildhall.contract import Schedule
from guildhall.platform.scheduler import dedupe_key, latest_tick

QUARTER_PAST = datetime(2026, 11, 9, 23, 15, tzinfo=UTC)  # a Monday


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (QUARTER_PAST, QUARTER_PAST),  # exactly on a tick: that tick
        (QUARTER_PAST + timedelta(seconds=29), QUARTER_PAST),
        (
            QUARTER_PAST - timedelta(microseconds=1),
            QUARTER_PAST - timedelta(minutes=15),
        ),
    ],
)
def test_the_latest_tick_is_at_or_before_now(now: datetime, expected: datetime) -> None:
    assert latest_tick("*/15 * * * *", now) == expected


def test_a_weekly_tick_looks_back_to_the_last_sunday() -> None:
    assert latest_tick("0 18 * * SUN", QUARTER_PAST) == datetime(
        2026, 11, 8, 18, 0, tzinfo=UTC
    )


def test_keys_name_the_plugin_job_type_and_tick() -> None:
    schedule = Schedule(job_type="poll_matches", cron="*/15 * * * *", per_account=False)
    assert (
        dedupe_key("gank_report", schedule, QUARTER_PAST, None)
        == "gank_report:schedule:poll_matches:2026-11-09T23:15Z"
    )


def test_per_account_keys_end_with_the_links_row_id() -> None:
    schedule = Schedule(job_type="poll_matches", cron="*/15 * * * *")
    assert dedupe_key("gank_report", schedule, QUARTER_PAST, 7).endswith(
        "2026-11-09T23:15Z:7"
    )
