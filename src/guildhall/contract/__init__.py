"""The plug-in contract: everything a plug-in may import from the platform.

Plug-ins import from this package only, never from `guildhall.platform`.
"""

from guildhall.contract.archive import RawArchive
from guildhall.contract.errors import PermanentError, RetryableError
from guildhall.contract.http import Http, HttpResponse
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
    "Archive",
    "Context",
    "ContractModel",
    "Database",
    "Event",
    "Handler",
    "Http",
    "HttpResponse",
    "Job",
    "JobSpec",
    "JobType",
    "LinkedAccount",
    "Outcome",
    "PermanentError",
    "RateLimitPolicy",
    "RawArchive",
    "RetryableError",
    "Schedule",
    "ScheduledRun",
    "SourcePlugin",
    "plugin_metadata",
]
