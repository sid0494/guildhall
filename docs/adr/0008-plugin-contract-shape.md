# 0008. Plug-in contract: an abstract base class with declared job types

Status: Accepted (2026-11-08)

## Context

Every plug-in is written against one contract, and the platform (worker,
scheduler, outbox) calls plug-ins only through it. The contract must survive
growth in two directions: new plug-ins written months apart, and new
capabilities added to the contract itself (an "account linked" hook is already
foreseeable for backfills). The first sketch in the design spec was a
`Protocol` with `discover()` and a single `process()` per plug-in.

Three questions shape the contract: how a plug-in satisfies the interface,
where the contract lives, and how a plug-in's different kinds of jobs reach
their code.

## Options considered

Interface:

- **Pure `Protocol`:** no coupling at all, but mismatches are reported where
  plug-ins are used rather than where they are defined, and adding a member
  to the contract breaks every plug-in, because defaults are not inherited
  without subclassing.
- **`Protocol` subclassed explicitly:** errors at the plug-in's class and
  inherited defaults, while still allowing structural conformance.
- **`abc.ABC`:** errors at the plug-in's class (from mypy, and as a
  `TypeError` at instantiation), inherited defaults for new members, and
  native `isinstance`; the familiar idiom. Only subclasses qualify, and a
  base class invites shared behaviour.

The last two differ little in practice; the familiar idiom decided it.

Location:

- **Inside `guildhall.platform`:** simpler tree, but plug-ins would import
  from the platform package.
- **Its own `guildhall.contract` package:** dependencies point one way,
  plug-ins → contract ← platform, so platform refactors cannot break plug-ins
  and plug-ins cannot reach platform internals.

Job routing:

- **`discover()` plus one `process()` per plug-in:** a small contract, but the
  platform knows nothing about job types. Each plug-in validates its own
  payloads, misspelled job types fail late, and only `discover` can create
  work, so parse retries and backfill fan-out need extra mechanisms.
- **Declared job types:** each plug-in lists its job types (name, payload
  model, handler); every handler returns an `Outcome` of events, follow-up
  jobs, or a retry delay. The platform validates and routes uniformly.

## Decision

- `SourcePlugin` is an abstract base class. Required members (`name`,
  `rate_limit`, `job_types()`) are abstract; members added later come with
  defaults (`schedules()` already returns `[]`). The base holds only
  signatures and trivial defaults, never shared behaviour.
- The contract lives in `guildhall.contract`. Plug-ins import only from it,
  never from `guildhall.platform`.
- A plug-in declares `JobType(name, payload_model, handler)` entries. Every
  handler has the shape `(Job[P], Context) -> Outcome`. `discover` is no longer
  special: polling is a scheduled job type whose outcome is follow-up jobs.
- An expected wait (a match not parsed yet) is an outcome,
  `Outcome(retry_in=...)`, which reruns the job with `attempt + 1` without
  counting as a failure. Failures are typed exceptions: `RetryableError`
  (backoff, then dead-letter after repeated failures) and `PermanentError`
  (dead-letter at once).
- `Schedule` names a job type, a UTC cron expression and whether to run per
  linked account.
- `Context` is passed to every handler but starts empty; the rate-limited HTTP
  client and storage arrive in M2. `commands()`, `render()` and
  `event_types()` arrive in M2 as members with defaults.

## Consequences

- The platform can check plug-ins up front: schedules name declared job types,
  enqueued jobs have known types and valid payloads, and malformed messages
  are dead-lettered before plug-in code runs.
- Handlers receive typed payloads, checked by mypy.
- Because handlers can create jobs, the worker must record follow-up jobs in
  the same transaction as the job's other writes, so a crash cannot lose
  them. This is designed with the worker loop.
- The contract will grow in M2 through defaulted members; `contract_version`
  is bumped only for breaking changes.
- Whether to enforce the import direction in CI (for example with
  import-linter) is decided separately.
