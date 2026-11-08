"""The JSON body of every job message on the queue.

The envelope is platform-internal: plug-ins never see it. Its payload stays raw
JSON until the worker validates it against the job type's payload model, so a
malformed payload can be dead-lettered with a reason instead of crashing.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PositiveInt

from guildhall.contract import JobSpec


class JobEnvelope(BaseModel):
    """Version 1 of the job message format."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    v: Literal[1] = 1
    plugin: str = Field(min_length=1)
    type: str = Field(min_length=1)
    dedupe_key: str = Field(min_length=1)
    attempt: PositiveInt = 1
    """Runs before this message; the handler sees it plus SQS redeliveries."""
    payload: dict[str, Any]

    @classmethod
    def from_spec(cls, plugin: str, spec: JobSpec, attempt: int = 1) -> JobEnvelope:
        return cls(
            plugin=plugin,
            type=spec.type,
            dedupe_key=spec.dedupe_key,
            attempt=attempt,
            payload=spec.payload.model_dump(mode="json"),
        )
