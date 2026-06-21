#!/usr/bin/env bash
set -euo pipefail

action="${1:-up}"
mode="${2:-smoke}"
gpu="${3:-}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$repo_root/deploy/ai-runtimes/docker-compose.yml"
gpu_compose_file="$repo_root/deploy/ai-runtimes/docker-compose.gpu.yml"
models_compose_file="$repo_root/deploy/ai-runtimes/docker-compose.models.yml"

default_model_root() {
  if [[ -d /e ]]; then
    echo "/e/Laura/models"
  elif [[ -d /mnt/e ]]; then
    echo "/mnt/e/Laura/models"
  else
    echo "$repo_root/workspace/models"
  fi
}

default_runtime_workspace() {
  if [[ -d /e ]]; then
    echo "/e/Laura/ai-runtime"
  elif [[ -d /mnt/e ]]; then
    echo "/mnt/e/Laura/ai-runtime"
  else
    echo "$repo_root/workspace/ai-runtime"
  fi
}

if [[ "$mode" != "smoke" && "$mode" != "model" ]]; then
  echo "mode must be smoke or model" >&2
  exit 2
fi

export LAURA_RUNTIME_MODE="$mode"
export LAURA_RUNTIME_WORKSPACE="${LAURA_RUNTIME_WORKSPACE:-$(default_runtime_workspace)}"
export LAURA_MODELS_ROOT="${LAURA_MODELS_ROOT:-$(default_model_root)}"
mkdir -p "$LAURA_RUNTIME_WORKSPACE" "$LAURA_MODELS_ROOT"

compose_args=(-f "$compose_file")
if [[ "$mode" == "model" ]]; then
  compose_args+=(-f "$models_compose_file")
fi
if [[ "$gpu" == "--gpu" || "$gpu" == "gpu" ]]; then
  compose_args+=(-f "$gpu_compose_file")
fi

case "$action" in
  build)
    docker compose "${compose_args[@]}" build
    ;;
  up)
    docker compose "${compose_args[@]}" up -d
    ;;
  down)
    docker compose "${compose_args[@]}" down
    ;;
  ps)
    docker compose "${compose_args[@]}" ps
    ;;
  logs)
    docker compose "${compose_args[@]}" logs --tail 100
    ;;
  health)
    python - <<'PY'
import json
import sys
import urllib.request

for port in (8898, 8899, 8901):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=5) as response:
        sys.stdout.write(json.dumps(json.loads(response.read().decode()), sort_keys=True) + "\n")
PY
    ;;
  *)
    echo "usage: scripts/ai-runtimes.sh [build|up|down|ps|logs|health] [smoke|model] [--gpu]" >&2
    exit 2
    ;;
esac
