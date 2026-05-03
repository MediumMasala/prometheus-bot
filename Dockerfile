# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Install uv
COPY --from=ghcr.io/astral-sh/uv:0.4.20 /uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN uv venv /opt/venv \
    && . /opt/venv/bin/activate \
    && uv pip install --no-cache .

# ---- runtime ----
FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH"

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -u 1001 -m prom

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY --chown=prom:prom alembic.ini ./alembic.ini
COPY --chown=prom:prom alembic ./alembic
COPY --chown=prom:prom src ./src
COPY --chown=prom:prom seeds ./seeds

USER prom

ENV PORT=8080
EXPOSE 8080

# Render-friendly: run migrations, then start
CMD ["sh", "-c", "alembic upgrade head && python -m prometheus.main"]
