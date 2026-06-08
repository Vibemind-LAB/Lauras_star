# setup-fetch.ps1 — install the URL-ingest extra (yt-dlp) into Laura's backend venv.
#
# Laura's URL ingest handles plain media links out of the box. To also paste
# "site" links (YouTube / Google Drive / Vimeo / ...), yt-dlp must be installed.
# This installs it into the shared backend venv (services/local-api).
#
# Usage:
#   pwsh ./scripts/setup-fetch.ps1
#
# Requirements:
#   - uv on PATH (https://docs.astral.sh/uv/)
#   - ffmpeg on PATH (or bundled): yt-dlp shells out to ffmpeg to MERGE separate
#     video+audio streams. Without it, adaptive sources fail to merge. Laura
#     resolves ffmpeg from $env:LAURA_FFMPEG first, else PATH.

$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $PSScriptRoot
$apiDir = Join-Path $repoRoot 'services/local-api'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Error "uv is not on PATH. Install it: https://docs.astral.sh/uv/"
    exit 1
}

Write-Host "Installing yt-dlp into the backend venv ($apiDir) ..." -ForegroundColor Cyan
Push-Location $apiDir
try {
    uv pip install "yt-dlp>=2024.8"
    if ($LASTEXITCODE -ne 0) { throw "uv pip install failed (exit $LASTEXITCODE)" }

    $version = uv run --no-sync python -c "import yt_dlp; print(yt_dlp.version.__version__)"
    if ($LASTEXITCODE -ne 0) { throw "yt-dlp import check failed (exit $LASTEXITCODE)" }
    Write-Host "yt-dlp installed: $version" -ForegroundColor Green
}
finally {
    Pop-Location
}

# ffmpeg reminder — required for merging adaptive video+audio streams.
if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host "ffmpeg found on PATH." -ForegroundColor Green
}
elseif ($env:LAURA_FFMPEG) {
    Write-Host "ffmpeg resolved via LAURA_FFMPEG=$($env:LAURA_FFMPEG)." -ForegroundColor Green
}
else {
    Write-Warning "ffmpeg not found on PATH and LAURA_FFMPEG is unset. yt-dlp needs ffmpeg to merge video+audio streams. Install ffmpeg or set LAURA_FFMPEG to its full path."
}

Write-Host ""
Write-Host "Done. Restart the Laura dev app so the backend picks up yt-dlp." -ForegroundColor Cyan
