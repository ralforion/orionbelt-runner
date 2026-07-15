# syntax=docker/dockerfile:1

# ── Build stage ───────────────────────────────────────────────────────────────
# Resolve dependencies and install the package into a self-contained venv with
# uv. Kept separate from the runtime image so build tooling never ships.
FROM python:3.14-slim AS build

# uv: fast, reproducible installs. Copied from the official distroless image.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install dependencies first (cached layer) using only the manifest, so source
# edits don't bust the dependency cache. uv.lock is .gitignored, so the build
# resolves from pyproject.toml rather than requiring a committed lock.
COPY pyproject.toml README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-install-project --no-dev

# Now install the project itself. --no-editable copies the package into the
# venv (rather than linking to /app/src), so the runtime stage needs only .venv.
COPY src/ ./src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable

# ── Runtime stage ─────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

# Run as a non-root user; reports are written under the working dir.
RUN useradd --create-home --uid 1000 runner

# Bring over the resolved virtualenv from the build stage.
COPY --from=build --chown=runner:runner /app/.venv /app/.venv

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    # Default OBSL endpoint; override at `docker run` time.
    OBSL_BASE_URL=http://localhost:8080

USER runner
WORKDIR /work

# Mount specs in and reports out, e.g.:
#   docker run --rm -v "$PWD/examples:/work/examples" -v "$PWD/reports:/work/reports" \
#     ralforion/orionbelt-runner run examples/monthly-revenue.yaml
ENTRYPOINT ["orionbelt-runner"]
CMD ["--help"]
