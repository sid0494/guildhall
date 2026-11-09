# 0026. App services in compose: one-shot setup, then the worker

Status: Accepted (2026-11-09)

## Context

The worker now runs from the container image (ADR 0024). Before it starts, the
database schema must be current and the job queues must exist on moto. ADR
0017 keeps queue creation out of the worker so it needs no permission to
create or reconfigure queues; the same reasoning applies to the schema. On
the host, developers also run the worker with `uv run`, and two workers on one
queue would split its messages unpredictably.

## Options considered

- **The worker prepares everything at start-up:** nothing to remember, but it
  needs schema and queue permissions, and replicas starting together race to
  migrate.
- **One-shot setup services:** `migrate` and `queues` run the same image with
  a different command and exit; the worker waits for both to complete. Keeps
  the worker's permissions minimal and mirrors M3, where a Kubernetes Job
  migrates and Terraform creates queues.
- **By hand:** keep running the two commands on the host; easy to forget, and
  every automated run must repeat them.

## Decision

One-shot services, in a compose profile:

- `migrate` runs `alembic upgrade head`, `queues` runs
  `python -m guildhall.platform.dev_queues`, and `worker` waits for both with
  `condition: service_completed_successfully`. The three share their build,
  image, settings and profile through a YAML anchor.
- They belong to the `app` profile, so `docker compose up` still starts only
  the infrastructure, and a worker run on the host does not compete with one in
  a container.
- Settings come from `.env` through `env_file` (optional, since CI has none),
  with `environment` overriding the addresses that differ inside the compose
  network (`postgres`, `http://moto:5000`).
- `worker` has `stop_grace_period: 45s`, as ADR 0023 requires, and no restart
  policy: its loops recover from errors themselves, and an exit means
  something a developer should see.
- When run as the `alembic` command, migrations log through the platform's
  logging, so each step appears in the `migrate` output.
- CI builds the image on every pull request, in the integration job.

## Consequences

- `docker compose --profile app up -d --build` brings up a working stack from
  a fresh checkout and `.env`.
- Compose builds the image once per service that declares `build`; the builds
  are identical and served from the same cache.
- A broken `Dockerfile` fails the pull request.
- The M3 manifests follow the same split: a migration Job before the worker
  Deployment, queues from Terraform.
