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

# Install locked deps (no dev).
#
# `scene` und `otel` sind KEINE Kuer: `laura.main` importiert `analysis.handlers`,
# das `analysis/quality.py` zieht, und das importiert `numpy` ungeschuetzt auf
# Modulebene. numpy kommt hier ueber `scene` (opencv-python) herein. Ohne die Extras
# baut dieses Dockerfile ein Image, das beim Start sofort mit
# `ModuleNotFoundError: No module named 'numpy'` stirbt -- am 11.09. genau so
# passiert, als das Image zum ersten Mal seit laengerem neu gebaut wurde.
# Dieselbe Kombination fahrt die CI (.github/workflows/ci.yml).
#
# Weitere Extras (asr/diarize/align/autoshort) bleiben draussen: sie ziehen torch
# und Modelle und gehoeren in die Sidecars, nicht in die API.
RUN uv sync --frozen --no-dev --extra scene --extra otel

ENV PATH="/app/.venv/bin:$PATH" \
    LAURA_HOST=0.0.0.0 \
    LAURA_PORT=8765 \
    LAURA_WORKSPACE=/data/workspace

EXPOSE 8765
VOLUME ["/data"]

# Bind to all interfaces inside the container; front with a reverse proxy + TLS.
CMD ["laura-api"]
