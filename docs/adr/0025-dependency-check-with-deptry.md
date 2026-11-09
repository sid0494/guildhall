# 0025. Check production imports against declared dependencies with deptry

Status: Accepted (2026-11-09)

## Context

The first container image crashed at start-up: `guildhall.platform.queue`
imported `types_aiobotocore_sqs`, a type-stub package declared as a dev
dependency. Development environments install dev dependencies, so neither the
tests nor the type checker noticed; the image installs runtime dependencies
only, as every deployed environment will. The fix needs a check that would
have caught it, and that keeps catching the whole class of mistake.

## Options considered

- **A unit test that parses imports:** walk the package, map each import to a
  distribution and compare with `pyproject.toml`. Reimplements an existing
  tool, including the awkward import-to-distribution mapping.
- **Import every module inside the built image in CI:** tests the real
  artefact, but only modules that are imported, only after a slow image build,
  and not before a commit.
- **deptry:** a static check that compares the imports in the code with the
  dependencies in `pyproject.toml`. It flags production code importing a dev
  or undeclared dependency, and declared dependencies that nothing uses. Fast
  enough for pre-commit.

## Decision

deptry, as a dev dependency, run by pre-commit (and so by CI) as
`deptry src migrations`: production code and migrations, not tests, which use
dev dependencies by design. `psycopg` is ignored for "declared but unused",
because SQLAlchemy loads the driver from the connection URL.

Type-only imports of dev dependencies go under `if TYPE_CHECKING:`; deptry
does not count them, and Python 3.14 evaluates annotations lazily, so no
quoting is needed.

## Consequences

- A runtime import of a dev-only package fails before the commit, not at
  container start-up.
- A dependency that is no longer used is reported, so `pyproject.toml` stays
  accurate.
- Packages loaded by name at runtime (drivers, plug-in entry points) need an
  explicit ignore with a reason.
