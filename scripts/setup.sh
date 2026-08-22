#!/usr/bin/env bash
# setup.sh — one-command setup for the Laura monorepo.
#
# Checks prerequisites, then installs everything needed to run the backend
# tests and the desktop dev app: `uv sync` in services/local-api and
# services/mcp, `pnpm install` at the workspace root, and a local .env.
# Mirrors what .github/workflows/ci.yml installs (same extras, same
# lockfile-frozen commands) so a clean setup here behaves like CI. Never
# overwrites an existing .env and never strips extras/packages an existing
# venv or node_modules already has (uv sync runs with --inexact for this
# reason — a bare `uv sync --extra X` REMOVES anything not in the given
# extra set, which is not what "setup" should do to a machine that already
# has more installed).
#
# Usage:
#   ./scripts/setup.sh                     # check + install + smoke-verify
#   ./scripts/setup.sh --check             # prerequisite check only, no writes
#   ./scripts/setup.sh --extras "a,b"      # override the local-api extras
#                                           # (default: scene,otel,autoshort — the CI set)
#   ./scripts/setup.sh --with-tts-sidecar  # also print TTS-sidecar setup instructions
#
# Requirements (checked below, with install URLs printed if missing):
#   - Python >=3.11    https://www.python.org/downloads/
#   - uv               https://docs.astral.sh/uv/getting-started/installation/
#   - Node >=22        https://nodejs.org/en/download
#   - pnpm             https://pnpm.io/installation
#   - ffmpeg + ffprobe https://ffmpeg.org/download.html
#
# Exit codes: 0 ok, 1 prerequisite missing, 2 install/verify failure.
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
api_dir="$repo_root/services/local-api"
mcp_dir="$repo_root/services/mcp"

check_only=0
extras="scene,otel,autoshort"
with_tts_sidecar=0

while [ $# -gt 0 ]; do
    case "$1" in
        --check)
            check_only=1
            shift
            ;;
        --extras)
            if [ $# -lt 2 ]; then
                echo "error: --extras requires a value (e.g. --extras \"scene,otel\")" >&2
                exit 1
            fi
            extras="$2"
            shift 2
            ;;
        --with-tts-sidecar)
            with_tts_sidecar=1
            shift
            ;;
        -h|--help)
            sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
            exit 0
            ;;
        *)
            echo "error: unknown argument: $1 (see --help)" >&2
            exit 1
            ;;
    esac
done

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

