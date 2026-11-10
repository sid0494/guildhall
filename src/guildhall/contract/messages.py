"""Discord messages as plug-ins describe them: command replies and posts
(docs/adr/0068).

The platform turns them into Discord's format. Limits are Discord's own, so a
message that would be refused fails validation where it is built.
"""

from datetime import datetime
from typing import Annotated, Self

from pydantic import Field, model_validator

from guildhall.contract.models import ContractModel, NonEmptyStr

MESSAGE_MAX_LENGTH = 2000
"""Discord's limit for a message's text."""


class EmbedField(ContractModel):
    name: Annotated[str, Field(min_length=1, max_length=256)]
    value: Annotated[str, Field(min_length=1, max_length=1024)]
    inline: bool = False
    """Discord lays inline fields out side by side."""


class Embed(ContractModel):
    """A card: title, text, a coloured edge, fields and a timestamp.

    Discord shows `timestamp` in each viewer's own time zone.
    """

    title: Annotated[str, Field(max_length=256)] | None = None
    description: Annotated[str, Field(max_length=4096)] | None = None
    colour: Annotated[int, Field(ge=0, le=0xFFFFFF)] | None = None
    """An RGB colour, e.g. 0x2ECC71."""
    fields: Annotated[tuple[EmbedField, ...], Field(max_length=25)] = ()
    timestamp: datetime | None = None


class Message(ContractModel):
    """A command's reply or a post: text, embeds or both.

    `mentions` lists the Discord users the message may notify; `<@id>` in the
    content notifies only those. Who sees a reply is the command's choice
    (`Command.private`), not the message's.
    """

    content: Annotated[str, Field(max_length=MESSAGE_MAX_LENGTH)] = ""
    embeds: Annotated[tuple[Embed, ...], Field(max_length=10)] = ()
    mentions: tuple[NonEmptyStr, ...] = ()

    @model_validator(mode="after")
    def _not_empty(self) -> Self:
        if not self.content and not self.embeds:
            raise ValueError("a message needs content or an embed")
        return self
