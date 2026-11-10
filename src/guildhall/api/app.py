"""The api's web app: Discord's interactions endpoint and health checks.

Every interaction's signature is checked against the raw body before anything
else; nothing about a rejected request is logged beyond the rejection
(docs/adr/0059, 0061).
"""

from typing import Any

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


def create_app(settings: ApiSettings) -> FastAPI:
    """The app, with interactive API docs turned off: the endpoint is public."""
    verifier = SignatureVerifier(settings.discord_public_key)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        """Liveness: the process is up and serving."""
        return {"status": "ok"}

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
        _log.info("interaction received", interaction_type=interaction.type)
        return _json(private_message(NOT_YET_AVAILABLE))

    return app


def _json(content: dict[str, Any], status_code: int = 200) -> JSONResponse:
    return JSONResponse(content, status_code=status_code)
