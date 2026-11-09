# 0036. Schedules fire from every worker, claimed once in Postgres

Status: Accepted (2026-11-09)

## Context

Plug-ins declare recurring triggers with `schedules()`: Gank Report polls for
new matches every 15 minutes and posts a weekly report on Sundays. Something
must turn each tick into jobs, and with several worker replicas each tick must
fire once. The spec planned EventBridge Scheduler, but everyday use runs on a
laptop, not on AWS.

## Options considered

- **EventBridge Scheduler:** sends a fixed message to SQS on each schedule.
  AWS only, so local use needs a second implementation; and it cannot fan out
  per linked account, because it does not know the accounts.
- **A separate scheduler process** with one replica: clear ownership, but one
  more service to run, and a rolling deploy briefly runs old and new together,
  so it still needs protection against firing twice.
- **A loop in every worker, claimed in Postgres:** each worker looks for due
  ticks; the first to claim a tick writes its jobs to the outbox in the same
  transaction, and the relay sends them.

## Decision

A loop in every worker, claimed in Postgres:

- `schedule_runs` has one row per schedule (`plugin`, `job_type`) holding the
  tick it last fired for (`fired_for`).
- Every `GUILDHALL_SCHEDULE_INTERVAL_SECONDS` (30 s), each worker computes
  each schedule's latest tick at or before now (cronsim, UTC), makes sure the
  row exists, and claims the tick with a compare-and-set:
  `UPDATE schedule_runs SET fired_for = :tick WHERE ... AND fired_for < :tick`.
  Postgres locks the row for the update; a second worker waits, re-checks the
  condition against the committed value and matches nothing. The worker whose
  update changed one row owns the tick.
- In the same transaction it adds the tick's jobs to `outbox_jobs`; the relay
  sends them. A crash before the commit leaves the tick unclaimed, so the next
  pass, here or on another worker, fires it.
- The loop is a fourth sibling in the worker's task group (ADR 0022): a failed
  pass is logged and waits for the next; stopping cancels it, like pruning.
- `check_declarations` refuses a schedule with an invalid cron expression, and
  a job type scheduled more than once (the claim row is per job type).

## Consequences

- The same code schedules on the laptop, in compose and on Kubernetes. No AWS
  resource is needed: EventBridge Scheduler leaves the infrastructure plan.
- A tick's jobs are queued exactly once, and their dedupe keys still guard
  against a duplicate delivery downstream.
- Each worker runs one small transaction per schedule every 30 s.
- A job fires up to 30 s after its tick, plus the relay's pass.
- `rowcount` is trusted only for the UPDATE: SQLAlchemy documents it as
  reliable for UPDATE and DELETE, and a test showed it reports -1 for the
  `INSERT ... ON CONFLICT DO NOTHING` that creates the row.
