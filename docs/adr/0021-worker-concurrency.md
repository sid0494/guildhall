# 0021. Worker concurrency: one receiver, a semaphore of job slots

Status: Accepted (2026-11-09)

## Context

Jobs spend nearly all their time waiting on upstream APIs, Postgres and SQS,
so one asyncio process can run several at once. SQS hides a received message
for the visibility timeout (60 s) and delivers it again if it is not settled
by then; the job timeout is 30 s. A message that waits locally before it
starts uses up that margin and risks running twice, so the worker should
receive only messages it can start straight away. SQS returns at most 10
messages per receive, and every receive is a billed request beyond the free
tier of one million a month.

## Options considered

- **Batch at a time:** receive up to 10, run them all, wait for every one to
  finish, receive again. Simplest, but one slow job leaves the other slots
  idle until it finishes.
- **N independent consumers:** N tasks, each receiving one message and
  running it. Slots are independent, but every idle consumer long-polls on
  its own: about 130,000 requests a month each, so ten use the whole free
  tier.
- **One receiver and a semaphore of slots:** wait for a free slot, take every
  free slot (up to 10), receive that many messages, start a task per message
  and return the slots not filled. Receives only what can start, frees slots
  independently, and makes one request per poll.
- **A task framework (Celery, Dramatiq):** brings its own worker loop,
  acknowledgement and retry semantics, which would replace the lifecycle in
  ADR 0016, and its SQS support is not asyncio.

## Decision

One receiver and a semaphore, in `guildhall.platform.consumer.JobConsumer`:

- `GUILDHALL_WORKER_CONCURRENCY` slots, default 10. A job holds a database
  connection only for its duplicate check and its commit, so 10 slots fit the
  default pool of 15 connections with room for the relay.
- Each job runs as its own task in an `asyncio.TaskGroup`, so it gets its own
  copy of the logging context (ADR 0020) and the consumer can wait for every
  running job on shutdown.
- A message the runner cannot settle (a queue failure) is logged and left on
  the queue for redelivery; its slot is freed and the consumer carries on.
- A failed receive is logged as a warning and retried after 5 seconds.

## Consequences

- No received message waits locally, and a slow job occupies only its own
  slot.
- An idle worker makes about three receive requests a minute.
- Jobs share one thread: a handler doing heavy CPU work stalls every job in
  the process, and its timeout cannot fire until it yields. Handlers move such
  work to `asyncio.to_thread`; this is now a rule in the plug-in contract, and
  M2's metrics are the first place it applies.
- Scaling beyond one process is by adding worker replicas.
