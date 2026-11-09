"""Fixtures for tests that drive the end-to-end test stack (docs/adr/0027).

Every test under tests/end_to_end is marked `end_to_end` and runs only when
asked for (`make test-e2e`, or `pytest -m end_to_end` with the stack up). The
tests talk to the stack from outside, through its published ports, which
differ from the development stack's, so they can never touch development data.
"""

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from guildhall.platform.database import DatabaseSettings, create_engine
from guildhall.platform.queue import JobQueue, QueueSettings, open_job_queue

POSTGRES_PORT = 15432
MOTO_URL = "http://127.0.0.1:15000"
"""The test stack's published ports, from compose.e2e-test.yaml."""


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    for item in items:
        if Path(item.path).is_relative_to(Path(__file__).parent):
            item.add_marker(pytest.mark.end_to_end)


@pytest.fixture(scope="session")
async def engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(DatabaseSettings(host="127.0.0.1", port=POSTGRES_PORT))
    yield engine
    await engine.dispose()


@pytest.fixture
def queue_settings() -> QueueSettings:
    return QueueSettings(
        aws_endpoint_url=MOTO_URL,
        # moto accepts any credentials; these match compose.e2e-test.yaml.
        aws_access_key_id=SecretStr("moto-e2e-test"),
        aws_secret_access_key=SecretStr("moto-e2e-test"),
    )


@pytest.fixture
async def queue(queue_settings: QueueSettings) -> AsyncIterator[JobQueue]:
    async with open_job_queue(queue_settings) as queue:
        yield queue
