"""Manual smoke test: boot the real uvicorn server and hit it over HTTP.

    uv run python scripts/smoke_server.py

Proves the production entrypoint path (uvicorn + lifespan + job runner), which the
in-process TestClient does not exercise. Exits non-zero on any failure.
"""

from __future__ import annotations

import sys
import tempfile
import threading
import time
from pathlib import Path

import httpx
import uvicorn

from laura.config import Settings
from laura.main import create_app

PORT = 8771
BASE = f"http://127.0.0.1:{PORT}"


def main() -> int:
    workspace = Path(tempfile.mkdtemp(prefix="laura-smoke-"))
    app = create_app(Settings(workspace_root=workspace, start_runner=True))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    try:
        # wait for startup
        last_exc: Exception | None = None
        for _ in range(100):
            try:
                if httpx.get(f"{BASE}/healthz", timeout=1.0).status_code == 200:
                    break
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                time.sleep(0.1)
        else:
            print("FAIL: server did not become healthy:", last_exc)
            return 1

        health = httpx.get(f"{BASE}/healthz", timeout=2.0).json()
        print("HEALTH:", health)
        assert health["status"] == "ok"
        assert health["schema_version"] >= 1

        created = httpx.post(
            f"{BASE}/projects",
            json={"name": "Smoke", "sequence_rate_num": 30000, "sequence_rate_den": 1001,
                  "drop_frame": True},
            timeout=5.0,
        ).json()
        print("CREATE:", created)
        assert created["id"]
        assert created["drop_frame"] is True

        fetched = httpx.get(f"{BASE}/projects/{created['id']}", timeout=5.0).json()
        assert fetched == created
        print("OK: real-server round-trip succeeded")
        return 0
    finally:
        server.should_exit = True
        thread.join(timeout=5.0)


if __name__ == "__main__":
    sys.exit(main())
