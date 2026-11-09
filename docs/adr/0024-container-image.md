# 0024. Container image: two-stage uv build on python:3.14-slim

Status: Accepted (2026-11-09)

## Context

The spec runs one image everywhere: compose now, kind and EKS in M3. The worker
is its first command; the api joins in M2 as another command on the same
image. The image must run Python 3.14 with the locked dependencies, contain no
secrets or local files from a public repository's working tree, receive
SIGTERM directly so the worker can drain (ADR 0023), and run without root.

## Options considered

- **`python:3.14-slim-trixie`:** the official Debian slim image, with the same
  Python release as development; every dependency installs from prebuilt
  wheels. Not the smallest.
- **`python:3.14-alpine`:** about a third of the size, but musl libc: packages
  without musl wheels compile from source, and behaviour differs in places.
- **Distroless:** no shell and a small attack surface, but its Python is
  Debian's 3.11.
- **uv's own images:** Debian slim with uv installed; in a single stage uv
  ships in the runtime image, and in two stages the result equals the slim
  option.

## Decision

`python:3.14-slim-trixie`, in two stages, following uv's Docker guidance:

- **build:** copies in the uv binary at the locally used version, installs the
  locked runtime dependencies first (a layer that changes only with
  `pyproject.toml` or `uv.lock`), then the project as a regular package
  (`--no-editable`). Bytecode is compiled at build time, uv's download cache is
  a build cache mount, and uv never downloads a Python.
- **runtime:** the same base, so the environment's link to Python stays valid;
  copies the virtual environment, plus `migrations/` and `pyproject.toml` for
  Alembic. Files are owned by root and the process runs as `10001:10001`, a
  numeric user so Kubernetes can verify `runAsNonRoot`.
- `CMD ["guildhall-worker"]` in exec form: the worker is PID 1 with no shell in
  between. It installs its own SIGTERM handler, which PID 1 needs, and starts
  no child processes, so no init process is needed.
- Base images are pinned by tag and digest, kept current by Dependabot's
  `docker` ecosystem.
- `.dockerignore` is an allowlist: only `pyproject.toml`, `uv.lock`, `src/` and
  `migrations/` enter the build context.

## Consequences

- The image is about 345 MB: 181 MB base and a 124 MB environment. botocore's
  data for 438 AWS services is 27 MB of it; trimming it is possible later.
- Code changes rebuild only the last layers; dependency changes reinstall
  everything from the warm cache.
- The runtime image has no uv, so commands run as `guildhall-worker` or
  `alembic` directly, never through `uv run`.
- The first build showed a runtime import of a type-stub package, which is a
  dev dependency and absent from the image. Type-only imports now go under
  `if TYPE_CHECKING:`, and deptry checks for this class of mistake
  ([ADR 0025](0025-dependency-check-with-deptry.md)).
