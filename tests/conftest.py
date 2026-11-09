"""Fixtures shared by every test layer."""

import io
import json
import logging
from collections.abc import Callable, Iterator
from typing import Any

import pytest
import structlog

from guildhall.platform.logs import LogFormat, LogSettings, configure_logging


@pytest.fixture
def restore_logging() -> Iterator[None]:
    """Undo `configure_logging`, which changes process-wide state."""
    root = logging.getLogger()
    saved_handlers, saved_level = root.handlers[:], root.level
    yield
    root.handlers[:] = saved_handlers
    root.setLevel(saved_level)
    structlog.reset_defaults()


@pytest.fixture
def log_lines(restore_logging: None) -> Callable[[], list[dict[str, Any]]]:
    """Configure JSON logging into memory; call the result to get parsed lines."""
    stream = io.StringIO()
    configure_logging(LogSettings(log_level="DEBUG", log_format=LogFormat.JSON), stream)
    return lambda: [json.loads(line) for line in stream.getvalue().splitlines()]
