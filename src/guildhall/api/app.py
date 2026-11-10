"""The api's web app: Discord's interactions endpoint and health checks.

Every interaction's signature is checked against the raw body before anything
else; nothing about a rejected request is logged beyond the rejection
(docs/adr/0059, 0061).
"""

from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

import structlog
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from pydantic_settings import BaseSettings, SettingsConfigDict

from guildhall.api.interactions import (
    Interaction,
    InteractionType,
    pong,
    private_message,
)
from guildhall.api.signature import SignatureVerifier

NOT_YET_AVAILABLE = "Guildhall doesn't know that command yet."

_log = structlog.get_logger(__name__)


class InteractionLog(Protocol):
    """Which interactions have been seen; `PostgresInteractionLog` provides it."""

    async def first_time(self, interaction_id: str) -> bool:
        """Record the ID; False if it was already recorded."""
        ...

    async def is_ready(self) -> bool:
        """Whether the log can be written to."""
        ...


type Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]


class ApiSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    discord_public_key: str
    """The Discord application's public key, as hex (not a secret)."""
    api_host: str = "127.0.0.1"
    """Where the api listens; the container sets 0.0.0.0."""
    api_port: int = 8000


def create_app(
    settings: ApiSettings,
    interaction_log: InteractionLog,
    lifespan: Lifespan | None = None,
) -> FastAPI:
    """The app, with interactive API docs turned off: the endpoint is public.

    `lifespan` runs around the app's life, for example to close the database
    engine when the server stops.
    """
    verifier = SignatureVerifier(settings.discord_public_key)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness: the process is up and serving."""
        return {"status": "ok"}

    @app.get("/readyz")
    async def readyz() -> JSONResponse:
        """Readiness: Postgres answers, so interactions can be recorded."""
        if await interaction_log.is_ready():
            return _json({"status": "ready"})
        return _json({"status": "not ready"}, status_code=503)

    @app.post("/interactions")
    async def interactions(request: Request) -> JSONResponse:
        body = await request.body()
        if not verifier.is_valid(
            request.headers.get("x-signature-ed25519"),
            request.headers.get("x-signature-timestamp"),
            body,
        ):
            _log.warning("interaction rejected", reason="invalid signature")
            return _json({"error": "invalid request signature"}, status_code=401)
        try:
            interaction = Interaction.model_validate_json(body)
        except ValidationError:
            _log.warning("interaction rejected", reason="malformed body")
            return _json({"error": "malformed interaction"}, status_code=400)

        if interaction.type == InteractionType.PING:
            return _json(pong())
        try:
            first_time = await interaction_log.first_time(interaction.id)
        except Exception:
            _log.warning("interaction not recorded", exc_info=True)
            return _json({"error": "temporarily unavailable"}, status_code=503)
        if not first_time:
            # A retry: answer again, but do nothing again.
            _log.info("interaction repeated", interaction_type=interaction.type)
        else:
            _log.info("interaction received", interaction_type=interaction.type)
        return _json(private_message(NOT_YET_AVAILABLE))

    return app


def _json(content: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status_code)
