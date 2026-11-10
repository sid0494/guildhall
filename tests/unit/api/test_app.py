"""The interactions endpoint, driven in memory with signed requests."""

import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from pydantic import ValidationError

from guildhall.api.app import (
    NOT_YET_AVAILABLE,
    UNKNOWN_COMMAND,
    ApiSettings,
    create_app,
)
from guildhall.contract import JobSpec
from guildhall.platform.command_jobs import CommandJob, command_routes
from guildhall.plugins.synthetic import SyntheticPlugin

PRIVATE_KEY = Ed25519PrivateKey.generate()
PUBLIC_KEY_HEX = (
    PRIVATE_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex()
)
TIMESTAMP = "1762790400"
TOKEN = "stand-in-interaction-token"
COMPONENT: dict[str, Any] = {
    "id": "1300000000000000001",
    "type": 3,
    "token": TOKEN,
}
"""A button press: an interaction the api does not handle yet."""


def slash(
    sub: str = "echo",
    text: str = "hi",
    interaction_id: str = "1300000000000000002",
    in_server: bool = True,
) -> dict[str, Any]:
    """`/synthetic <sub> text:<text>` as Discord sends it."""
    caller = (
        {"member": {"user": {"id": "user-1"}}}
        if in_server
        else {"user": {"id": "user-1"}}
    )
    return {
        "id": interaction_id,
        "type": 2,
        "application_id": "app-1",
        "token": TOKEN,
        "data": {
            "name": "synthetic",
            "options": [
                {
                    "name": sub,
                    "type": 1,
                    "options": [{"name": "text", "type": 3, "value": text}],
                }
            ],
        },
    } | caller


