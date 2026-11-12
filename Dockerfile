# syntax=docker/dockerfile:1
# Backend image for the industrial-manual RAG service.
#
# Build context is the repository root. The image contains the Python package and
# its runtime dependencies only; the Qdrant vector store and the knowledge-base
# data volume are supplied at run time (see docker-compose.yml).

FROM python:3.11-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# PyMuPDF wheels ship their own libraries, but the slim image lacks a few
# shared objects the PDF stack links against at import time.
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        curl \
        libgl1 \
        libglib2.0-0 \
        libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only the packaging metadata first so dependency layers cache well.
COPY pyproject.toml README.md ./
COPY src/industrial_rag/__init__.py ./src/industrial_rag/__init__.py

# Install runtime dependencies without the editable package, then the package.
RUN python -m pip install --upgrade pip \
    && python -m pip install \
        "lightrag-hku==1.5.4" \
        "openai>=1.97,<3" \
        "PyMuPDF>=1.24,<2" \
        "python-dotenv>=1.0,<2" \
        "networkx>=3.2,<4" \
        "pyvis>=0.3.2,<0.4" \
        "httpx>=0.28,<1" \
        "fastapi>=0.115,<1" \
        "uvicorn>=0.30,<1" \
        "sqlalchemy[asyncio]>=2.0,<3" \
        "aiosqlite>=0.20,<1" \
        "alembic>=1.13,<2" \
        "qdrant-client==1.13.3" \
        "pydantic>=2,<3" \
        "starlette>=0.40,<1" \
        "python-multipart>=0.0.9,<1"

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./alembic.ini
COPY scripts ./scripts
# The acceptance job reads the frozen suite and the approval registry.
COPY evaluation ./evaluation
COPY tools ./tools

RUN python -m pip install --no-deps -e .

# Non-root runtime user; the data volume is chowned before dropping privileges.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /app/lightrag_storage /app/data/db /app/artifacts \
    && chown -R appuser:appuser /app

USER appuser

ENV PYTHONPATH=/app/src \
    LIGHTRAG_WORKING_DIR=/app/lightrag_storage \
    VALIDATION_ARTIFACT_DIR=/app/artifacts/validation-runs

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

# Migrations run at start-up because the SQLite file lives on a mounted volume
# that may be empty on first boot.
CMD ["sh", "-c", "python -m alembic upgrade head && exec python -m uvicorn industrial_rag.api:app --host 0.0.0.0 --port 8000 --no-proxy-headers"]
