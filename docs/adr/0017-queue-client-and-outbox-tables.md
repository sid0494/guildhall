# 0017. Queue client, local queue setup, job envelope and outbox tables

Status: Accepted (2026-11-08)

## Context

The worker is asynchronous and spends most of its time long-polling SQS for
up to 20 seconds. On Kubernetes it must shut down within the grace period
after SIGTERM. Queues exist on AWS through Terraform, but locally and in CI
something must create them on the moto emulator. The lifecycle in ADR 0016
needs a message format and two outbox tables.

## Options considered

SQS client:

- **boto3 on a thread (`asyncio.to_thread`):** the most documented, but a long
  poll occupies a thread and cannot be cancelled, so shutdown waits for it.
- **aioboto3:** boto3's resource-style API over aiobotocore; a third layer the
  project does not need.
- **aiobotocore:** asynchronous botocore with the same operation names as
  boto3; long polls can be cancelled; typed through `types-aiobotocore`. It
  pins a narrow botocore range, so it trails AWS releases slightly.

Creating queues locally:

- **The worker creates them at startup:** convenient, but the production
  worker would need permission to create queues, and queue settings would live
  in application code as well as Terraform.
- **A compose init container running the AWS CLI:** automatic, but another
  image to pin and settings expressed as shell commands in YAML.
- **An idempotent setup command:** explicit and testable; the worker never
  creates anything.

## Decision

- aiobotocore is the SQS client. `QueueSettings` (prefix `GUILDHALL_`) holds
  the region, an optional endpoint and optional static credentials (used for
  moto; on AWS the pod's role supplies credentials), the queue names
  (`guildhall-jobs`, `guildhall-jobs-dlq`), the maximum receive count (5) and
  the visibility timeout (60 seconds).
- `guildhall.platform.queue.JobQueue` sends, receives, deletes, delays
  (`retry_later`) and dead-letters (`dead_letter`, with a
  `dead_letter_reason` message attribute).
- `python -m guildhall.platform.dev_queues` creates both queues and the
  redrive policy, and is safe to run repeatedly.
- The message body is a versioned `JobEnvelope` (`v`, plug-in, job type,
  dedupe key, attempt, raw payload). It is platform-internal; the payload is
  validated against the job type's model only when the job runs.
- `outbox_jobs` holds envelopes with a delay (0 to 900 seconds, the SQS limit,
  enforced by a check constraint) and `sent_at`, with a partial index on unsent
  rows. Its dedupe key is deliberately not unique: two polls may legitimately
  queue the same job, and job dedupe handles it.
- `outbox_events` holds events with a unique dedupe key, so one event key
  yields at most one Discord message. Delivery columns arrive with the
  dispatcher in M2.

## Consequences

- Shutdown can cancel an in-flight long poll immediately.
- Local and CI setups need the moto endpoint and any static credentials in
  the environment; AWS needs neither.
- A handler asking for a delay over 15 minutes cannot be queued; the job
  runner must reject it as a contract violation before writing.
