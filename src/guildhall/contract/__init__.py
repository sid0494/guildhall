"""The plug-in contract: everything a plug-in may import from the platform.

Plug-ins import from this package only, never from `guildhall.platform`.
"""

from guildhall.contract.errors import PermanentError, RetryableError
from guildhall.contract.models import (
    ContractModel,
    Event,
    Job,
    JobSpec,
    Outcome,
    RateLimitPolicy,
    Schedule,
)
from guildhall.contract.plugin import Context, Handler, JobType, SourcePlugin
from guildhall.contract.storage import Database, plugin_metadata

__all__ = [
    "Context",
    "ContractModel",
    "Database",
    "Event",
    "Handler",
    "Job",
    "JobSpec",
    "JobType",
    "Outcome",
    "PermanentError",
    "RateLimitPolicy",
    "RetryableError",
    "Schedule",
    "SourcePlugin",
    "plugin_metadata",
]
