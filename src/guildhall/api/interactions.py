"""The parts of Discord's interaction objects the api reads and writes.

Discord sends more fields than these; unknown ones are ignored. See Discord's
"Receiving and Responding" documentation for the full objects.
"""

from enum import IntEnum
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field


class InteractionType(IntEnum):
    PING = 1
    APPLICATION_COMMAND = 2
    MESSAGE_COMPONENT = 3
    APPLICATION_COMMAND_AUTOCOMPLETE = 4
    MODAL_SUBMIT = 5


class ResponseType(IntEnum):
    PONG = 1
    CHANNEL_MESSAGE_WITH_SOURCE = 4
    DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE = 5


SUB_COMMAND: Final = 1
"""The option type of a subcommand inside a command's data."""


EPHEMERAL: Final = 1 << 6
"""Message flag: only the user who invoked the interaction sees the reply."""


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")


class User(_Model):
    id: str = Field(min_length=1)


class Member(_Model):
    user: User


class CommandOption(_Model):
    name: str
    type: int
    value: Any = None
    options: list[CommandOption] = []


class CommandData(_Model):
    name: str
    options: list[CommandOption] = []

    def path_and_values(self) -> tuple[tuple[str, ...], dict[str, Any]]:
        """The command's path and its option values by name.

        A subcommand arrives as the command's only option, of type 1, holding
        the options the user filled in.
        """
        if self.options and self.options[0].type == SUB_COMMAND:
            sub = self.options[0]
            return (self.name, sub.name), {o.name: o.value for o in sub.options}
        return (self.name,), {o.name: o.value for o in self.options}


class Interaction(_Model):
    id: str = Field(min_length=1)
    """A snowflake, sent as a string."""
    type: int
    application_id: str = ""
    token: str = Field("", repr=False)
    """Lets the application answer this interaction for 15 minutes."""
    data: CommandData | None = None
    member: Member | None = None
    """Who invoked it, in a server."""
    user: User | None = None
    """Who invoked it, in a direct message."""

    @property
    def caller_id(self) -> str | None:
        if self.member is not None:
            return self.member.user.id
        return self.user.id if self.user is not None else None


def pong() -> dict[str, Any]:
    return {"type": ResponseType.PONG}


def deferred(private: bool) -> dict[str, Any]:
    """ "Guildhall is thinking..."; the reply is edited in later. Whether only
    the caller sees it is fixed here, not when the reply arrives."""
    response: dict[str, Any] = {
        "type": ResponseType.DEFERRED_CHANNEL_MESSAGE_WITH_SOURCE
    }
    if private:
        response["data"] = {"flags": EPHEMERAL}
    return response


def private_message(content: str) -> dict[str, Any]:
    """An immediate reply only the invoking user sees."""
    return {
        "type": ResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
        "data": {"content": content, "flags": EPHEMERAL},
    }
