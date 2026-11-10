"""The plug-in contract: everything a plug-in may import from the platform.

Plug-ins import from this package only, never from `guildhall.platform`.
"""

from guildhall.contract.archive import RawArchive
from guildhall.contract.commands import (
    Command,
    CommandHandler,
    CommandOutcome,
    Invocation,
)
from guildhall.contract.errors import PermanentError, RetryableError
from guildhall.contract.http import Http, HttpResponse
from guildhall.contract.links import (
    Accepted,
    AccountLink,
    LinkRequest,
    LinkVerifier,
    Rejected,
)
from guildhall.contract.messages import Embed, EmbedAuthor, EmbedField, Message
from guildhall.contract.models import (
    Archive,
    ContractModel,
    Event,
    Job,
    JobSpec,
    LinkedAccount,
    Outcome,
    RateLimitPolicy,
    Schedule,
    ScheduledRun,
)
from guildhall.contract.plugin import Context, Handler, JobType, SourcePlugin
from guildhall.contract.storage import Database, plugin_metadata

__all__ = [
    "Accepted",
    "AccountLink",
    "Archive",
    "Command",
    "CommandHandler",
    "CommandOutcome",
    "Context",
    "ContractModel",
    "Database",
    "Embed",
    "EmbedAuthor",
    "EmbedField",
    "Event",
    "Handler",
    "Http",
    "HttpResponse",
    "Invocation",
    "Job",
    "JobSpec",
    "JobType",
    "LinkRequest",
    "LinkVerifier",
    "LinkedAccount",
    "Message",
    "Outcome",
    "PermanentError",
    "RateLimitPolicy",
    "RawArchive",
    "Rejected",
    "RetryableError",
    "Schedule",
    "ScheduledRun",
    "SourcePlugin",
    "plugin_metadata",
]
