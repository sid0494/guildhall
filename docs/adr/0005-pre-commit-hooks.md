# 0005. Pre-commit hooks with uv-run tools and gitleaks

Status: Accepted (2026-11-07)

## Context

The repository is public, so a secret that reaches GitHub must be treated as
leaked even if it is deleted at once. Lint, type and test failures are also
cheaper to catch before a commit exists than after a CI run. Git hooks live
in `.git/hooks/`, which is not versioned, so a hook manager is needed to
share the hook configuration through the repository.

## Options considered

Hook manager:

- **pre-commit:** the established framework; hooks pinned to git tags in
  `.pre-commit-config.yaml`; largest ecosystem of published hooks.
- **prek:** a faster reimplementation that reads the same configuration;
  younger project.
- **lefthook:** fast, but uses its own configuration format, and most
  published hooks target pre-commit.

Source of the Python tools inside hooks:

- **Published hook repositories** (`ruff-pre-commit`, `mirrors-mypy`): each
  tool runs in an isolated environment pinned in the YAML file, so versions
  are pinned in two places and drift from `uv.lock`; mypy cannot see the
  project's dependencies unless they are listed again.
- **Local hooks that call `uv run`:** versions come from `uv.lock`, identical
  to CI; requires uv, which the project already needs.

Secret scanner:

- **detect-secrets:** needs a baseline file that must be kept up to date.
- **trufflehog:** strongest at verifying whether a found secret is live, which
  needs network access and is slow for every commit.
- **gitleaks:** fast, no setup, broad rule set for known credential formats
  plus high-entropy strings.

## Decision

- **pre-commit**, installed as a dev dependency so its version is pinned in
  `uv.lock`, and enabled per clone with `uv run pre-commit install`.
- Hooks:
  - `pre-commit-hooks`: merge-conflict markers, private keys, large files,
    YAML and TOML syntax, final newline, trailing whitespace.
  - `gitleaks` on the staged changes.
  - Local hooks: `uv lock --check`, `ruff format`, `ruff check`, `mypy` and
    `pytest`, each through `uv run --locked` so a stale lockfile fails
    instead of being updated silently.
- Formatters and whitespace hooks fix files in place and fail the commit so
  the fix can be reviewed and staged; linters only report.
- Hook repository revisions are pinned to tags and updated deliberately with
  `pre-commit autoupdate`.

## Consequences

- A secret, lint error, type error or failing test blocks the commit locally.
- The first run downloads the hook environments (gitleaks is built with a Go
  toolchain that pre-commit fetches), taking about half a minute; later runs
  reuse them.
- Hooks can be skipped with `git commit --no-verify`, so CI repeats the same
  checks and adds a full-history gitleaks scan.
- Tests run on every commit while the suite is fast; if it becomes slow,
  pytest moves to the pre-push stage, with CI still running everything.
- Every new clone needs `uv run pre-commit install` once; the README will say
  so.
