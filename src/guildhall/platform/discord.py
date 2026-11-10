"""Calls to Discord's REST API, made by the platform, never by plug-ins.

Registering commands needs the bot token, a secret the api never holds
(docs/adr/0065). Discord asks every client to identify itself in the
User-Agent as `DiscordBot (<url>, <version>)`.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from enum import Enum, auto
from typing import Any, Final

import httpx
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from guildhall.contract import Embed, Message

API_URL: Final = "https://discord.com/api/v10"
USER_AGENT: Final = "DiscordBot (https://github.com/sid0494/guildhall, 0.1)"


class DiscordSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="GUILDHALL_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
    )

    discord_application_id: str
    """From the developer portal's General Information page; not a secret."""
    discord_guild_id: str
    """The server whose commands are registered: personal, so only in .env."""
    discord_bot_token: SecretStr
    """From the portal's Bot page. A secret."""


class DiscordError(Exception):
    """Discord refused a request; the message names the status, never the token."""


@asynccontextmanager
async def open_discord(settings: DiscordSettings) -> AsyncIterator[httpx.AsyncClient]:
    async with open_bot(settings.discord_bot_token) as client:
        yield client


@asynccontextmanager
async def open_bot(token: SecretStr) -> AsyncIterator[httpx.AsyncClient]:
    """A client that acts as the bot (for posts and registration)."""
    headers = {
        "Authorization": f"Bot {token.get_secret_value()}",
        "User-Agent": USER_AGENT,
    }
    async with httpx.AsyncClient(
        base_url=API_URL, headers=headers, timeout=10
    ) as client:
        yield client


class Delivery(Enum):
    """What to do after asking Discord to send something."""

    SENT = auto()
    GIVE_UP = auto()
    """Discord refused it for good (4xx other than 429)."""
    TRY_AGAIN = auto()
    """Discord or the network failed (5xx, timeouts): worth another try."""
    RATE_LIMITED = auto()
    """Wait as long as Discord says, then go on."""


def classify(response: httpx.Response) -> tuple[Delivery, Any]:
    """The delivery outcome, and its detail: the status, or the wait for 429."""
    if response.is_success:
        return Delivery.SENT, response.status_code
    if response.status_code == httpx.codes.TOO_MANY_REQUESTS:
        return Delivery.RATE_LIMITED, retry_after(response)
    if response.status_code >= 500:
        return Delivery.TRY_AGAIN, response.status_code
    return Delivery.GIVE_UP, response.status_code


def retry_after(response: httpx.Response) -> float:
    """Discord's wait, from the body or the header; a second if neither."""
    try:
        return float(response.json()["retry_after"])
    except ValueError, KeyError, TypeError:
        pass
    try:
        return float(response.headers.get("retry-after", 1))
    except ValueError:
        return 1.0


async def register_commands(
    client: httpx.AsyncClient,
    settings: DiscordSettings,
    commands: Sequence[dict[str, Any]],
) -> int:
    """Make the server's commands exactly `commands` (a bulk overwrite).

    Commands missing from the list are deleted; returns how many Discord now
    has.
    """
    path = (
        f"/applications/{settings.discord_application_id}"
        f"/guilds/{settings.discord_guild_id}/commands"
    )
    response = await client.put(path, json=list(commands))
    if response.is_error:
        raise DiscordError(
            f"registering commands failed with {response.status_code}: "
            f"{_reason(response)}"
        )
    return len(response.json())


def _reason(response: httpx.Response) -> str:
    """Discord's error message, which describes the request, not the token."""
    try:
        return str(response.json().get("message", ""))
    except ValueError:
        return ""


def discord_message(message: Message) -> dict[str, Any]:
    """Discord's JSON for a message (docs/adr/0068).

    Only the users in `mentions` can be notified: never @everyone, roles or
    anyone else named in user-supplied text.
    """
    body: dict[str, Any] = {
        "allowed_mentions": {"parse": [], "users": list(message.mentions)}
    }
    if message.content:
        body["content"] = message.content
    if message.embeds:
        body["embeds"] = [_embed(embed) for embed in message.embeds]
    return body


def _embed(embed: Embed) -> dict[str, Any]:
    fields = {
        "author": embed.author.model_dump(exclude_none=True) if embed.author else None,
        "title": embed.title,
        "description": embed.description,
        "color": embed.colour,
        "fields": [field.model_dump() for field in embed.fields] or None,
        "timestamp": embed.timestamp.isoformat() if embed.timestamp else None,
    }
    return {key: value for key, value in fields.items() if value is not None}
