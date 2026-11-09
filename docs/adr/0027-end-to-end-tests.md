# 0027. End-to-end tests against an isolated, containerised stack

Status: Accepted (2026-11-09)

## Context

M1 is done when a plug-in's jobs flow end to end and a duplicate delivery is a
no-op. Unit and integration tests already cover each part in the development
environment; what they cannot see is the artefact we deploy: the image, the
compose wiring, the one-shot setup services and the settings inside the
containers. The first image showed that gap is real, with a dev-only import
that crashed only in the container (ADR 0025). The test must not touch the
development stack's data or queues.

## Options considered

- **The containerised stack, driven from outside:** send messages to the queue
  and wait for the results in Postgres. Tests what is deployed; slower, and a
  failure reads as "a condition never became true", debugged from the logs.
- **The worker inside the test process:** fast and easy to debug, but tests
  neither the image nor compose.
- **The worker command as a subprocess:** tests the entry point, but from the
  development environment; `test_worker_process.py` already does this.

## Decision

The containerised stack, as its own compose project:

- `compose.e2e-test.yaml` layers onto `compose.yaml` under the project name
  `guildhall-e2e-test`: other host ports (15432, 15000, 16379), Postgres data
  on a tmpfs so each run starts empty, the developer's `.env` ignored, and the
  settings the test relies on (the synthetic plug-in, moto credentials, a 5 s
  receive wait for a quicker stop) set explicitly. Names say "test" so the
  stack is never mistaken for the development one.
- Tests live in `tests/end_to_end/`, are marked `end_to_end`, and are
  deselected by default; they reach the stack only through its published
  ports. Assertions poll for a condition with a 30 s deadline.
- `make test-e2e` starts the stack, runs the tests and always removes the
  stack; on failure it prints the setup and worker logs first.
  `make test-e2e-up` and `make test-e2e-down` keep it running for debugging.
- The first test is the M1 acceptance scenario: a synthetic job with three
  follow-ups and two events, then the same message again.
- CI runs `make test-e2e` in its own job, "End-to-end tests", which is a
  required check on `main`. The image build moves there from the integration
  job (ADR 0026), since this job builds the image anyway. Running it inside
  the integration job would avoid a ruleset change but serialise the two and
  blur which layer failed; running it only after merge would let a broken
  image reach `main`.

## Consequences

- The image, compose wiring and settings are tested together, the way they
  are deployed.
- The two stacks can run side by side; the test stack leaves nothing behind.
- Running the stack exposed that app containers took the database password
  only from `.env`; it now also comes through compose interpolation, so the
  stack runs where there is no `.env`, such as CI.
- `check-yaml` cannot load compose's `!reset` and `!override` tags, so compose
  files are checked for syntax only; `docker compose config` validates them
  fully.
- The test runs in about a second once the stack is up; starting the stack
  takes most of the time.
- A pull request now waits for four required checks; the end-to-end job runs
  in parallel with the others, so it adds no wall-clock time unless it is the
  slowest.
