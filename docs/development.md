# Development

## Setup

Requirements: [uv](https://docs.astral.sh/uv/). It installs the right Python
version (3.14) itself.

```sh
uv sync                        # create .venv and install dependencies from uv.lock
uv run pre-commit install      # run the checks below before every commit
cp .env.example .env           # local configuration; .env is git-ignored
```

## Configuration

All configuration comes from environment variables prefixed `GUILDHALL_`.
[`.env.example`](../.env.example) lists every variable; copy it to `.env` and
fill in real values. `.env` is git-ignored and must never be committed. Real
environment variables override `.env`.

## Local services

[`compose.yaml`](../compose.yaml) runs the infrastructure the bot needs:
Postgres, Redis, and moto (an open-source emulator for SQS and S3). They need
[Docker](https://docs.docker.com/engine/install/ubuntu/) with the Compose
plug-in, and `GUILDHALL_POSTGRES_PASSWORD` set in `.env`.

```sh
docker compose up -d --wait     # start everything in the background; wait until healthy
docker compose ps               # list the services and their health
docker compose logs -f postgres # follow one service's logs
docker compose down             # stop and remove the containers; data is kept
docker compose down -v          # also delete the Postgres data volume
```

All ports are bound to `127.0.0.1`: Postgres on 5432, Redis on 6379, moto on
5000.

After starting the services, create the job queue and its dead-letter queue on
moto (safe to run repeatedly):

```sh
uv run python -m guildhall.platform.dev_queues
```

## Database migrations

The schema is managed with Alembic; migrations live in
[`migrations/versions/`](../migrations/versions/). Start the compose services
first.

```sh
uv run alembic upgrade head                            # apply every pending migration
uv run alembic current                                 # show the database's revision
uv run alembic revision --autogenerate -m "add thing"  # draft a migration from table changes; review it
uv run alembic downgrade -1                            # undo the latest migration
```

Never edit or delete a migration that has been applied anywhere; write a new
one instead.

Plug-ins declare their tables on the plug-in (`tables`, from
`plugin_metadata()`), and their migrations live in the same history: name the
revision after the plug-in (`-m "gank_report create matches"`) and touch only
its tables. An integration test fails if any declared table or column has no
migration.

## Running the worker

In containers, the `app` profile adds three services to the infrastructure:
`migrate` and `queues` bring the schema up to date and create the queues, then
exit; `worker` starts once both have succeeded.

```sh
docker compose --profile app up -d --build --wait  # build the image, set up, start the worker
docker compose logs -f worker                      # follow the worker's log
docker compose stop worker                         # stop gracefully (SIGTERM, up to 45 s)
```

On the host instead (for quick iteration; don't run both, or they share the
queue's messages), with the services up, the queues created and the
migrations applied:

```sh
uv run guildhall-worker
```

It logs one line per event: readable on a terminal, JSON when the output is
piped or `GUILDHALL_LOG_FORMAT=json` is set. Ctrl+C (or SIGTERM) stops it
gracefully: it starts no new receive, lets running jobs finish (up to
`GUILDHALL_SHUTDOWN_DRAIN_SECONDS`), sends any follow-up jobs still in the
outbox, and exits. An idle worker first waits for its open receive to end,
which takes up to `GUILDHALL_RECEIVE_WAIT_SECONDS` (20). Press Ctrl+C a second
time to stop at once; running jobs are then redelivered after the visibility
timeout.

## Container image

The [`Dockerfile`](../Dockerfile) builds the image that every environment runs.
Only the files allowed by [`.dockerignore`](../.dockerignore) reach the build,
so `.env` never does.

```sh
docker build -t guildhall:dev .                       # build; unchanged layers come from the cache
docker history guildhall:dev                          # list the layers and their sizes
docker run --rm guildhall:dev id                      # run another command in a throwaway container: shows the user
```

## Checks

```sh
uv run pre-commit run --all-files   # format, lint, types, dependency check, unit and contract tests
uv run pytest -m "not integration and not end_to_end"  # unit and contract tests only
docker compose up -d --wait         # integration tests need the compose services...
uv run pytest -m integration        # ...then run them (CI runs them in their own job)
```

Pre-commit skips integration tests so committing doesn't need Docker running;
CI always runs them. Integration tests use their own `guildhall_test` database
on the compose Postgres, created and migrated automatically, so they never touch
your development data.

### End-to-end tests

End-to-end tests (`tests/end_to_end/`) drive the containerised stack from
outside: they send jobs to the queue and wait for the results in Postgres. They
run on their own throwaway stack, the `guildhall-e2e-test` compose project
(`compose.e2e-test.yaml`), on other ports and with an in-memory database, so
they never touch the development stack. Plain `pytest` skips them.

```sh
make test-e2e        # start the test stack, run the tests, remove the stack
make test-e2e-up     # or keep it running while you debug...
uv run pytest -m end_to_end
make test-e2e-down   # ...and remove it afterwards
```

`make` comes from the `make` package (`sudo apt-get install make`); `make`
alone lists the targets.

## Workflow

Changes go through pull requests into `main`, which are squash-merged. CI runs
the same pre-commit hooks, the integration tests, the end-to-end tests and a
secret scan of the full history; all four must pass before a pull request can
be merged.

Design decisions are recorded in [`docs/adr/`](adr/) using the
[template](adr/template.md).
