# Vicero API — multi-stage build using uv
FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS base

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

# Install dependencies first (cached layer) using the lockfile.
COPY pyproject.toml uv.lock* ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev || uv sync --no-install-project --no-dev

# Copy source and install the project itself.
COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev || true

# Run as a non-root user.
#
# `var/uploads` is created HERE, before the chown, and that ordering is the whole point. In
# production a shared named volume is mounted at this path so `api` and `worker` see the same
# files (docs/15 PROD-1). Docker seeds an empty named volume from the image's directory —
# including its ownership — but if the path does not exist in the image it creates the mountpoint
# as root:root instead, and appuser then cannot write to its own upload directory. That failure
# looks like a permissions bug at the first client upload, i.e. after deploy, not at build time.
RUN mkdir -p /app/var/uploads \
    && useradd -m -u 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

# Migrations run via the compose entrypoint before uvicorn (see docker-compose.yml).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
