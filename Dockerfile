# ── Backend: FastAPI + Supabase Postgres + LLM agent ──────────
# Multi-stage build with uv. Final image runs as a non-root user,
# bundles only the resolved venv + application code, and exposes
# /api/health for orchestrator probes.

# ─────────────────────────────────────────────────────────────
# Stage 1: build venv (cached unless lockfile changes)
# ─────────────────────────────────────────────────────────────
FROM python:3.14-slim AS deps

# Build-time system libs needed to compile asyncpg / pyarrow / torch wheels
# that don't ship binaries for every platform.
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# uv from the official image — fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# Lockfile + project metadata only — keeps this layer cached when source
# changes but deps don't. --no-install-project: we don't ship as a package.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project


# ─────────────────────────────────────────────────────────────
# Stage 2: runtime — slim, non-root, health-checked
# ─────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

# Runtime libs only (no compilers). curl for HEALTHCHECK.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libpq5 \
        curl \
    && rm -rf /var/lib/apt/lists/*

# Non-root user
RUN groupadd --system --gid 1001 app \
 && useradd  --system --uid 1001 --gid app --no-create-home app

WORKDIR /app

# Pull the pre-built virtualenv from the deps stage and put its bin on PATH.
COPY --from=deps /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Application source — only what FastAPI imports at runtime.
# scripts/ stays in so `python -m scripts.ingest` still works inside the
# container (e.g. via `docker exec` for one-off backfills).
COPY --chown=app:app api.py ./
COPY --chown=app:app db/       db/
COPY --chown=app:app models/   models/
COPY --chown=app:app services/ services/
COPY --chown=app:app scripts/  scripts/

# HF Transformers caches Qwen3Guard locally on first launch — fix the path
# so volume mounts can persist it across container restarts if desired.
ENV HF_HOME=/app/.cache/huggingface
ENV TOKENIZERS_PARALLELISM=false
# Don't buffer stdout — logs surface immediately under docker logs.
ENV PYTHONUNBUFFERED=1

USER app

EXPOSE 8000

# Liveness probe — uses the cheap /api/health endpoint. start-period
# allows the safety guard model to load before the first probe runs.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/api/health || exit 1

# Workers=1: the local guard model and chat client hold per-process state;
# scale horizontally with replicas, not workers.
CMD ["uvicorn", "api:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "120"]
