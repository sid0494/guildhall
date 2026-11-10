"""The interactions endpoint, driven in memory with signed requests."""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import ValidationError

from guildhall.api.app import NOT_YET_AVAILABLE, ApiSettings, create_app

PRIVATE_KEY = Ed25519PrivateKey.generate()
PUBLIC_KEY_HEX = (
    PRIVATE_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
)
TIMESTAMP = "1762790400"
COMMAND: dict[str, Any] = {
    "id": "1300000000000000001",
    "type": 2,
    "token": "secret-token",
}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(ApiSettings(discord_public_key=PUBLIC_KEY_HEX))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://api") as c:
        yield c


def signed(body: bytes, signer: Ed25519PrivateKey = PRIVATE_KEY) -> dict[str, str]:
    return {
        "x-signature-ed25519": signer.sign(TIMESTAMP.encode() + body).hex(),
        "x-signature-timestamp": TIMESTAMP,
        "content-type": "application/json",
    }


async def post(
    client: httpx.AsyncClient, body: bytes, headers: dict[str, str]
) -> httpx.Response:
    return await client.post("/interactions", content=body, headers=headers)


def encode(interaction: dict[str, Any]) -> bytes:
    return json.dumps(interaction).encode()


async def test_a_ping_gets_a_pong(client: httpx.AsyncClient) -> None:
    body = encode({"id": "1", "type": 1, "token": "t"})
    response = await post(client, body, signed(body))
    assert (response.status_code, response.json()) == (200, {"type": 1})


async def test_a_command_gets_a_private_not_yet_available_reply(
    client: httpx.AsyncClient,
) -> None:
    body = encode(COMMAND)
    response = await post(client, body, signed(body))
    assert response.status_code == 200
    assert response.json() == {
        "type": 4,
        "data": {"content": NOT_YET_AVAILABLE, "flags": 64},
    }


async def test_an_unsigned_request_is_refused(client: httpx.AsyncClient) -> None:
    body = encode(COMMAND)
    response = await post(client, body, {"content-type": "application/json"})
    assert response.status_code == 401


async def test_a_forged_signature_is_refused(client: httpx.AsyncClient) -> None:
    body = encode(COMMAND)
    forged = signed(body, signer=Ed25519PrivateKey.generate())
    assert (await post(client, body, forged)).status_code == 401


async def test_a_body_changed_after_signing_is_refused(
    client: httpx.AsyncClient,
) -> None:
    body = encode(COMMAND)
    headers = signed(body)
    assert (await post(client, body + b" ", headers)).status_code == 401


async def test_a_rejected_request_leaves_no_trace_of_its_content(
    client: httpx.AsyncClient, log_lines: Callable[[], list[dict[str, Any]]]
) -> None:
    body = encode(COMMAND)
    forged = signed(body, signer=Ed25519PrivateKey.generate())
    await post(client, body, forged)
    logged = str(log_lines())
    assert "interaction rejected" in logged
    for secret in ("secret-token", COMMAND["id"], forged["x-signature-ed25519"]):
        assert secret not in logged


@pytest.mark.parametrize(
    "body",
    [b"not json", b'{"type": 2}', b'{"id": "", "type": 2}', b"[]"],
    ids=["not-json", "no-id", "empty-id", "array"],
)
async def test_a_signed_but_malformed_body_is_a_bad_request(
    client: httpx.AsyncClient, body: bytes
) -> None:
    assert (await post(client, body, signed(body))).status_code == 400


async def test_health_answers_without_a_signature(client: httpx.AsyncClient) -> None:
    response = await client.get("/healthz")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


@pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
async def test_api_docs_are_not_published(client: httpx.AsyncClient, path: str) -> None:
    assert (await client.get(path)).status_code == 404


def test_the_public_key_is_required() -> None:
    with pytest.raises(ValidationError, match="discord_public_key"):
        ApiSettings(_env_file=None)


def test_a_wrong_public_key_stops_the_app_at_startup() -> None:
    with pytest.raises(ValueError):  # noqa: PT011 - raised by the key parser
        create_app(ApiSettings(discord_public_key="not-a-key"))
