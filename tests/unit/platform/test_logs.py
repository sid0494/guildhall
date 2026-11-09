"""Unit tests for the logging setup: one format, job fields on every line."""

import asyncio
import io
import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
import structlog
from pydantic import ValidationError

from guildhall.platform.logs import (
    LogFormat,
    LogSettings,
    bind_job,
    configure_logging,
)

type LogLines = Callable[[], list[dict[str, Any]]]

JOB = {"plugin": "synthetic", "job_type": "work", "dedupe_key": "synthetic:work:1"}


def test_structlog_lines_are_json_with_level_logger_and_utc_time(
    log_lines: LogLines,
) -> None:
    structlog.get_logger("guildhall.test").info("hello", count=3)
    (line,) = log_lines()
    assert line["event"] == "hello"
    assert line["count"] == 3
    assert line["level"] == "info"
    assert line["logger"] == "guildhall.test"
    assert line["timestamp"].endswith("Z")


def test_standard_logging_lines_share_the_format(log_lines: LogLines) -> None:
    logging.getLogger("some.library").warning("careful %s", "now")
    (line,) = log_lines()
    assert line["event"] == "careful now"
    assert line["level"] == "warning"
    assert line["logger"] == "some.library"


def test_bound_job_fields_are_on_every_line_until_exit(log_lines: LogLines) -> None:
    with bind_job(**JOB, attempt=2):
        structlog.get_logger().info("platform line")
        logging.getLogger("plugin.code").info("plug-in line")
    structlog.get_logger().info("after")
    inside, plugin_line, after = log_lines()
    assert inside.items() >= {**JOB, "attempt": 2}.items()
    assert plugin_line.items() >= {**JOB, "attempt": 2}.items()
    assert "dedupe_key" not in after


async def test_concurrent_jobs_keep_their_own_fields(log_lines: LogLines) -> None:
    async def job(key: str, delay: float) -> None:
        with bind_job("synthetic", "work", key, 1):
            await asyncio.sleep(delay)  # the other job binds and logs meanwhile
            logging.getLogger("plugin.code").info(key)

    await asyncio.gather(job("slow", 0.05), job("fast", 0.01))
    lines = [line for line in log_lines() if line["logger"] == "plugin.code"]
    assert {line["event"]: line["dedupe_key"] for line in lines} == {
        "fast": "fast",
        "slow": "slow",
    }


def test_exceptions_are_rendered_into_the_line(log_lines: LogLines) -> None:
    try:
        raise ValueError("boom")
    except ValueError:
        structlog.get_logger().exception("failed")
    (line,) = log_lines()
    assert "ValueError: boom" in line["exception"]


@pytest.mark.usefixtures("restore_logging")
def test_lines_below_the_level_are_dropped() -> None:
    stream = io.StringIO()
    configure_logging(
        LogSettings(log_level="WARNING", log_format=LogFormat.JSON), stream
    )
    logging.getLogger("x").info("quiet")
    structlog.get_logger().warning("loud")
    (line,) = stream.getvalue().splitlines()
    assert json.loads(line)["event"] == "loud"


class Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.usefixtures("restore_logging")
def test_format_defaults_to_console_on_a_terminal_and_json_elsewhere() -> None:
    terminal, pipe = Terminal(), io.StringIO()
    configure_logging(LogSettings(log_format=None), terminal)
    structlog.get_logger().info("readable")
    configure_logging(LogSettings(log_format=None), pipe)
    structlog.get_logger().info("parsable")
    assert "readable" in terminal.getvalue()
    assert not terminal.getvalue().startswith("{")
    assert json.loads(pipe.getvalue())["event"] == "parsable"


@pytest.mark.parametrize("field", ["GUILDHALL_LOG_LEVEL", "GUILDHALL_LOG_FORMAT"])
def test_log_settings_reject_unknown_values(
    field: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(field, "loud")
    with pytest.raises(ValidationError):
        LogSettings()
