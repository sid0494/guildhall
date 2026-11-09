"""Structured logging for every Guildhall process (docs/adr/0020).

structlog and the standard library share one processor chain, so platform
code (structlog), plug-ins and third-party libraries (standard `logging`) all
write lines in the same format. Job fields bound with `bind_job` are added to
every line written in the same asyncio task while the binding is active,
including lines a plug-in logs inside its handler. Each concurrently running
job is its own task, so jobs never see each other's fields.
"""

import logging
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from enum import StrEnum
from typing import Literal, TextIO

import structlog
from pydantic_settings import BaseSettings, SettingsConfigDict
from structlog.typing import Processor

QUIETED_LOGGERS = ("httpx", "httpcore")
"""Third-party loggers held at WARNING whatever the level: their INFO and DEBUG
lines carry full URLs, query strings included, which is where API keys go.
`Context.http` logs each call itself, without them (docs/adr/0035)."""


class LogFormat(StrEnum):
    JSON = "json"
    CONSOLE = "console"


class LogSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: LogFormat | None = None
    """Unset: readable console lines on a terminal, JSON everywhere else."""


def configure_logging(settings: LogSettings, stream: TextIO = sys.stdout) -> None:
    """Route structlog and standard `logging` to one handler on `stream`.

    Call once, at process start. Replaces any handlers on the root logger.
    """
    log_format = settings.log_format or (
        LogFormat.CONSOLE if stream.isatty() else LogFormat.JSON
    )
    shared: list[Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    renderer: list[Processor] = (
        [structlog.processors.format_exc_info, structlog.processors.JSONRenderer()]
        if log_format is LogFormat.JSON
        else [structlog.dev.ConsoleRenderer()]
    )
    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
    )
    handler = logging.StreamHandler(stream)
    handler.setFormatter(
        structlog.stdlib.ProcessorFormatter(
            foreign_pre_chain=shared,
            processors=[
                structlog.stdlib.ProcessorFormatter.remove_processors_meta,
                *renderer,
            ],
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level)
    for name in QUIETED_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


@contextmanager
def bind_job(
    plugin: str, job_type: str, dedupe_key: str, attempt: int
) -> Iterator[None]:
    """Add the job's fields to every line logged in this task until exit."""
    with structlog.contextvars.bound_contextvars(
        plugin=plugin, job_type=job_type, dedupe_key=dedupe_key, attempt=attempt
    ):
        yield
