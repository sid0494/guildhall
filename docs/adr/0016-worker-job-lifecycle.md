# 0016. Worker job lifecycle: transactional outbox and failure routing

Status: Accepted (2026-11-08)

## Context

The worker receives job messages from SQS, which delivers at least once, and
runs the plug-in's handler. A handler's outcome can include events (delivered
to Discord later) and follow-up jobs (sent back to SQS). A database
transaction cannot include a queue send, and failures range from transient
upstream errors to malformed messages that will never succeed. The spec
promises that a job takes effect once, a crashed worker loses no work, and bad
jobs don't block good ones.

## Options considered

Delivering follow-up jobs:

- **Send to SQS before committing:** never loses a job, but a failed commit
  leaves jobs for work that never happened.
- **Send to SQS after committing:** a crash between the two loses the jobs for
  good; a backfill would silently stop halfway.
- **Transactional outbox:** write the jobs as rows in the same transaction; a
  relay sends pending rows to SQS and marks them sent. A crash before marking
  causes a resend, which job dedupe makes harmless.

Routing failures:

- **Leave everything to SQS redrive:** after a fixed number of receives SQS
  moves the message to the dead-letter queue. Simple, but permanent failures
  are retried pointlessly and arrive in the dead-letter queue with no reason.
- **Route by kind of failure:** retry what might succeed, dead-letter at once
  what cannot, with the reason attached.

## Decision

For each message the worker:

1. Parses the envelope and finds the plug-in and job type.
2. Skips the job if its dedupe key is already in `processed_jobs`, deleting the
   message, so duplicates do not repeat upstream calls.
3. Validates the payload and runs the handler outside any database
   transaction, so slow upstream calls never hold a transaction open.
4. Checks the outcome with `check_outcome`.
5. In one transaction: claims the dedupe key, inserts the events into
   `outbox_events` and the follow-up jobs into `outbox_jobs`. If the claim
   fails, it rolls back.
6. Deletes the message only after the commit.

Failures are routed as follows:

| What happened | Handling |
| --- | --- |
| `RetryableError` or an unexpected exception | The message stays on the queue, invisible for a backoff delay; SQS dead-letters it after the maximum receive count |
| `PermanentError`, invalid envelope or payload, contract violation, unknown job type, or disabled plug-in | Sent to the dead-letter queue at once with the reason as a message attribute, then deleted |
| `Outcome(retry_in=...)` | A new message with `attempt + 1` and the delay is sent, then the original is deleted; it does not count as a failure |
| Worker crash | The visibility timeout expires and SQS redelivers the message |

The outbox relay runs inside the worker process.

## Consequences

- No committed follow-up job is ever lost, and none is sent for work that
  rolled back.
- Every dead-lettered message explains itself, and can be redriven to the main
  queue once the cause is fixed, including jobs for a plug-in that was
  disabled.
- A crash between sending a retry message and deleting the original leaves two
  copies of the job; dedupe keeps that harmless.
- How plug-ins write their own tables inside step 5's transaction is designed
  with storage in M2.
- The work is split into: queue, envelope and outbox tables; the job runner;
  the outbox relay; the worker process; and an end-to-end test.
