# 0018. Job runner: backoff, timeouts and error classes

Status: Accepted (2026-11-08)

## Context

ADR 0016 fixed the job lifecycle and how failures are routed. Implementing it
leaves several choices: how long a retryable job waits, how long a handler may
run, what happens when the database rather than the handler fails, and how
the runner is tested without SQS. The spec's drills require that a two-minute
upstream `429` storm dead-letters nothing and that a 60-second Postgres outage
recovers without manual steps, with at most five receives per message.

## Options considered

Backoff after a retryable failure:

- **Fixed delay:** simple, but jobs failed by one outage all retry at the same
  moment and hit the upstream together again (a thundering herd).
- **Exponential backoff with full jitter:** a random delay up to the
  exponential value spreads retries best, but an unlucky job can use up its
  retries within seconds.
- **Exponential backoff with equal jitter:** half the exponential value fixed,
  half random; nearly as good a spread with a guaranteed minimum wait.

## Decision

- Retryable failures wait `exponential / 2 + random(0, exponential / 2)`
  seconds, where `exponential = min(cap, base * 2^(receive_count - 1))`, with a
  20-second base and a 15-minute cap (`GUILDHALL_RETRY_BASE_SECONDS`,
  `GUILDHALL_RETRY_CAP_SECONDS`). Four retries wait between 150 and 300
  seconds in total.
- Handlers run under a 30-second timeout (`GUILDHALL_JOB_TIMEOUT_SECONDS`),
  below the queue's 60-second visibility timeout so a job never runs twice at
  once; a timeout is retried.
- `RetryableError`, unexpected exceptions and database errors are retried with
  backoff; errors from the queue itself propagate to the worker loop.
- Delays in `JobSpec.delay` and `Outcome.retry_in` over 15 minutes, the SQS
  limit, are contract violations (`check_outcome`), dead-lettered with the
  reason.
- Dead-letter reasons from validation name fields and messages only, never the
  offending values, so no payload data leaks into queue attributes.
- `JobRunner.run(message)` settles the message and returns a `RunResult`
  (done, duplicate, not ready, retrying or dead-lettered, with a reason) for
  the worker loop to log. It depends on a small `MessageQueue` protocol that
  `JobQueue` satisfies structurally, so tests use a recording fake queue
  against real Postgres.

## Consequences

- A two-minute upstream outage or a 60-second database outage is survived
  without dead-lettering; longer outages dead-letter jobs for redrive.
- Retries from a burst of failures spread over time rather than arriving
  together.
- Handlers that legitimately need longer than 30 seconds must split their work
  into follow-up jobs, or the timeout and visibility timeout must be raised
  together.
