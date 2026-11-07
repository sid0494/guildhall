# 0002. Target Python 3.14 and manage dependencies with uv

Status: Accepted (2026-11-07)

## Context

The project needs one Python version used everywhere (local development, CI
and the container image) and a tool that manages the virtual environment,
installs dependencies and produces a lockfile. The lockfile pins the exact
version and hash of every direct and transitive dependency, so every
environment installs the same packages and an upstream release cannot break
the build overnight.

## Options considered

Package manager:

- **uv:** a single binary that manages virtual environments, dependencies, a
  cross-platform lockfile (`uv.lock`) and Python installations. Very fast,
  which shortens CI runs and container builds. Uses standard `pyproject.toml`
  metadata (PEP 621). Newer, and maintained by a single company.
- **Poetry:** mature and widely used, with its own resolver and
  `poetry.lock`. Noticeably slower, does not install Python versions, and
  only recently adopted standard project metadata.
- **pip-tools:** `pip-compile` produces a pinned, hashed `requirements.txt`.
  Minimal, but virtual environments and Python versions need separate tools,
  and a lock is only valid for one platform and Python version.

Python version:

- **3.13:** the most conservative choice, but gives up 3.14's improvements
  with no compatibility benefit for this project's dependencies.
- **3.14:** released October 2025; the libraries this project expects to use
  publish wheels for it, and an official slim container image exists.
- **3.15:** the newest release; libraries with compiled extensions may still
  lack wheels for it.

## Decision

- Python 3.14, pinned in `.python-version` and in `requires-python` in
  `pyproject.toml`.
- uv manages the virtual environment, dependencies and Python installation.
  `uv.lock` is committed and is the single source of exact versions.
- CI and the container image install with `uv sync --locked`, which fails if
  the lockfile is out of date with `pyproject.toml` instead of silently
  re-resolving.

## Consequences

- Contributors need only uv; it installs the right Python itself.
- Dependency changes always produce a `uv.lock` diff, which is reviewed with
  the change.
- Project metadata is standard, so moving to another tool later means
  replacing the lockfile, not rewriting the project definition.
- Moving to Python 3.15 is a separate change once its wheel coverage is
  confirmed.
