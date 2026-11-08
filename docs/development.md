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

## Checks

```sh
uv run pre-commit run --all-files   # everything CI runs: format, lint, types, tests
uv run pytest                       # tests only
```

## Workflow

Changes go through pull requests into `main`, which are squash-merged. CI runs
the same pre-commit hooks plus a secret scan of the full history, and must pass
before a pull request can be merged.

Design decisions are recorded in [`docs/adr/`](adr/) using the
[template](adr/template.md).
