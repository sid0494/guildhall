"""Calls to Discord's REST API, made by the platform, never by plug-ins.

Registering commands needs the bot token, a secret the api never holds
(docs/adr/0065). Discord asks every client to identify itself in the
User-Agent as `DiscordBot (<url>, <version>)`.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
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
    headers = {
        "Authorization": f"Bot {settings.discord_bot_token.get_secret_value()}",
        "User-Agent": USER_AGENT,
    }
    async with httpx.AsyncClient(
        base_url=API_URL, headers=headers, timeout=10
    ) as client:
        yield client


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
        "title": embed.title,
        "description": embed.description,
        "color": embed.colour,
        "fields": [field.model_dump() for field in embed.fields] or None,
        "timestamp": embed.timestamp.isoformat() if embed.timestamp else None,
    }
    return {key: value for key, value in fields.items() if value is not None}
