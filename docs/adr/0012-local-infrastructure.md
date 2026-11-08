# 0012. Local infrastructure: Docker Engine, compose, moto and Redis 8

Status: Accepted (2026-11-08)

## Context

M1 needs Postgres, Redis, and stand-ins for SQS and S3 running locally, and
from PR 4 the same services back integration tests in CI. Development happens
in WSL2 (Ubuntu, systemd enabled). The spec named LocalStack for SQS and S3,
but its image now requires an account and an auth token. That token would be
a secret in `.env` and in CI, and GitHub does not expose repository secrets to
Dependabot pull requests, so their integration tests would fail.

## Options considered

Container runtime on WSL2:

- **Docker Desktop:** easiest install, with a GUI, but heavier, with frequent
  update prompts, and licensed free only for personal and small-company use.
- **Podman:** rootless and daemonless, but kind's Podman support is
  experimental and compose compatibility has rough edges.
- **Docker Engine inside Ubuntu:** open source, run by systemd, the same setup
  as Linux servers and CI runners, and works with kind. CLI only.

SQS and S3 emulator:

- **LocalStack with a token:** the highest fidelity, but needs an account and
  a secret, breaks Dependabot pull requests, and is proprietary.
- **ElasticMQ plus moto:** the best SQS fidelity without a token, but two
  containers.
- **moto server:** an open-source (Apache 2.0) single container for SQS
  (visibility timeouts, delays, redrive to a dead-letter queue) and S3; no
  account or token; lower fidelity than LocalStack in edge cases.

Rate-limit store:

- **Valkey:** BSD-licensed Linux Foundation fork, aligned with AWS.
- **Redis 8:** the original and most documented; licensed under AGPLv3 among
  others since 2025, which places no obligations on running it unmodified.
  For token buckets the two behave identically.

Image pinning:

- **Exact tag:** readable, but official images re-push a tag for OS patches,
  so its content can change.
- **Tag and digest:** immutable, like the SHA-pinned actions of ADR 0006.

## Decision

- Docker Engine (`docker-ce` from Docker's apt repository) runs inside the
  WSL2 Ubuntu distribution, managed by systemd.
- `compose.yaml` runs infrastructure only: `postgres:18.6` on Debian (matching
  the glibc collation behaviour of RDS rather than Alpine's musl), `redis:8.10.3`
  with persistence disabled, and `motoserver/moto:5.2.5`. The bot runs on the
  host with `uv run` until it has a container image.
- Images are pinned by tag and digest; Dependabot's `docker-compose` ecosystem
  updates them monthly, grouped, after a 7-day cooldown.
- Every port is bound to `127.0.0.1`. Every service has a health check, so
  `docker compose up --wait` returns only when all are ready.
- The local Postgres password comes from `GUILDHALL_POSTGRES_PASSWORD` in
  `.env`; compose refuses to start without it. Postgres data lives in a named
  volume.

## Consequences

- The local stack and CI need no accounts or secrets beyond `.env`.
- moto identifies the AWS service from each request's signature, so it must
  be called through an AWS SDK (or signed requests), not bare HTTP.
- If a drill exposes a gap in moto's SQS behaviour, ElasticMQ can replace it
  for SQS without code changes, since both speak the SQS API.
- Members of the `docker` group effectively have root on the machine; this is
  accepted for a personal development machine.
- The api and worker join compose once they have a Dockerfile.
