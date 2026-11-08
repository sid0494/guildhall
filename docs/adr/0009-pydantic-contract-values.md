# 0009. Pydantic models for contract values

Status: Accepted (2026-11-08)

## Context

Contract values leave the process: jobs are serialised to JSON, travel
through SQS and are read back by a different worker; events are stored as
JSON in Postgres. They need types for mypy, conversion to and from JSON, and
validation on the way back in, so a malformed message (the poison-message
drill) is recognised instead of crashing plug-in code. The spec also requires
consumers to ignore unknown fields, so schemas can grow additively.

## Options considered

- **Standard-library dataclasses:** no dependency, but JSON conversion and
  validation would be written and tested by hand.
- **pydantic v2:** validation and JSON in both directions; ignoring unknown
  fields is one setting; FastAPI, used for the api, is built on it.
- **attrs with cattrs:** flexible, but two libraries to cover what pydantic
  does, used nowhere else in the stack.
- **msgspec:** very fast typed structs with JSON, but a smaller community and a
  second model library alongside FastAPI's pydantic.

## Decision

- Contract values (`RateLimitPolicy`, `Schedule`, `JobSpec`, `Job`, `Event`,
  `Outcome` and plug-in payloads) are pydantic v2 models deriving from
  `ContractModel`: frozen, with unknown fields ignored.
- Declarations that hold code (`JobType`, which carries a handler) are frozen
  standard-library dataclasses, since they are never serialised.
- Fields typed as a base model (`JobSpec.payload`, `Outcome.events`) use
  `SerializeAsAny`, so subclass fields are serialised; without it pydantic
  serialises only the declared base type's fields and payloads become empty.
- mypy runs the pydantic plugin, so assigning to a frozen model and passing
  unknown or mistyped constructor arguments are type errors.

## Consequences

- pydantic is the platform's first runtime dependency and sits at the centre
  of the contract; replacing it later would touch every plug-in.
- Validation runs on every model creation; the cost is irrelevant at this
  project's volume.
- Plug-in payload and event models get validation and JSON for free.
