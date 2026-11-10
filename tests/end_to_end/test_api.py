"""The containerised api, from outside: up, ready, and refusing what Discord
did not sign."""

from collections.abc import AsyncIterator

import httpx
import pytest

API_URL = "http://127.0.0.1:18000"
"""The test stack's api, from compose.e2e-test.yaml."""


@pytest.fixture
async def api() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(base_url=API_URL, timeout=5) as client:
        yield client


async def test_the_api_is_live_and_ready(api: httpx.AsyncClient) -> None:
    assert (await api.get("/healthz")).status_code == 200
    assert (await api.get("/readyz")).json() == {"status": "ready"}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"x-signature-ed25519": "aa" * 64, "x-signature-timestamp": "1762790400"},
    ],
    ids=["unsigned", "forged"],
)
async def test_an_interaction_discord_did_not_sign_is_refused(
    api: httpx.AsyncClient, headers: dict[str, str]
) -> None:
    response = await api.post(
        "/interactions", content=b'{"id": "1", "type": 1}', headers=headers
    )
    assert response.status_code == 401
