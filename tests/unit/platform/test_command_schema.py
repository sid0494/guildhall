"""Options models and commands as Discord receives them, and registration."""

import json
from typing import Any

import httpx
import pytest
from pydantic import Field, SecretStr

from guildhall.bootstrap import register_commands_main
from guildhall.contract import (
    Command,
    CommandOutcome,
    Context,
    ContractModel,
    Invocation,
    Message,
)
from guildhall.platform.command_schema import (
    OptionError,
    discord_commands,
    options_schema,
)
from guildhall.platform.discord import (
    USER_AGENT,
    DiscordError,
    DiscordSettings,
    register_commands,
)

BOT_TOKEN = "stand-in-bot-token"  # noqa: S105 - a stand-in, never a real token


class TrendOptions(ContractModel):
    hero: str | None = Field(None, max_length=20, description="Hero name")
    weeks: int = Field(4, ge=1, le=52, description="How many weeks back")
    metric: str = Field(min_length=2, description="Which skill")
    ratio: float = Field(description="A number")
    verbose: bool = Field(description="Say more")


class NoOptions(ContractModel):
    pass


async def reply(invocation: Invocation[Any], ctx: Context) -> CommandOutcome:
    return CommandOutcome(reply=Message(content="ok"))


def test_fields_become_options_with_their_types_and_limits() -> None:
    assert options_schema(TrendOptions) == [
        {
            "type": 3,
            "name": "metric",
            "description": "Which skill",
            "required": True,
            "min_length": 2,
        },
        {"type": 10, "name": "ratio", "description": "A number", "required": True},
        {"type": 5, "name": "verbose", "description": "Say more", "required": True},
        {
            "type": 3,
            "name": "hero",
            "description": "Hero name",
            "required": False,
            "max_length": 20,
        },
        {
            "type": 4,
            "name": "weeks",
            "description": "How many weeks back",
            "required": False,
            "min_value": 1,
            "max_value": 52,
        },
    ]


def test_required_options_come_first() -> None:
    required = [option["required"] for option in options_schema(TrendOptions)]
    assert required == sorted(required, reverse=True)


class NoDescription(ContractModel):
    text: str


class UpperName(ContractModel):
    Text: str = Field(description="x")


class ListOption(ContractModel):
    names: list[str] = Field(description="x")


class Pattern(ContractModel):
    code: str = Field(pattern="^[a-z]+$", description="x")


@pytest.mark.parametrize(
    ("model", "expected"),
    [
        (NoDescription, "needs a description"),
        (UpperName, "must match"),
        (ListOption, "must be str, int, float or bool"),
        (Pattern, "Discord cannot express"),
    ],
    ids=["no-description", "upper-case-name", "list", "pattern"],
)
def test_options_discord_cannot_express_are_refused(
    model: type[ContractModel], expected: str
) -> None:
    with pytest.raises(OptionError, match=expected):
        options_schema(model)


def test_commands_become_top_level_commands_and_subcommands() -> None:
    commands = [
        Command(("trend",), "Your skills over time", NoOptions, reply),
        Command(("match", "last"), "Your latest match", NoOptions, reply),
        Command(("match", "best"), "Your best match", NoOptions, reply),
    ]
    assert discord_commands(commands) == [
        {
            "type": 1,
            "name": "match",
            "description": "Match commands",
            "options": [
                {
                    "type": 1,
                    "name": "best",
                    "description": "Your best match",
                    "options": [],
                },
                {
                    "type": 1,
                    "name": "last",
                    "description": "Your latest match",
                    "options": [],
                },
            ],
        },
        {
            "type": 1,
            "name": "trend",
            "description": "Your skills over time",
            "options": [],
        },
    ]


def settings() -> DiscordSettings:
    return DiscordSettings(
        discord_application_id="app-1",
        discord_guild_id="guild-1",
        discord_bot_token=SecretStr(BOT_TOKEN),
        _env_file=None,
    )


async def test_registration_overwrites_the_servers_commands() -> None:
    seen: list[httpx.Request] = []

    def discord(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=json.loads(request.content))

    client = httpx.AsyncClient(
        base_url="https://discord.com/api/v10",
        transport=httpx.MockTransport(discord),
        headers={"Authorization": f"Bot {BOT_TOKEN}", "User-Agent": USER_AGENT},
    )
    commands = [{"type": 1, "name": "trend", "description": "x", "options": []}]
    async with client:
        assert await register_commands(client, settings(), commands) == 1
    [request] = seen
    assert request.method == "PUT"
    assert request.url.path == "/api/v10/applications/app-1/guilds/guild-1/commands"
    assert request.headers["authorization"] == f"Bot {BOT_TOKEN}"
    assert request.headers["user-agent"].startswith("DiscordBot (")
    assert json.loads(request.content) == commands


async def test_a_refused_registration_names_the_reason_not_the_token() -> None:
    def discord(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"message": "Invalid Form Body", "code": 50035})

    client = httpx.AsyncClient(
        base_url="https://discord.com/api/v10", transport=httpx.MockTransport(discord)
    )
    async with client:
        with pytest.raises(DiscordError) as raised:
            await register_commands(client, settings(), [])
    assert (
        str(raised.value) == "registering commands failed with 400: Invalid Form Body"
    )
    assert BOT_TOKEN not in str(raised.value)


def test_the_dry_run_prints_the_enabled_plugins_commands(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    restore_logging: None,
) -> None:
    monkeypatch.setenv("GUILDHALL_PLUGINS", "synthetic")
    monkeypatch.setattr("sys.argv", ["guildhall-register-commands", "--dry-run"])
    register_commands_main()
    printed = json.loads(capsys.readouterr().out)
    assert [command["name"] for command in printed] == ["synthetic"]
    assert [sub["name"] for sub in printed[0]["options"]] == ["echo", "whisper"]
