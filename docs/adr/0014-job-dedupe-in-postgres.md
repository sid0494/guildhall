# 0014. Job deduplication in Postgres

Status: Accepted (2026-11-08)

## Context

SQS standard queues deliver at least once: a message can arrive twice after a
network error, or when a worker finishes a job but crashes before deleting
the message. The platform promises that a job takes effect once even if it is
delivered twice.

## Options considered

- **SQS FIFO deduplication:** FIFO queues drop a message whose deduplication
  ID was seen in the previous five minutes. Redeliveries after that window
  (a long job, a crash, a visibility timeout) are not caught; deduplication is
  not atomic with the job's database writes; FIFO queues have throughput
  limits and a different delivery model.
- **A `processed_jobs` table in Postgres:** the job's dedupe key is inserted in
  the same transaction as the job's writes; works with standard queues and has
  no time window.

## Decision

- `processed_jobs(dedupe_key primary key, completed_at)` records every job
  that has taken effect.
- `guildhall.platform.dedupe.claim(connection, key)` inserts the key with
  `ON CONFLICT DO NOTHING` inside the caller's transaction and reports whether
  this transaction recorded it. A false result means the job already took
  effect, so the worker rolls back and acknowledges the message.
- Concurrent claims of one key serialise on the primary key: the second
  waits until the first transaction ends, then fails if it committed or
  succeeds if it rolled back. An integration test demonstrates this.

## Consequences

- A duplicate delivery is a no-op however late it arrives.
- `processed_jobs` grows by one row per job; at this project's volume that is
  negligible, and old rows can be pruned later if it ever matters.
- Holding a claim's row lock during slow work would block a duplicate
  delivery until the work finishes; where in the job the claim happens is
  decided with the worker loop.
