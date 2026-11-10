"""Turns plug-ins' declared commands into Discord's command structures.

An options model's fields become a command's options: `str`, `int`, `float`
and `bool`, optional when the field has a default or allows None, with the
field's description, and `ge`/`le` or length limits as Discord's own limits.
Required options come first, as Discord requires (docs/adr/0062).
"""

import re
import types
from collections.abc import Iterable, Mapping
from enum import IntEnum
from typing import Any, Final, Union, get_args, get_origin

from annotated_types import Ge, Le, MaxLen, MinLen
from pydantic import BaseModel
from pydantic.fields import FieldInfo

from guildhall.contract import AccountLink, Command, SourcePlugin

OPTION_NAME: Final = re.compile(r"[a-z][a-z0-9_-]{0,31}")
LINK_COMMAND: Final = "link"
ACCOUNT_OPTION: Final = "account"
"""`/link <kind> account:<id>`: the platform's own command (docs/adr/0066)."""
MAX_DESCRIPTION: Final = 100


class CommandType(IntEnum):
    CHAT_INPUT = 1
    """A slash command."""


class OptionType(IntEnum):
    SUB_COMMAND = 1
    STRING = 3
    INTEGER = 4
    BOOLEAN = 5
    NUMBER = 10


_OPTION_TYPES: Final = {
    str: OptionType.STRING,
    int: OptionType.INTEGER,
    float: OptionType.NUMBER,
    bool: OptionType.BOOLEAN,
}


class OptionError(ValueError):
    """An options model has a field Discord cannot express."""


def options_schema(model: type[BaseModel]) -> list[dict[str, Any]]:
    """Discord's option objects for a command's options model."""
    options = [_option(name, field) for name, field in model.model_fields.items()]
    return sorted(options, key=lambda option: not option["required"])


def _option(name: str, field: FieldInfo) -> dict[str, Any]:
    if not OPTION_NAME.fullmatch(name):
        raise OptionError(f"option {name!r} must match {OPTION_NAME.pattern}")
    if not field.description or len(field.description) > MAX_DESCRIPTION:
        raise OptionError(
            f"option {name!r} needs a description of 1 to {MAX_DESCRIPTION} characters"
        )
    kind = _option_type(name, field.annotation)
    option: dict[str, Any] = {
        "type": kind,
        "name": name,
        "description": field.description,
        "required": field.is_required(),
    }
    for limit in field.metadata:
        match limit:
            case Ge(ge=value) if kind in (OptionType.INTEGER, OptionType.NUMBER):
                option["min_value"] = value
            case Le(le=value) if kind in (OptionType.INTEGER, OptionType.NUMBER):
                option["max_value"] = value
            case MinLen(min_length=value) if kind == OptionType.STRING:
                option["min_length"] = value
            case MaxLen(max_length=value) if kind == OptionType.STRING:
                option["max_length"] = value
            case _:
                raise OptionError(f"option {name!r}: Discord cannot express {limit!r}")
    return option


def _option_type(name: str, annotation: Any) -> OptionType:
    if get_origin(annotation) in (Union, types.UnionType):
        inner = [arg for arg in get_args(annotation) if arg is not type(None)]
        if len(inner) == 1:
            annotation = inner[0]
    kind = _OPTION_TYPES.get(annotation)
    if kind is None:
        raise OptionError(
            f"option {name!r} must be str, int, float or bool, optionally with None"
        )
    return kind


def discord_commands(
    commands: Iterable[Command[Any]],
) -> list[dict[str, Any]]:
    """The top-level commands to register, sorted by name.

    A two-level path becomes a subcommand of its group; Discord needs a
    description for the group too, so one is made from its name.
    """
    top: dict[str, dict[str, Any]] = {}
    groups: set[str] = set()
    for command in commands:
        if len(command.path) == 1:
            top[command.path[0]] = _command(command.path[0], command)
        else:
            group, name = command.path
            groups.add(group)
            entry = top.setdefault(
                group,
                {
                    "type": CommandType.CHAT_INPUT,
                    "name": group,
                    "description": f"{group.capitalize()} commands",
                    "options": [],
                },
            )
            entry["options"].append(
                _command(name, command) | {"type": OptionType.SUB_COMMAND}
            )
    for group in groups:
        top[group]["options"].sort(key=lambda sub: sub["name"])
    return [top[name] for name in sorted(top)]


def _command(name: str, command: Command[Any]) -> dict[str, Any]:
    return {
        "type": CommandType.CHAT_INPUT,
        "name": name,
        "description": command.description,
        "options": options_schema(command.options),
    }


def commands_of(plugins: Mapping[str, SourcePlugin]) -> list[Command[Any]]:
    """Every enabled plug-in's commands."""
    return [command for plugin in plugins.values() for command in plugin.commands()]


def link_command(links: Iterable[AccountLink]) -> dict[str, Any] | None:
    """`/link`, with one subcommand per kind of account; None if there are none."""
    subcommands = [
        {
            "type": OptionType.SUB_COMMAND,
            "name": link.kind,
            "description": f"Link your {link.kind} account",
            "options": [
                {
                    "type": OptionType.STRING,
                    "name": ACCOUNT_OPTION,
                    "description": link.description,
                    "required": True,
                }
            ],
        }
        for link in sorted(links, key=lambda link: link.kind)
    ]
    if not subcommands:
        return None
    return {
        "type": CommandType.CHAT_INPUT,
        "name": LINK_COMMAND,
        "description": "Link your game accounts",
        "options": subcommands,
    }


def all_commands(plugins: Mapping[str, SourcePlugin]) -> list[dict[str, Any]]:
    """Everything to register: the plug-ins' commands and `/link`, by name."""
    commands = discord_commands(commands_of(plugins))
    kinds = [
        account_link
        for plugin in plugins.values()
        if (account_link := plugin.account_link()) is not None
    ]
    link = link_command(kinds)
    if link is not None:
        commands.append(link)
    return sorted(commands, key=lambda command: command["name"])
