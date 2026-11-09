# 0022. Worker loops: siblings in one task group, recovering in place

Status: Accepted (2026-11-09)

## Context

A worker process runs three things: the job consumer (ADR 0021), the outbox
relay's polling loop (ADR 0019) and the periodic pruning of sent outbox rows.
ADR 0016 places the relay inside every worker. In an `asyncio.TaskGroup`, an
exception escaping one task cancels its siblings, so the way each loop treats
a failed pass decides whether a database or queue outage pauses work or ends
the process along with every job in flight.

## Options considered

- **Loops recover in place:** each loop catches errors around one pass, logs
  them and pauses, as the consumer's receive already does; the process stops
  only when asked. An outage pauses housekeeping while jobs carry on. A real
  bug keeps logging instead of failing loudly, so it is noticed through logs
  and metrics rather than restarts.
- **Crash-only:** nothing is caught; any error ends the process and the
  supervisor starts a fresh one. Restarts clear bad state and bugs are loud,
  but a brief outage kills in-flight jobs, which are redelivered 60 s later
  and repeat their upstream calls, and a long outage becomes a crash loop.
- **Separate processes for the relay and pruning:** isolated, but one more
  thing to deploy and watch, against ADR 0016's relay in every worker.

## Decision

Loops recover in place, in `guildhall.platform.worker.Worker`:

- `Worker.run()` starts the consumer, relay loop and prune loop as siblings in
  one `TaskGroup`. For now it runs until cancelled.
- Relay loop: after a pass that sent rows it goes again at once; after an
  empty pass it sleeps 1 s; after a failed pass it logs a warning with the
  traceback and pauses 5 s.
- Prune loop: prunes at start-up and then every
  `GUILDHALL_PRUNE_INTERVAL_SECONDS` (3600); a failed prune is logged and
  waits for the next interval, since pruning is never urgent.
- Loops catch `Exception`, never `BaseException`, so cancellation always gets
  through.

## Consequences

- A database or queue outage pauses the relay and pruning without touching
  jobs in progress, and they resume on their own.
- A loop that fails on every pass looks alive while doing nothing. The
  Kubernetes liveness probe in M3 must check that each loop is still making
  progress, not just that the process is up; until then, warnings in the log
  are the signal.
- Several replicas pruning at once is harmless: one waits for the other's
  row locks and then finds nothing to delete. The delete scans `outbox_jobs`
  without an index on `sent_at`, which is cheap while the table holds about a
  week of rows; revisit if it grows.
