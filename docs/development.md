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

After starting the services, create the job queue, its dead-letter queue and
the raw archive bucket on moto (safe to run repeatedly):

```sh
uv run python -m guildhall.platform.dev_aws
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
`migrate` and `aws-setup` bring the schema up to date and create the queues
and the archive bucket, then exit; `worker` starts once both have succeeded.

```sh
docker compose --profile app up -d --build --wait  # build the image, set up, start the worker
docker compose logs -f worker                      # follow the worker's log
docker compose stop worker                         # stop gracefully (SIGTERM, up to 45 s)
```

On the host instead (for quick iteration; don't run both, or they share the
queue's messages), with the services up, the queues and bucket created and
the migrations applied:

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

## Running the api

The api is Discord's interactions endpoint. It needs the application's public
key, from the developer portal's General Information page, as
`GUILDHALL_DISCORD_PUBLIC_KEY` in `.env`. The `app` profile runs it on
`127.0.0.1:8000`, once the migrations are done:

```sh
curl -s http://127.0.0.1:8000/healthz   # liveness: {"status":"ok"}
curl -s http://127.0.0.1:8000/readyz    # readiness: Postgres answers
docker compose logs -f api              # follow the api's log
```

On the host instead: `uv run guildhall-api` (stop the container first; both
use port 8000).

### Slash commands

Commands appear in Discord once registered with your test server. Set
`GUILDHALL_DISCORD_APPLICATION_ID`, `GUILDHALL_DISCORD_GUILD_ID` and
`GUILDHALL_DISCORD_BOT_TOKEN` (a secret) in `.env`; the application must be
installed in the server with the `applications.commands` scope.

```sh
uv run guildhall-register-commands --dry-run   # print what Discord would receive
uv run guildhall-register-commands             # make the server's commands match the enabled plug-ins
```

Run it again after adding, changing or removing a command: it overwrites the
server's whole list. With the `synthetic` plug-in enabled, `/synthetic echo`
(public) and `/synthetic whisper` (private) reply with your text. Each command
runs as a job on the worker, so the worker must be up for replies to arrive.

### Linking an account

With `gank_report` in `GUILDHALL_PLUGINS`, register the commands again so
Discord learns `/link`, then run `/link dota account:<your ID>` in your server
(the ID from `opendota.com/players/<id>`, or your Steam ID). The reply is
private. The worker then backfills your 100 newest matches, which shows in
its log as `backfill`, `fetch_match` and, five minutes later, `analyze_match`
jobs; the schedule polls for new matches every 15 minutes. To see what was
stored:

```sh
docker compose exec postgres psql -U guildhall -d guildhall \
  -c 'select count(*), count(*) filter (where parsed) as parsed from gank_report_matches'
```

### Match reports

The worker posts reports to `GUILDHALL_DISCORD_CHANNEL_ID` (right-click the
channel, Copy Channel ID), with the bot token; without both it logs that
posting is off. In that channel the bot needs View Channel, Send Messages and
Embed Links. Each post shows in the worker's log as `event posted`; an event
the worker cannot post shows as `event abandoned` with the reason.

Hero names and icons come from a table shipped with Gank Report. When Valve
adds or renames a hero:

```sh
uv run python scripts/update_heroes.py                    # rewrite the table from OpenDota
git diff src/guildhall/plugins/gank_report/heroes.json   # review what changed
```

### Letting Discord in: the tunnel

Discord calls the api over public HTTPS, so locally an ngrok agent forwards a
fixed public address to it ([ADR 0060](adr/0060-ngrok-tunnel.md)). Set
`GUILDHALL_NGROK_AUTHTOKEN` (a secret, from the ngrok dashboard) and
`GUILDHALL_NGROK_DOMAIN` (your free static domain, without `https://`) in
`.env`, then:

```sh
docker compose --profile app --profile tunnel up -d --build --wait  # everything, plus the tunnel
docker compose --profile app --profile tunnel logs ngrok            # "started tunnel" and the URL
curl -s https://<your-domain>/healthz                                # through the tunnel, as Discord calls
docker compose --profile app --profile tunnel down                  # stop everything; data is kept
```

Name both profiles in every command that touches `ngrok`: it depends on the
api, which is in the `app` profile, and compose refuses to load a service
whose dependency is outside the active profiles. Open
<http://127.0.0.1:4040> to see each request Discord sends and the answer.

In the developer portal, set the application's **Interactions Endpoint URL**
to `https://<your-domain>/interactions` once; the domain never changes. Saving
it makes Discord send a `PING` and some badly signed requests, so it succeeds
only while the api and the tunnel are up.

## Upstream rate limits

Each plug-in's upstream budget lives in Redis, shared by every worker. To look
at it while jobs call upstream:

```sh
docker compose exec redis redis-cli --scan --pattern 'guildhall:*'   # the budgets and any cooldown
docker compose exec redis redis-cli pttl guildhall:cooldown:<plug-in>  # milliseconds a 429 still pauses it
```

When Redis is stopped, jobs that call upstream are logged as `deferred` and
come back every 30 to 60 seconds until it returns; other jobs carry on.

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

Tests import shared fakes of a handler's services (`Context.db`, `.archive`,
`.http`) from [`tests/support/fakes.py`](../tests/support/fakes.py) as
`fakes`; reach for those before writing a new fake.

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

### Recorded fixtures

Tests replay real OpenDota responses from `tests/fixtures/opendota/` and never
call the live API. [`scripts/record_opendota.py`](../scripts/record_opendota.py)
records them from the account in `GUILDHALL_FIXTURE_ACCOUNT_ID` (set it in
`.env`): its match list, and its newest unparsed match before and after
OpenDota parses it. Every response is trimmed to the fields the recorder's
shapes name and every match and account ID is replaced with a fake, so the
files can be committed to this public repository
([ADR 0039](adr/0039-recorded-fixtures-are-response-bodies-from-a-script.md) to
[ADR 0042](adr/0042-a-structural-test-checks-fixtures.md)).

```sh
uv run python scripts/record_opendota.py   # record; waits up to 15 minutes for the parse
git diff --stat tests/fixtures             # see which fixtures changed
uv run pytest tests/unit/test_recorded_fixtures.py  # only allowlisted fields and fake IDs
```

It needs the account's Dota setting **Expose Public Match Data** on, and a
recent match that OpenDota has not parsed yet. To use a field in a test that the
fixtures lack, add it to the recorder's shape and record again.

## Workflow

Changes go through pull requests into `main`, which are squash-merged. CI runs
the same pre-commit hooks, the integration tests, the end-to-end tests and a
secret scan of the full history; all four must pass before a pull request can
be merged.

Design decisions are recorded in [`docs/adr/`](adr/) using the
[template](adr/template.md).
