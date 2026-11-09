# 0023. Graceful shutdown: finish the open receive, drain, flush

Status: Accepted (2026-11-09)

## Context

Kubernetes and `docker compose stop` send SIGTERM, then SIGKILL once the grace
period ends (30 s and 10 s by default; both are configurable). Up to 10 jobs
may be running. The lifecycle in ADR 0016 keeps data correct however a job is
cut off, so the cost of a careless stop is wasted work: a cut-off job repeats
its upstream calls, and its message stays invisible until the 60 s visibility
timeout ends.

A local experiment showed a further cost. Cancelling a long-poll receive on
our side does not end it on the server: moto kept the request open for its
full wait and gave the next message that arrived to it, where nobody would
ever read it, so the message stayed invisible for 60 s. We have not checked
whether SQS behaves the same, and the design should not depend on it.

## Options considered

What to do with running jobs:

- **Cancel at once:** exits immediately; every running job is redone after up
  to 60 s, on every deploy.
- **Drain, then cancel leftovers:** stop receiving, wait for running jobs up
  to a deadline, flush the outbox, exit. Nothing wasted when the deadline is
  above the job timeout; the grace period must cover it.
- **Cancel and hand back:** cancel jobs and make their messages visible again
  at once. Fast, but the work is still wasted and redone.
- **Drain, and hand back leftovers:** only differs when a job outlives the
  deadline, which the deadline is chosen to prevent.

What to do with a receive in progress:

- **Cancel it:** quick, but strands any message the server gives it.
- **Let it finish and hand back what it returns:** nothing is stranded; an
  idle stop waits up to the receive wait (20 s), alongside the drain.
- **The same with shorter polls (5 s):** faster idle stops, but four times the
  receive requests when idle, about half the free tier per replica.

## Decision

Drain, letting the open receive finish:

- SIGTERM or SIGINT sets a stop event. The consumer starts no new receive; a
  receive waiting on SQS finishes, and any messages it returns are handed back
  with visibility 0. A consumer waiting for a free slot or pausing after an
  error is cancelled, since it holds nothing.
- Running jobs and the open receive are waited for together, up to
  `GUILDHALL_SHUTDOWN_DRAIN_SECONDS` (35: the 30 s job timeout plus time to
  settle); anything left is cancelled. Running jobs are now a plain set of
  tasks instead of ADR 0021's `TaskGroup`, which cannot wait with a deadline.
- The relay keeps running during the drain, so follow-up jobs of draining jobs
  are sent. Pruning is cancelled; the relay then passes until the outbox is
  empty, for up to 5 s, and stops at the first failure.
- A second signal cancels everything at once; the process exits with code 1.
- The receive wait becomes a setting, `GUILDHALL_RECEIVE_WAIT_SECONDS`
  (0 to 20, default 20).
- `guildhall-worker` (a `[project.scripts]` entry) runs
  `guildhall.bootstrap.worker_main`: configure logging, load plug-ins, open
  the engine and SQS client, run the worker until stopped, close them.

## Consequences

- A deploy finishes running jobs instead of redoing them, and strands no
  message.
- A stop takes up to 40 s (35 drain, 5 flush). The grace period must be at
  least 45 s: `stop_grace_period` when the worker joins compose, and
  `terminationGracePeriodSeconds` in M3. Raising the job timeout means raising
  the drain and the grace period with it.
- An idle worker takes up to the receive wait to stop. In development, a
  second Ctrl+C stops at once.
- If the process runs behind a wrapper (such as `uv run`, or a shell in a
  container), the wrapper must pass SIGTERM on to it; the container image
  should start `guildhall-worker` directly.