class MemoryInteractionLog:
    """`InteractionLog` in memory; can be made unready or failing."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self.queued: list[tuple[str, JobSpec]] = []
        self.ready = True
        self.failing = False

    async def first_time(self, interaction_id: str) -> bool:
        if self.failing:
            raise ConnectionError("database down")
        if interaction_id in self.seen:
            return False
        self.seen.append(interaction_id)
        return True

    async def is_ready(self) -> bool:
        return self.ready

    async def accept_command(
        self, interaction_id: str, plugin: str, spec: JobSpec
    ) -> bool:
        if not await self.first_time(interaction_id):
            return False
        self.queued.append((plugin, spec))
        return True


@pytest.fixture
def interaction_log() -> MemoryInteractionLog:
    return MemoryInteractionLog()


@pytest.fixture
async def client(
    interaction_log: MemoryInteractionLog,
) -> AsyncIterator[httpx.AsyncClient]:
    settings = ApiSettings(discord_public_key=PUBLIC_KEY_HEX)
    routes = command_routes({"synthetic": SyntheticPlugin()})
    transport = httpx.ASGITransport(app=create_app(settings, interaction_log, routes))
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


async def test_an_unhandled_interaction_gets_a_private_not_yet_reply(
    client: httpx.AsyncClient,
) -> None:
    body = encode(COMPONENT)
    response = await post(client, body, signed(body))
    assert response.status_code == 200
    assert response.json() == {
        "type": 4,
        "data": {"content": NOT_YET_AVAILABLE, "flags": 64},
    }


async def test_an_unsigned_request_is_refused(client: httpx.AsyncClient) -> None:
    body = encode(COMPONENT)
    response = await post(client, body, {"content-type": "application/json"})
    assert response.status_code == 401


async def test_a_forged_signature_is_refused(client: httpx.AsyncClient) -> None:
    body = encode(COMPONENT)
    forged = signed(body, signer=Ed25519PrivateKey.generate())
    assert (await post(client, body, forged)).status_code == 401


async def test_a_body_changed_after_signing_is_refused(
    client: httpx.AsyncClient,
) -> None:
    body = encode(COMPONENT)
    headers = signed(body)
    assert (await post(client, body + b" ", headers)).status_code == 401


async def test_a_rejected_request_leaves_no_trace_of_its_content(
    client: httpx.AsyncClient, log_lines: Callable[[], list[dict[str, Any]]]
) -> None:
    body = encode(COMPONENT)
    forged = signed(body, signer=Ed25519PrivateKey.generate())
    await post(client, body, forged)
    logged = str(log_lines())
    assert "interaction rejected" in logged
    for secret in (TOKEN, COMPONENT["id"], forged["x-signature-ed25519"]):
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
        create_app(
            ApiSettings(discord_public_key="not-a-key"), MemoryInteractionLog(), {}
        )


async def test_an_interaction_is_recorded_before_it_is_answered(
    client: httpx.AsyncClient, interaction_log: MemoryInteractionLog
) -> None:
    body = encode(COMPONENT)
    await post(client, body, signed(body))
    assert interaction_log.seen == [COMPONENT["id"]]


async def test_a_retried_interaction_gets_the_same_answer_and_is_recorded_once(
    client: httpx.AsyncClient,
    interaction_log: MemoryInteractionLog,
    log_lines: Callable[[], list[dict[str, Any]]],
) -> None:
    body = encode(COMPONENT)
    first = await post(client, body, signed(body))
    again = await post(client, body, signed(body))
    assert (again.status_code, again.json()) == (first.status_code, first.json())
    assert interaction_log.seen == [COMPONENT["id"]]
    assert "interaction repeated" in [line["event"] for line in log_lines()]


async def test_pings_are_not_recorded(
    client: httpx.AsyncClient, interaction_log: MemoryInteractionLog
) -> None:
    body = encode({"id": "2", "type": 1, "token": "t"})
    await post(client, body, signed(body))
    assert interaction_log.seen == []


async def test_an_interaction_is_refused_while_it_cannot_be_recorded(
    client: httpx.AsyncClient, interaction_log: MemoryInteractionLog
) -> None:
    interaction_log.failing = True
    body = encode(COMPONENT)
    assert (await post(client, body, signed(body))).status_code == 503


async def test_ready_when_the_log_is(
    client: httpx.AsyncClient, interaction_log: MemoryInteractionLog
) -> None:
    assert (await client.get("/readyz")).json() == {"status": "ready"}
    interaction_log.ready = False
    response = await client.get("/readyz")
    assert (response.status_code, response.json()) == (503, {"status": "not ready"})


async def test_a_command_is_queued_as_a_job_and_deferred(
    client: httpx.AsyncClient, interaction_log: MemoryInteractionLog
) -> None:
    body = encode(slash("echo", "hello"))
    response = await post(client, body, signed(body))
    assert (response.status_code, response.json()) == (200, {"type": 5})
    [(plugin, spec)] = interaction_log.queued
    assert plugin == "synthetic"
    assert spec.type == "command:synthetic.echo"
    assert spec.dedupe_key == "synthetic:command:1300000000000000002"
    assert spec.payload == CommandJob(
        path=("synthetic", "echo"),
        discord_user_id="user-1",
        options={"text": "hello"},
        application_id="app-1",
        interaction_token=TOKEN,
        private=False,
    )


async def test_a_private_command_is_deferred_privately(
    client: httpx.AsyncClient, interaction_log: MemoryInteractionLog
) -> None:
    body = encode(slash("whisper"))
    response = await post(client, body, signed(body))
    assert response.json() == {"type": 5, "data": {"flags": 64}}
    [(_, spec)] = interaction_log.queued
    assert isinstance(spec.payload, CommandJob)
    assert spec.payload.private


async def test_a_command_in_a_direct_message_finds_its_caller(
    client: httpx.AsyncClient, interaction_log: MemoryInteractionLog
) -> None:
    body = encode(slash(in_server=False))
    await post(client, body, signed(body))
    [(_, spec)] = interaction_log.queued
    assert isinstance(spec.payload, CommandJob)
    assert spec.payload.discord_user_id == "user-1"


async def test_a_retried_command_is_queued_once_and_deferred_again(
    client: httpx.AsyncClient, interaction_log: MemoryInteractionLog
) -> None:
    body = encode(slash())
    first = await post(client, body, signed(body))
    again = await post(client, body, signed(body))
    assert first.json() == again.json() == {"type": 5}
    assert len(interaction_log.queued) == 1


async def test_a_command_no_plugin_offers_any_more_gets_a_private_reply(
    client: httpx.AsyncClient, interaction_log: MemoryInteractionLog
) -> None:
    body = encode(slash("shout"))
    response = await post(client, body, signed(body))
    assert response.json() == {
        "type": 4,
        "data": {"content": UNKNOWN_COMMAND, "flags": 64},
    }
    assert interaction_log.queued == []


async def test_a_command_is_refused_while_it_cannot_be_queued(
    client: httpx.AsyncClient, interaction_log: MemoryInteractionLog
) -> None:
    interaction_log.failing = True
    body = encode(slash())
    assert (await post(client, body, signed(body))).status_code == 503


@pytest.mark.parametrize(
    "broken",
    [
        {"data": None},
        {"member": None},
        {"token": ""},
    ],
    ids=["no-data", "no-caller", "no-token"],
)
async def test_a_command_without_data_caller_or_token_is_a_bad_request(
    client: httpx.AsyncClient, broken: dict[str, Any]
) -> None:
    body = encode(slash() | broken)
    assert (await post(client, body, signed(body))).status_code == 400


async def test_the_interaction_token_never_reaches_the_log(
    client: httpx.AsyncClient, log_lines: Callable[[], list[dict[str, Any]]]
) -> None:
    body = encode(slash())
    await post(client, body, signed(body))
    assert TOKEN not in str(log_lines())
    assert "command queued" in [line["event"] for line in log_lines()]
