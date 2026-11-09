"""The interface every plug-in implements.

The base class holds only signatures and trivial defaults, never shared
behaviour, so plug-ins depend on the contract and nothing else.
"""

from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, ClassVar

from pydantic import BaseModel
from sqlalchemy import MetaData

from guildhall.contract.archive import RawArchive
from guildhall.contract.http import Http
from guildhall.contract.models import Job, Outcome, RateLimitPolicy, Schedule
from guildhall.contract.storage import Database, plugin_metadata


@dataclass(frozen=True)
class Context:
    """Services the platform passes to every handler.

    Handlers take the context from the start, so services added to it later
    change no signatures.
    """

    db: Database
    """Reads of the plug-in's own tables; writes go in `Outcome.writes`."""
    archive: RawArchive
    """Reads of the plug-in's own archived responses; uploads go in
    `Outcome.archives`."""
    http: Http
    """Rate-limited calls to the plug-in's upstream API."""


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

    Subclasses set `name` and `rate_limit` (and `tables`, if they store data)
    as class attributes and implement `job_types`. Members added to the
    contract later come with defaults, so existing plug-ins keep working.
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

    @property
    def tables(self) -> MetaData:
        """The tables this plug-in owns, in a `plugin_metadata()`; none by default.

        Every table is named `<plug-in name>_...`. Subclasses set it as a class
        attribute, like `name`.
        """
        return plugin_metadata()
