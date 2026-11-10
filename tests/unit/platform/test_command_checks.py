"""Declaration checks for slash commands, and their owners across plug-ins."""

from typing import Any

import pytest
from pydantic import BaseModel

from guildhall.contract import (
    Command,
    CommandOutcome,
    Context,
    ContractModel,
    Invocation,
    JobType,
    Message,
    RateLimitPolicy,
    SourcePlugin,
)
from guildhall.platform.checks import check_declarations
from guildhall.platform.registry import RegistryError, build_registry


class NoOptions(ContractModel):
    pass


class PlainOptions(BaseModel):
    """Not a ContractModel."""


async def reply(invocation: Invocation[Any], ctx: Context) -> CommandOutcome:
    return CommandOutcome(reply=Message(content="ok"))


def command(*path: str, description: str = "Does a thing") -> Command[Any]:
    return Command(path, description, NoOptions, reply)


class Commanding(SourcePlugin):
    name = "commanding"
    rate_limit = RateLimitPolicy(per_minute=1, per_day=1)

    def __init__(self, commands: list[Command[Any]] | None = None) -> None:
        self._commands = commands or []

    def job_types(self) -> list[JobType[Any]]:
        return []

    def commands(self) -> list[Command[Any]]:
        return self._commands


def problems(*commands: Command[Any]) -> list[str]:
    return check_declarations(Commanding(list(commands)))


def test_well_formed_commands_pass() -> None:
    assert (
        problems(command("match", "last"), command("match", "best"), command("trend"))
        == []
    )


def test_a_plugin_without_commands_passes() -> None:
    assert check_declarations(Commanding()) == []


@pytest.mark.parametrize(
    ("bad", "expected"),
    [
        (command(), "must have one or two names"),
        (command("a", "b", "c"), "must have one or two names"),
        (command("Match"), "must match"),
        (command("match last"), "must match"),
        (command("x" * 33), "must match"),
        (command("link", "dota"), "/link is the platform's"),
        (command("guildhall"), "/guildhall is the platform's"),
        (command("trend", description=""), "needs a description"),
        (command("trend", description="x" * 101), "needs a description"),
        (
            Command(("trend",), "Trend", PlainOptions, reply),
            "derive from ContractModel",
        ),
    ],
    ids=[
        "no-name",
        "three-levels",
        "upper-case",
        "space",
        "too-long",
        "link-reserved",
        "guildhall-reserved",
        "no-description",
        "long-description",
        "plain-options",
    ],
)
def test_malformed_commands_are_reported(bad: Command[Any], expected: str) -> None:
    assert any(expected in problem for problem in problems(bad))


def test_a_command_declared_twice_is_reported() -> None:
    assert any(
        "declared more than once" in p
        for p in problems(command("trend"), command("trend"))
    )


def test_a_name_cannot_be_both_a_command_and_a_group() -> None:
    found = problems(command("match"), command("match", "last"))
    assert any("both a command and a group" in p for p in found)


class MatchLast(Commanding):
    def commands(self) -> list[Command[Any]]:
        return [command("match", "last")]


class MatchBest(Commanding):
    name = "other"

    def commands(self) -> list[Command[Any]]:
        return [command("match", "best")]


def test_two_plugins_cannot_share_a_top_level_name() -> None:
    catalogue: dict[str, type[SourcePlugin]] = {
        "commanding": MatchLast,
        "other": MatchBest,
    }
    with pytest.raises(RegistryError) as raised:
        build_registry(catalogue, ["commanding", "other"])
    assert any(
        "/match is declared by more than one plug-in: commanding, other" in p
        for p in raised.value.problems
    )
