# 0019. Outbox relay: SKIP LOCKED batches, mark and prune, adaptive polling

Status: Accepted (2026-11-08)

## Context

ADR 0016 writes follow-up jobs to `outbox_jobs` in the job's transaction and
leaves a relay to send them to SQS. Several worker replicas will each run a
relay. The relay must never lose a committed row, should avoid sending the
same row from two replicas at once, and should make it possible to see what
was sent. Its integration tests act on every pending row, so they must not
share a database with development data.

## Options considered

Sent rows:

- **Delete on send:** simplest, and the table only ever holds pending work,
  but the database keeps no record of what was sent.
- **Mark `sent_at`, prune after a retention period:** "was this job sent, and
  when?" is answerable in SQL; one periodic delete keeps the table small.

Learning there is work:

- **Postgres `LISTEN/NOTIFY`:** instant wake-up, but a dedicated, always-open
  connection with reconnect handling, and polling is still needed as a
  fallback for missed notifications.
- **Adaptive polling:** loop again at once after a pass that found rows, sleep
  a second after an empty pass; about a second of latency, one cheap indexed
  query per idle second.

Coordinating replicas:

- **No coordination:** replicas would read and send the same rows; harmless
  thanks to dedupe, but wasteful.
- **`SELECT ... FOR UPDATE SKIP LOCKED`:** each pass locks its rows, and other
  relays skip locked rows instead of waiting, turning the table into a work
  queue shared safely by many consumers.

## Decision

- `OutboxRelay.relay_once()` locks up to `GUILDHALL_RELAY_BATCH_SIZE` (100)
  of the oldest unsent rows with `FOR UPDATE SKIP LOCKED`, sends each to the
  queue with its delay, and sets `sent_at` in the same transaction. If a send
  fails, the transaction rolls back and the whole batch is sent again later;
  job dedupe absorbs the duplicates.
- `OutboxRelay.prune()` deletes rows sent more than
  `GUILDHALL_OUTBOX_RETENTION_DAYS` (7) ago.
- The relay is woken by adaptive polling; the loop, and how often pruning
  runs, belong to the worker process.
- Integration tests use a separate `guildhall_test` database on the same
  server, created on first use and migrated at the start of each session.
  Alembic's environment accepts a URL from the caller for this.

## Consequences

- Committed follow-up jobs reach SQS within about a second, from whichever
  replica locks them first.
- A failure mid-batch resends up to a batch of jobs; at most that many
  duplicates, each dropped by dedupe.
- Development data is safe from test runs; the test database can be dropped
  at any time and is recreated on the next run.
