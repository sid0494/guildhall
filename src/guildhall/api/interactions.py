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


EPHEMERAL: Final = 1 << 6
"""Message flag: only the user who invoked the interaction sees the reply."""


class Interaction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str = Field(min_length=1)
    """A snowflake, sent as a string."""
    type: int


def pong() -> dict[str, Any]:
    return {"type": ResponseType.PONG}


def private_message(content: str) -> dict[str, Any]:
    """An immediate reply only the invoking user sees."""
    return {
        "type": ResponseType.CHANNEL_MESSAGE_WITH_SOURCE,
        "data": {"content": content, "flags": EPHEMERAL},
    }
