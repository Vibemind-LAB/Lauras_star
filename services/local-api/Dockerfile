# Laura API/worker image (server / on-prem mode — docs/14-enterprise.md).
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1

# ffmpeg/ffprobe are required for ingest, proxy, audio, and export.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# uv for fast, reproducible installs.
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY src ./src

# Install locked deps (no dev). Add --extra scene/asr/diarize/server as needed.
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH" \
    LAURA_HOST=0.0.0.0 \
    LAURA_PORT=8765 \
    LAURA_WORKSPACE=/data/workspace

EXPOSE 8765
VOLUME ["/data"]

# Bind to all interfaces inside the container; front with a reverse proxy + TLS.
CMD ["laura-api"]
