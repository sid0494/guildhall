"""Slash commands a plug-in offers (docs/adr/0062).

Every command runs on the worker as a job: the api records the interaction,
queues it and tells Discord a reply is coming, and the platform edits the
reply in once the handler's outcome commits (docs/adr/0064). A handler sees
who asked and their linked account for the plug-in, never Discord itself.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict, SerializeAsAny
from sqlalchemy.sql.expression import UpdateBase

from guildhall.contract.messages import Message
from guildhall.contract.models import (
    ContractModel,
    Event,
    JobSpec,
    LinkedAccount,
    NonEmptyStr,
)
from guildhall.contract.plugin import Context


class Invocation[O: BaseModel](ContractModel):
    """A command as delivered to its handler, with its options validated."""

    discord_user_id: NonEmptyStr
    account: LinkedAccount | None = None
    """The caller's linked account for this plug-in, if they linked one."""
    options: O


class CommandOutcome(ContractModel):
    """The result of a command: the reply, and what else it changes.

    Like a job's `Outcome`, the writes, jobs and events commit together with
    the reply being queued, or none of them does. There is no `retry_in`: the
    reply must arrive within Discord's 15 minutes.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    reply: Message
    writes: tuple[UpdateBase, ...] = ()
    jobs: tuple[JobSpec, ...] = ()
    events: tuple[SerializeAsAny[Event], ...] = ()


type CommandHandler[O: BaseModel] = Callable[
    [Invocation[O], Context], Awaitable[CommandOutcome]
]


@dataclass(frozen=True)
class Command[O: BaseModel]:
    """Declares one slash command.

    `path` is the command's name: `("match", "last")` is `/match last`, at
    most two levels. `options` is a model whose fields become the command's
    options; each field needs a `description`. A `private` command's replies
    are seen only by the user who ran it: Discord fixes that when the api
    first answers, before the handler runs (docs/adr/0062).
    """

    path: tuple[str, ...]
    description: str
    options: type[O]
    handler: CommandHandler[O]
    private: bool = False
