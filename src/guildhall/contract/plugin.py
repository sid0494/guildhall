"""The interface every plug-in implements.

The base class holds only signatures and trivial defaults, never shared
behaviour, so plug-ins depend on the contract and nothing else.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel

from guildhall.contract.models import Job, Outcome, RateLimitPolicy, Schedule


class Context:
    """Services the platform passes to every handler.

    Deliberately empty for now: the rate-limited HTTP client and storage arrive
    in M2. Handlers take it from the start so adding them changes no signatures.
    """


type Handler[P: BaseModel] = Callable[[Job[P], Context], Awaitable[Outcome]]
"""Handlers share one event loop with every other running job: they must await
I/O, and move CPU-heavy work off the loop with `asyncio.to_thread`."""


@dataclass(frozen=True)
class JobType[P: BaseModel]:
    """Declares one kind of job: its name, payload model and handler."""

    name: str
    payload_model: type[P]
    handler: Handler[P]


class SourcePlugin(ABC):
    """A game or data source hosted by the platform.

    Subclasses set `name` and `rate_limit` as class attributes and implement
    `job_types`. Methods added to the contract later come with defaults, so
    existing plug-ins keep working.
    """

    contract_version: ClassVar[int] = 1

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique plug-in name, e.g. "gank_report"; namespaces keys and events."""

    @property
    @abstractmethod
    def rate_limit(self) -> RateLimitPolicy:
        """Budget for calls to this plug-in's upstream API."""

    @abstractmethod
    def job_types(self) -> list[JobType[Any]]:
        """Every kind of job this plug-in handles."""

    def schedules(self) -> list[Schedule]:
        """Recurring triggers; none by default."""
        return []
