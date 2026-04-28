# ── Backend: FastAPI + DuckDB + Guard Model ──────────────────
# Uses uv for fast dependency resolution and installs PyTorch CPU
# to keep the image smaller (no CUDA). Switch the base or add
# --extra-index-url for GPU deployments.

FROM python:3.14-slim AS base

# System deps for building native extensions (asyncpg, pyarrow, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/

WORKDIR /app

# ── Dependency layer (cached unless lock changes) ────────────
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# ── Application code ─────────────────────────────────────────
COPY api.py main.py ./
COPY models/ models/
COPY services/ services/
COPY db/ db/
COPY scripts/ scripts/

# ── Data layer (parquet files — ~1 GB) ───────────────────────
# In production you may want to mount this as a volume instead.
COPY data/ data/

# The guard model (Qwen3Guard-Gen-0.6B) is downloaded at first
# startup by HuggingFace Transformers. Cache it inside the image
# so cold-starts are faster. Set HF_HOME for a predictable path.
ENV HF_HOME=/app/.cache/huggingface
ENV TOKENIZERS_PARALLELISM=false

EXPOSE 8000

# Run with uvicorn. Workers=1 because the guard model holds GPU/
# CPU memory — scale with replicas, not workers.
CMD ["uv", "run", "uvicorn", "api:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--timeout-keep-alive", "120"]
