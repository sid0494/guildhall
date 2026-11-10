"""Linking someone's account in a plug-in's source to their Discord user
(docs/adr/0066).

`/link` belongs to the platform, which owns `linked_accounts`. A plug-in
declares the kind of account it links and how to check one; the platform runs
the check as a job, stores the link, queues the plug-in's follow-up job with
the stored `LinkedAccount`, and replies.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated

from pydantic import Field

from guildhall.contract.models import ContractModel, LinkedAccount, NonEmptyStr
from guildhall.contract.plugin import Context


class LinkRequest(ContractModel):
    """Someone asking to link an account, as `verify` sees it."""

    discord_user_id: NonEmptyStr
    external_id: NonEmptyStr
    """As typed, trimmed."""
    previous: LinkedAccount | None = None
    """The link this one would replace, if any."""


class Accepted(ContractModel):
    """The account is good; `external_id` is how to store it (normalised)."""

    external_id: NonEmptyStr


class Rejected(ContractModel):
    """The account cannot be linked; `reason` is shown to the user, privately."""

    reason: Annotated[str, Field(min_length=1, max_length=1000)]


type LinkVerifier = Callable[[LinkRequest, Context], Awaitable[Accepted | Rejected]]


@dataclass(frozen=True)
class AccountLink:
    """Declares the kind of account a plug-in links: `/link <kind> account:...`.

    `verify` runs on the worker with the plug-in's context. `on_link`, if set,
    names a job type taking a `LinkedAccount`, queued once the link is stored,
    for example to backfill history.
    """

    kind: str
    description: str
    """Shown in Discord for the account option, e.g. where to find the ID."""
    verify: LinkVerifier
    on_link: str | None = None
