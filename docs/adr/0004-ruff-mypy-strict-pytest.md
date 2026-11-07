# 0004. Lint and format with ruff, type check with mypy strict, test with pytest

Status: Accepted (2026-11-07)

## Context

Every change must pass lint, type checks and tests, first locally (pre-commit)
and then in CI. The project needs one tool for each job, configured in
`pyproject.toml`, with versions pinned in `uv.lock`.

## Options considered

Lint and format:

- **black + flake8 + isort:** the long-standing combination; three tools and
  three configurations that occasionally disagree.
- **pylint:** catches more design issues, but is slow and noisy.
- **ruff:** one fast tool that covers linting, import sorting and formatting,
  with rule families ported from flake8 plug-ins, pyupgrade and bandit.

Rule selection for ruff:

- **Defaults** (pyflakes plus a few pycodestyle errors): low friction, but
  misses blocking calls in async code, naive datetimes and security smells,
  all of which matter for this project.
- **All rules minus ignores:** thorough, but noisy, includes rules that
  conflict with each other, and new ruff releases can enable rules that
  suddenly fail CI.
- **A curated list of rule families:** each family is chosen for a reason and
  explained in the configuration.

Type checking:

- **mypy:** the reference type checker; strict mode is a single switch.
- **pyright:** faster, but runs on Node.js.
- **ty:** promising, but still in beta.

Tests:

- **unittest:** in the standard library, but needs class boilerplate and gives
  weaker failure output.
- **pytest:** plain functions with `assert`, fixtures and parametrisation;
  the de facto standard.

## Decision

- **ruff** for linting and formatting, line length 88 (the default), with the
  curated rule families listed in `pyproject.toml`: pycodestyle, pyflakes,
  isort, bugbear, pyupgrade, simplify, naming, bandit, async, datetimez,
  pytest-style and ruff-specific rules. Tests may use `assert` (S101).
- **mypy** with `strict = true`, plus `warn_unreachable` and a required error
  code on every `type: ignore`, checking both `src` and `tests`.
- **pytest** with strict markers and config, strict xfail, and importlib
  import mode, so tests import the installed package rather than relying on
  `sys.path` manipulation.
- All three are in the `dev` dependency group (PEP 735), installed by
  `uv sync` and excluded from the production image with `--no-dev`.

## Consequences

- One command per check: `uv run ruff check`, `uv run ruff format --check`,
  `uv run mypy`, `uv run pytest`. Pre-commit and CI run the same commands.
- Strict typing means every function, tests included, is fully annotated.
  Third-party libraries without type information will need stubs or a
  targeted, coded `type: ignore`.
- Adding a rule family later is a small, reviewable change with its own
  reason.
