#!/usr/bin/env bash
# setup-fetch.sh — install the URL-ingest extra (yt-dlp) into Laura's backend venv.
#
# Laura's URL ingest handles plain media links out of the box. To also paste
# "site" links (YouTube / Google Drive / Vimeo / ...), yt-dlp must be installed.
# This installs it into the shared backend venv (services/local-api).
#
# Usage:
#   ./scripts/setup-fetch.sh
#
# Requirements:
#   - uv on PATH (https://docs.astral.sh/uv/)
#   - ffmpeg on PATH (or bundled): yt-dlp shells out to ffmpeg to MERGE separate
#     video+audio streams. Without it, adaptive sources fail to merge. Laura
#     resolves ffmpeg from $LAURA_FFMPEG first, else PATH.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
api_dir="$repo_root/services/local-api"

if ! command -v uv >/dev/null 2>&1; then
    echo "error: uv is not on PATH. Install it: https://docs.astral.sh/uv/" >&2
    exit 1
fi

echo "Installing yt-dlp into the backend venv ($api_dir) ..."
cd "$api_dir"
uv pip install "yt-dlp>=2024.8"

version="$(uv run --no-sync python -c 'import yt_dlp; print(yt_dlp.version.__version__)')"
echo "yt-dlp installed: $version"

# ffmpeg reminder — required for merging adaptive video+audio streams.
if command -v ffmpeg >/dev/null 2>&1; then
    echo "ffmpeg found on PATH."
elif [ -n "${LAURA_FFMPEG:-}" ]; then
    echo "ffmpeg resolved via LAURA_FFMPEG=$LAURA_FFMPEG."
else
    echo "warning: ffmpeg not found on PATH and LAURA_FFMPEG is unset." >&2
    echo "         yt-dlp needs ffmpeg to merge video+audio streams. Install ffmpeg or set LAURA_FFMPEG." >&2
fi

echo ""
echo "Done. Restart the Laura dev app so the backend picks up yt-dlp."
