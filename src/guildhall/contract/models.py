"""Values exchanged between plug-ins and the platform.

All values are immutable pydantic models that ignore unknown fields, so a newer
producer can add fields without breaking an older consumer.
"""

from datetime import datetime, timedelta
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    PositiveInt,
    SerializeAsAny,
    model_validator,
)
from sqlalchemy.sql.expression import UpdateBase

NonEmptyStr = Annotated[str, Field(min_length=1)]


def _non_negative(value: timedelta) -> timedelta:
    if value < timedelta(0):
        raise ValueError("must not be negative")
    return value


def _positive(value: timedelta) -> timedelta:
    if value <= timedelta(0):
        raise ValueError("must be positive")
    return value


class ContractModel(BaseModel):
    """Base for every contract value: immutable, unknown fields ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class RateLimitPolicy(ContractModel):
    """A plug-in's budget for calls to its upstream API."""

    per_minute: PositiveInt
    per_day: PositiveInt


class Schedule(ContractModel):
    """A recurring trigger for one of the plug-in's job types.

    `cron` is a five-field cron expression evaluated in UTC, for example
    "*/15 * * * *" or "0 18 * * SUN". With `per_account`, the platform creates
    one job per linked account; otherwise one job for the plug-in. The job
    type's payload model must be `ScheduledRun`, which the platform fills in.
    A tick missed while no worker ran fires once when one returns
    (docs/adr/0036, docs/adr/0037).
    """

    job_type: NonEmptyStr
    cron: NonEmptyStr
    per_account: bool = True


class LinkedAccount(ContractModel):
    """Someone's account in the plug-in's source, linked with `/link`."""

    discord_user_id: NonEmptyStr
    external_id: NonEmptyStr
    """The account in the source, e.g. a Dota account ID."""


class ScheduledRun(ContractModel):
    """The payload of every scheduled job (docs/adr/0038).

    `scheduled_for` is the tick, not when the job runs: a late weekly report
    still knows which week it covers. `account` is set for `per_account`
    schedules.
    """

    scheduled_for: datetime
    account: LinkedAccount | None = None


class JobSpec(ContractModel):
    """Work a handler asks the platform to enqueue.

    `dedupe_key` is namespaced by plug-in name, e.g. "gank_report:match:42";
    the platform runs each key to completion at most once.
    """

    type: NonEmptyStr
    payload: SerializeAsAny[BaseModel]
    dedupe_key: NonEmptyStr
    delay: Annotated[timedelta, AfterValidator(_non_negative)] = timedelta(0)


class Job[P: BaseModel](ContractModel):
    """Work as delivered to a handler, with its payload already validated.

    `attempt` starts at 1 and increases each time the job runs again, whether
    after a failure or a retry outcome.
    """

    type: NonEmptyStr
    payload: P
    dedupe_key: NonEmptyStr
    attempt: PositiveInt


class Event(ContractModel):
    """Something that happened, for the platform to deliver.

    Plug-ins subclass it with their own fields. `type` is namespaced by plug-in
    name, e.g. "gank_report.match_analyzed". Within one `schema_version`,
    changes are additive only.
    """

    type: NonEmptyStr
    schema_version: PositiveInt
    dedupe_key: NonEmptyStr


class Archive(ContractModel):
    """One raw upstream response to keep, e.g. `Archive(kind="match", id="42", ...)`.

    `kind` matches `[a-z][a-z0-9_]*` and `id` matches `[A-Za-z0-9][A-Za-z0-9._-]*`
    (at most 200 characters); the platform checks both. `body` is the response
    exactly as received, normally JSON (docs/adr/0031).
    """

    kind: NonEmptyStr
    id: NonEmptyStr
    body: bytes = Field(repr=False)


class Outcome(ContractModel):
    """The result of running a job.

    A finished job may write its plug-in's tables, emit events and ask for
    follow-up jobs; the platform commits all of them together with the job's
    dedupe claim, or none (docs/adr/0028). `writes` are SQLAlchemy insert,
    update or delete statements on the plug-in's own tables, run in order.
    `archives` are raw upstream responses; the platform uploads them before
    that commit, so a committed job's archives always exist (docs/adr/0031).

    A job that is not ready yet (an expected wait, not a failure) sets
    `retry_in` instead and does nothing else, so the platform runs it again
    later with `attempt + 1`.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    writes: tuple[UpdateBase, ...] = ()
    archives: tuple[Archive, ...] = ()
    events: tuple[SerializeAsAny[Event], ...] = ()
    jobs: tuple[JobSpec, ...] = ()
    retry_in: Annotated[timedelta, AfterValidator(_positive)] | None = None

    @model_validator(mode="after")
    def _retry_emits_nothing(self) -> Self:
        if self.retry_in is not None and (
            self.writes or self.archives or self.events or self.jobs
        ):
            raise ValueError(
                "an outcome that retries cannot write, archive, emit events or ask"
                " for jobs"
            )
        return self
