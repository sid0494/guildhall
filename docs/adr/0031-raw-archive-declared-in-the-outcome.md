# 0031. The raw archive is declared in the outcome and uploaded before commit

Status: Accepted (2026-11-09)

## Context

Plug-ins keep every raw upstream response (an OpenDota match, later Steam and
IsThereAnyDeal data) in S3, so a metric that changes or gains a fix can be
recomputed from the archive instead of calling the upstream again, which
costs request budget and may no longer return old data.

S3 is not part of the job's Postgres transaction, so an upload cannot commit
or roll back with the claim, the plug-in's writes and the outbox rows
(ADR 0028). Whatever the API, the order matters:

- **Upload, then commit:** a crash between them leaves an object with no row.
  It is harmless; the job runs again and writes the same key.
- **Commit, then upload:** a crash between them leaves a committed row whose
  archive never arrives, and the job will not run again. The data is lost.

So uploads come first. The question is who performs them.

## Options considered

- **The handler uploads through `ctx.archive.put`:** direct, but every
  plug-in must remember to upload before it returns; a handler that uploads
  and then fails leaves an orphan; and every handler test needs a fake archive.
- **The handler declares archives in the outcome:** `Outcome.archives` holds
  `Archive(kind, id, body)` values and the platform uploads them after the
  outcome passes its checks and before the commit. It follows ADR 0028 and
  ADR 0030: reads through the context, writes declared in the outcome.
- **An outbox for archives:** the body goes into a Postgres table in the job's
  transaction and the relay uploads it later. Atomic, but every response,
  up to several MB for a parsed match, is written to Postgres first and
  deleted again.

## Decision

Archives are declared in the outcome:

- `Outcome.archives` is a tuple of `Archive(kind, id, body)`. `body` is bytes,
  exactly as received. A retry outcome carries none, like writes, events and
  jobs, so a match that is not parsed yet is archived once, when it finishes.
- The runner uploads them after `check_outcome` and before the commit, under
  `archive_timeout_seconds` (10 s). An S3 error or timeout commits nothing and
  retries the job with backoff, like a database error.
- `Context.archive.get(kind, id)` reads a body back, or returns None.
- Archiving the same kind and id again replaces the body. The body comes from
  a fetch made when the job runs, not from the queue message, so even a late
  duplicate stores data at least as new.

## Consequences

- A committed job's archives always exist. Orphan objects (an upload whose
  commit then failed) are possible and harmless.
- Handlers stay plain functions from input to outcome: tests assert on
  `outcome.archives` with no fake S3.
- A body is held in memory from the handler's return to the upload, a few MB
  at most per running job.
- A job that duplicates another, despite the check before running, may upload
  again before its claim fails; the object is replaced with equally fresh data.
- With the job timeout (30 s), the upload timeout (10 s) stays inside the
  queue's 60 s visibility timeout, so a job never runs twice at once.