version_ge() {
    # version_ge <found> <required> -> true if found >= required (dotted numeric)
    local found="$1" required="$2"
    local -a f r
    IFS='.' read -r -a f <<< "$found"
    IFS='.' read -r -a r <<< "$required"
    local i n fi ri
    n=${#r[@]}
    for ((i = 0; i < n; i++)); do
        fi="${f[i]:-0}"; fi="${fi%%[!0-9]*}"; fi="${fi:-0}"
        ri="${r[i]:-0}"; ri="${ri%%[!0-9]*}"; ri="${ri:-0}"
        if ((10#$fi > 10#$ri)); then return 0; fi
        if ((10#$fi < 10#$ri)); then return 1; fi
    done
    return 0
}

# Rows collected during the prereq check, printed as a table.
declare -a row_name row_found row_required row_status row_url
missing_lines=()

add_row() {
    row_name+=("$1"); row_found+=("$2"); row_required+=("$3"); row_status+=("$4"); row_url+=("$5")
    if [ "$4" != "ok" ]; then
        missing_lines+=("  - $1: found '$2', need $3 -> $5")
    fi
}

print_table() {
    printf '\n%-10s %-30s %-10s %-8s\n' "NAME" "FOUND" "REQUIRED" "STATUS"
    printf -- '-%.0s' {1..62}; printf '\n'
    local i
    for i in "${!row_name[@]}"; do
        printf '%-10s %-30s %-10s %-8s\n' "${row_name[i]}" "${row_found[i]}" "${row_required[i]}" "${row_status[i]}"
    done
    printf '\n'
}

check_python() {
    local cmd="" ver=""
    if command -v python3 >/dev/null 2>&1; then
        cmd=python3
    elif command -v python >/dev/null 2>&1; then
        cmd=python
    fi
    if [ -z "$cmd" ]; then
        add_row "python" "missing" ">=3.11" "missing" "https://www.python.org/downloads/"
        return
    fi
    ver="$("$cmd" --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
    [ -z "$ver" ] && ver="unknown"
    if [ "$ver" != "unknown" ] && version_ge "$ver" "3.11.0"; then
        add_row "python" "$ver" ">=3.11" "ok" "https://www.python.org/downloads/"
    else
        add_row "python" "$ver" ">=3.11" "missing" "https://www.python.org/downloads/"
    fi
}

check_uv() {
    if ! command -v uv >/dev/null 2>&1; then
        add_row "uv" "missing" "any" "missing" "https://docs.astral.sh/uv/getting-started/installation/"
        return
    fi
    local ver
    ver="$(uv --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
    [ -z "$ver" ] && ver="unknown"
    add_row "uv" "$ver" "any" "ok" "https://docs.astral.sh/uv/getting-started/installation/"
}

check_node() {
    if ! command -v node >/dev/null 2>&1; then
        add_row "node" "missing" ">=22" "missing" "https://nodejs.org/en/download"
        return
    fi
    local ver
    ver="$(node --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
    [ -z "$ver" ] && ver="unknown"
    if [ "$ver" != "unknown" ] && version_ge "$ver" "22.0.0"; then
        add_row "node" "$ver" ">=22" "ok" "https://nodejs.org/en/download"
    else
        add_row "node" "$ver" ">=22" "missing" "https://nodejs.org/en/download"
    fi
}

check_pnpm() {
    if ! command -v pnpm >/dev/null 2>&1; then
        add_row "pnpm" "missing" "any" "missing" "https://pnpm.io/installation"
        return
    fi
    local ver
    ver="$(pnpm --version 2>&1 | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1)"
    [ -z "$ver" ] && ver="unknown"
    add_row "pnpm" "$ver" "any" "ok" "https://pnpm.io/installation"
}

check_ffmpeg() {
    if ! command -v ffmpeg >/dev/null 2>&1; then
        add_row "ffmpeg" "missing" "any" "missing" "https://ffmpeg.org/download.html"
        return
    fi
    local ver
    ver="$(ffmpeg -version 2>&1 | head -n1 | awk '{print $3}')"
    [ -z "$ver" ] && ver="unknown"
    add_row "ffmpeg" "$ver" "any" "ok" "https://ffmpeg.org/download.html"
}

check_ffprobe() {
    if ! command -v ffprobe >/dev/null 2>&1; then
        add_row "ffprobe" "missing" "any" "missing" "https://ffmpeg.org/download.html"
        return
    fi
    local ver
    ver="$(ffprobe -version 2>&1 | head -n1 | awk '{print $3}')"
    [ -z "$ver" ] && ver="unknown"
    add_row "ffprobe" "$ver" "any" "ok" "https://ffmpeg.org/download.html"
}

run_prereq_check() {
    check_python
    check_uv
    check_node
    check_pnpm
    check_ffmpeg
    check_ffprobe
    print_table
    if [ "${#missing_lines[@]}" -gt 0 ]; then
        echo "Missing/failing prerequisites:"
        printf '%s\n' "${missing_lines[@]}"
        echo ""
        return 1
    fi
    echo "All prerequisites ok."
    return 0
}

print_tts_sidecar_instructions() {
    cat <<'EOS'

TTS sidecar (optional — NOT installed by this script)
-------------------------------------------------------
The Chatterbox TTS sidecar needs its own Python venv (torch + chatterbox-tts),
separate from services/local-api's venv, and is not installable from here.

  1. Create a separate venv and install chatterbox-tts + its deps into it.
  2. Run the sidecar from that venv:
       python services/tts-sidecar/chatterbox_sidecar.py --port 8898
  3. Point Laura at it (e.g. in .env):
       LAURA_VOICEOVER_BACKEND=sidecar
       LAURA_VOICEOVER_URL=http://127.0.0.1:8898

Full HTTP contract, env vars (CHATTERBOX_VOICE_REF, HF_HOME, HF_HUB_OFFLINE,
CHATTERBOX_DEVICE, ...), and the setuptools<81 gotcha are documented in:
  services/tts-sidecar/README.md
EOS
}

# ---------------------------------------------------------------------------
# 1) prerequisite check (always runs, both modes)
# ---------------------------------------------------------------------------
echo "Laura setup - prerequisite check"
if ! run_prereq_check; then
    echo "error: install the missing prerequisites above, then re-run this script." >&2
    exit 1
fi

if [ "$check_only" -eq 1 ]; then
    if [ "$with_tts_sidecar" -eq 1 ]; then
        print_tts_sidecar_instructions
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# 2) install steps
# ---------------------------------------------------------------------------
step_log="$(mktemp)"
trap 'rm -f "$step_log"' EXIT

declare -a step_name step_status

run_step() {
    local label="$1" detail="$2" fn="$3"
    if "$fn" >"$step_log" 2>&1; then
        step_name+=("$label"); step_status+=("ok")
        if [ -n "$detail" ]; then echo "[ok] $label - $detail"; else echo "[ok] $label"; fi
    else
        step_name+=("$label"); step_status+=("FAIL")
        echo "[FAIL] $label"
        echo "----- output (last 40 lines) -----"
        tail -n 40 "$step_log"
        echo "-----------------------------------"
    fi
}

step_local_api_sync() {
    local extra_args=()
    if [ -n "$extras" ]; then
        local IFS=','
        local e
        for e in $extras; do
            [ -n "$e" ] && extra_args+=(--extra "$e")
        done
    fi
    # --inexact: add/update the requested extras without uninstalling anything
    # else already present in this venv (a plain `uv sync --extra X` makes the
    # venv match ONLY the given extras and removes everything else).
    (cd "$api_dir" && uv sync --frozen --inexact "${extra_args[@]}")
}

step_mcp_sync() {
    (cd "$mcp_dir" && uv sync --frozen --inexact)
}

step_pnpm_install() {
    (cd "$repo_root" && pnpm install --frozen-lockfile)
}

step_dotenv() {
    if [ -f "$repo_root/.env" ]; then
        return 0
    fi
    if [ ! -f "$repo_root/.env.example" ]; then
        echo "error: .env.example not found at $repo_root/.env.example" >&2
        return 1
    fi
    cp "$repo_root/.env.example" "$repo_root/.env"
}

echo ""
echo "Installing..."
run_step "uv sync (services/local-api)" "extras: ${extras:-none}" step_local_api_sync
run_step "uv sync (services/mcp)" "" step_mcp_sync
run_step "pnpm install (workspace root)" "" step_pnpm_install

if [ -f "$repo_root/.env" ]; then
    env_detail="kept existing .env (not overwritten)"
else
    env_detail="created .env from .env.example"
fi
run_step ".env" "$env_detail" step_dotenv

install_failed=0
for s in "${step_status[@]}"; do
    [ "$s" = "FAIL" ] && install_failed=1
done

if [ "$install_failed" -eq 1 ]; then
    echo ""
    echo "error: install failed - see output above. Fix the issue(s) and re-run." >&2
    if [ "$with_tts_sidecar" -eq 1 ]; then print_tts_sidecar_instructions; fi
    exit 2
fi

# ---------------------------------------------------------------------------
# 3) verify (fast smoke checks)
# ---------------------------------------------------------------------------
echo ""
echo "Verifying..."

declare -a verify_name verify_status

verify_backend_tests() {
    # Real fast subset: the time-core unit tests (frame/sample/DF-NDF/range
    # invariants — CLAUDE.md's non-negotiable rules). No DB/network/GPU deps,
    # ~40 tests, <2s. NOT the full suite.
    (cd "$api_dir" && uv run pytest -q -x tests/test_timecode.py tests/test_ranges.py)
}

verify_app_factory() {
    (cd "$api_dir" && uv run python -c "from laura.main import create_app; create_app()")
}

run_verify() {
    local label="$1" fn="$2"
    if "$fn" >"$step_log" 2>&1; then
        verify_name+=("$label"); verify_status+=("pass")
        echo "[pass] $label"
    else
        verify_name+=("$label"); verify_status+=("FAIL")
        echo "[FAIL] $label"
        echo "----- output (last 40 lines) -----"
        tail -n 40 "$step_log"
        echo "-----------------------------------"
    fi
}

run_verify "backend fast tests (time/range core: test_timecode.py, test_ranges.py)" verify_backend_tests
run_verify "app factory import (laura.main:create_app)" verify_app_factory

verify_failed=0
for s in "${verify_status[@]}"; do
    [ "$s" = "FAIL" ] && verify_failed=1
done

# ---------------------------------------------------------------------------
# 4) summary
# ---------------------------------------------------------------------------
echo ""
echo "=================== Summary ==================="
echo "Prerequisites: ok"
for i in "${!step_name[@]}"; do
    echo "Install  [${step_status[i]}] ${step_name[i]}"
done
for i in "${!verify_name[@]}"; do
    echo "Verify   [${verify_status[i]}] ${verify_name[i]}"
done
echo "================================================="

if [ "$with_tts_sidecar" -eq 1 ]; then
    print_tts_sidecar_instructions
fi

if [ "$verify_failed" -eq 1 ]; then
    echo ""
    echo "error: setup finished but verification failed - see output above." >&2
    exit 2
fi

echo ""
echo "Setup complete. Next steps:"
echo "  Backend dev server:   cd services/local-api && uv run laura-api   # http://127.0.0.1:8765"
echo "  Full backend tests:   cd services/local-api && uv run pytest"
echo "  Desktop dev app:      pnpm dev                                   # or: cd apps/desktop && pnpm dev"
echo ""
exit 0
