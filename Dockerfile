# The Guildhall image (docs/adr/0024): one image, run as the worker for now.
# Two stages: `build` installs the locked dependencies and the project into a
# virtual environment with uv; `runtime` copies only that environment, so uv,
# its cache and the source tree stay out of the shipped image.

# --- build ---------------------------------------------------------------
FROM python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS build

# The uv binary, from uv's own image at the version used locally.
COPY --from=ghcr.io/astral-sh/uv:0.13.7@sha256:96f40d3904aad8d0bd010539001d36c8e2188b981e1334471a90c4a1d93bb490 /uv /usr/local/bin/uv

# Compile .pyc files now so containers start faster; copy files out of uv's
# cache (a separate mount, where hard links are impossible); use this image's
# Python and never download another.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Dependencies first, from the lockfile alone: this layer is rebuilt only when
# pyproject.toml or uv.lock change, not on every code change. The cache mount
# keeps downloaded wheels between builds without putting them in a layer.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

# Then the project itself, installed as a regular package (not editable), so
# the runtime stage needs the environment only, not the source tree.
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- runtime -------------------------------------------------------------
# The same base as `build`, so the environment's link to Python stays valid.
FROM python:3.14-slim-trixie@sha256:9aaa474097d094040649f2162a627c0049b408f52138d980916788d80f04802f AS runtime

# An unprivileged user with fixed numeric IDs; Kubernetes can only verify
# runAsNonRoot against a numeric user.
RUN groupadd --system --gid 10001 guildhall \
    && useradd --system --uid 10001 --gid guildhall --no-create-home guildhall

WORKDIR /app

# Owned by root and only readable by the app user, so a compromised process
# cannot change its own code. Alembic reads its settings from pyproject.toml.
COPY --from=build /app/.venv /app/.venv
COPY pyproject.toml ./
COPY migrations ./migrations

ENV PATH="/app/.venv/bin:$PATH"

USER 10001:10001

# Exec form: guildhall-worker runs as PID 1 with no shell in between, so it
# receives SIGTERM itself and drains (docs/adr/0023).
CMD ["guildhall-worker"]
