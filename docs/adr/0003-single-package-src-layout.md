# 0003. Single `guildhall` package in a src layout

Status: Accepted (2026-11-07)

## Context

The design spec's first repository layout placed `platform/`, `plugins/` and
`api/` at the repository root as separate top-level packages. A top-level
package named `platform` shadows the standard library's `platform` module
whenever the repository root is on `sys.path`, which breaks any library that
imports it (pytest, boto3 and httpx among them). `api` and `plugins` are
generic enough to collide with other installed distributions.

## Options considered

- **Rename and keep separate top-level packages** (`core/`, `plugins/`,
  `api/`): stays close to the original layout and fixes the `platform` clash,
  but leaves several generic top-level names.
- **One `guildhall` package, flat layout** (`guildhall/` at the root):
  removes all name clashes, but the package is importable from the
  repository root even when packaging is misconfigured, so a broken package
  can pass tests locally and fail in the container image.
- **One `guildhall` package, src layout** (`src/guildhall/`): removes all
  name clashes, and the package is importable only once installed, so tests
  run against the same installed package the image runs. This is the layout
  the Python Packaging Authority recommends.

Build backend (needed to install the project into its virtual environment):

- **uv_build:** uv's own backend and its default; zero configuration for a
  src layout with a single package.
- **hatchling:** more configurable, which this project does not need.

## Decision

- All Python code lives under `src/guildhall/`: `guildhall.platform`,
  `guildhall.plugins.<name>` and `guildhall.api`. Within the package,
  `platform` is safe because imports are absolute.
- Non-Python parts (`tests/`, `migrations/`, `k8s/`, `infra/`, `docs/`) stay
  at the repository root.
- The build backend is `uv_build`; `uv sync` installs the project in
  editable mode, so code changes take effect without reinstalling.
- Subpackages are created when they get their first code, not as empty
  placeholders.

## Consequences

- Import paths are one level longer, e.g.
  `from guildhall.platform.dedupe import ...`.
- Packaging mistakes surface in local tests rather than in the container.
- The spec's repository layout is updated to match.
