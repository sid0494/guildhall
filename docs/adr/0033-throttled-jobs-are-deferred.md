# 0033. Throttled jobs are deferred, not failed

Status: Accepted (2026-11-09)

## Context

A handler's upstream call sometimes cannot go out now:

- the plug-in's budget is spent, for seconds (the minute window) or for hours
  (the daily quota);
- the upstream answers `429 Too Many Requests`;
- Redis, which holds the shared budget, is unreachable. The spec says the
  limiter fails closed: no call rather than risk exceeding someone's limits.

None of these is the job's fault, and the spec's drills expect throttled jobs
to resume, never to be dead-lettered.

## Options considered

- **Always wait in the handler** until the budget allows the call. Hours of
  waiting for a daily quota exceed the 30 s job timeout, and every waiting job
  holds one of the worker's slots.
- **Raise `RetryableError`.** Each retry is another SQS receive, so after 5 a
  good job is dead-lettered; and each retry raises `attempt`, so Gank Report's
  "give up on parsing after 3 attempts" would fire because of throttling.
- **Wait briefly, otherwise defer.** If a slot frees within a few seconds,
  wait for it; otherwise raise `ThrottledError(retry_after)` and let the runner
  send the job again later as a new message with the same attempt number.

## Decision

Wait briefly, otherwise defer:

- `ctx.http` waits in place while the total wait stays within
  `GUILDHALL_RATE_LIMIT_MAX_WAIT_SECONDS` (5 s).
- Beyond that, after a `429`, or when the limiter is unreachable, it raises
  the platform's `ThrottledError`. A `429` also sets a cooldown shared by every
  replica (ADR 0034) for `Retry-After` seconds, or 60 s without one; an
  unreachable limiter defers by 30–60 s at random.
- The runner re-sends the envelope with the current attempt number and a delay
  of the retry-after plus up to half again (spreading jobs that return
  together), at most 15 minutes, then deletes the original. The job is logged
  as `deferred`. A new message starts SQS's receive count again, so throttling
  never moves a job towards the dead-letter queue.

## Consequences

- Backfilling 100 matches against a 60-a-minute budget waits a little within
  each job and defers the rest; nothing fails.
- A spent daily quota defers jobs every 15 minutes until it frees. A Redis
  outage defers upstream-calling jobs every 30–60 s until Redis returns: it
  pauses them, as the spec asks. Jobs that make no upstream calls are not
  affected.
- A deferred job never reaches the dead-letter queue, so a job that is
  throttled forever loops forever; its log lines show it.
- `ThrottledError` is an ordinary exception, so a handler that wraps
  `ctx.http` in a bare `except Exception` would swallow it. The contract
  documents that handlers must not; our plug-ins are the only ones.
- Sending the new message and deleting the old are two steps, as for retry
  outcomes; a crash between them leaves two copies, which dedupe handles.
