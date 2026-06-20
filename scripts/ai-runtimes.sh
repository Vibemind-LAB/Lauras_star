#!/usr/bin/env bash
set -euo pipefail

action="${1:-up}"
gpu="${2:-}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
compose_file="$repo_root/deploy/ai-runtimes/docker-compose.yml"
gpu_compose_file="$repo_root/deploy/ai-runtimes/docker-compose.gpu.yml"

compose_args=(-f "$compose_file")
if [[ "$gpu" == "--gpu" ]]; then
  compose_args+=(-f "$gpu_compose_file")
fi

case "$action" in
  build)
    docker compose "${compose_args[@]}" build
    ;;
  up)
    docker compose "${compose_args[@]}" up -d --build
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
    echo "usage: scripts/ai-runtimes.sh [build|up|down|ps|logs|health] [--gpu]" >&2
    exit 2
    ;;
esac
