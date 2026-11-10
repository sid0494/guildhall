"""Slash commands run as jobs on the worker (docs/adr/0063).

The api turns an invocation into a command job, queued through the outbox in
the same transaction that records the interaction (docs/adr/0061). The job's
type is `command:<path>`; the colon cannot appear in a plug-in's own job types,
so the two never collide.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final

from pydantic import Field

from guildhall.contract import (
    AccountLink,
    Command,
    ContractModel,
    JobSpec,
    SourcePlugin,
)

COMMAND_TYPE_PREFIX: Final = "command:"


class CommandJob(ContractModel):
    """A command invocation, waiting for the worker.

    `options` stay raw until the worker validates them against the command's
    options model. `interaction_token` lets the platform edit the reply for 15
    minutes; it is never logged.
    """

    path: tuple[str, ...]
    discord_user_id: str = Field(min_length=1)
    options: dict[str, Any]
    application_id: str = Field(min_length=1)
    interaction_token: str = Field(min_length=1, repr=False)
    private: bool


def command_type(path: tuple[str, ...]) -> str:
    return COMMAND_TYPE_PREFIX + ".".join(path)


def is_command(job_type: str) -> bool:
    return job_type.startswith(COMMAND_TYPE_PREFIX)


def command_spec(plugin: str, interaction_id: str, job: CommandJob) -> JobSpec:
    """The job for one invocation, keyed by the interaction, never the user."""
    return JobSpec(
        type=command_type(job.path),
        payload=job,
        dedupe_key=f"{plugin}:command:{interaction_id}",
    )


LINK_COMMAND: Final = "link"


@dataclass(frozen=True)
class CommandRoute:
    """Which plug-in a command goes to, and whether its replies are private.

    A plug-in's own command carries `command`; `/link <kind>` carries the
    plug-in's `link` instead, and is always private.
    """

    plugin: str
    private: bool
    command: Command[Any] | None = None
    link: AccountLink | None = None


def command_routes(
    plugins: Mapping[str, SourcePlugin],
) -> Mapping[tuple[str, ...], CommandRoute]:
    """Every enabled plug-in's commands and `/link` kinds, by path."""
    routes = {
        command.path: CommandRoute(name, command.private, command=command)
        for name, plugin in plugins.items()
        for command in plugin.commands()
    }
    for name, plugin in plugins.items():
        link = plugin.account_link()
        if link is not None:
            routes[(LINK_COMMAND, link.kind)] = CommandRoute(name, True, link=link)
    return MappingProxyType(routes)


def commands_by_path(plugin: SourcePlugin) -> dict[tuple[str, ...], Command[Any]]:
    return {command.path: command for command in plugin.commands()}
