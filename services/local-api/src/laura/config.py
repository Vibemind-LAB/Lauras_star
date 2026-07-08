"""Service configuration (docs/04-api.md). Local-first defaults; bind to loopback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765

# How many parent directories _load_dotenv climbs looking for a .env file.
_DOTENV_SEARCH_DEPTH = 4


def _load_dotenv(start: Path | None = None) -> None:
    """Load the nearest ``.env`` (cwd upward) into ``os.environ`` — real env always wins.

    Stdlib-only ``KEY=value`` parser (comments/blank lines skipped, surrounding quotes
    stripped); values are applied via ``setdefault`` so explicitly set environment variables
    are never overridden. Missing file is a no-op. Searching upward lets the repo-root
    ``.env`` cover dev runs whose cwd is ``services/local-api`` (the desktop spawner).
    """
    directory = (start or Path.cwd()).resolve()
    for _ in range(_DOTENV_SEARCH_DEPTH):
        candidate = directory / ".env"
        if candidate.is_file():
            for line in candidate.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, _, value = stripped.partition("=")
                key = key.strip()
                value = value.strip().strip("'\"")
                if key:
                    os.environ.setdefault(key, value)
            return
        if directory.parent == directory:
            return
        directory = directory.parent


@dataclass(frozen=True)
class Settings:
    """Runtime settings. In the desktop app the Electron main process sets these
    via environment variables before spawning the service."""

    workspace_root: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    token: str | None = None          # if set, required via X-Laura-Token header
    start_runner: bool = True         # background job runner thread
    lease_seconds: int = 60
    worker_concurrency: int = 3       # background job-runner threads (desktop)
    job_max_runtime_seconds: int = 3600  # cap before a job's lease stops being refreshed
    database_url: str | None = None   # postgresql://… for server mode; else SQLite
    rate_limit_rpm: int = 0           # per-identity requests/minute; 0 disables the limiter
    rate_limit_burst: int = 0         # bucket capacity; 0 -> falls back to rpm

    @property
    def db_path(self) -> Path:
        return self.workspace_root / "laura.db"

    @classmethod
    def load(cls) -> Settings:
        _load_dotenv()  # fills gaps from the nearest .env; explicit env always wins
        root = os.environ.get("LAURA_WORKSPACE")
        workspace_root = (
            Path(root).expanduser().resolve()
            if root
            else (Path.cwd() / "workspace").resolve()
        )
        return cls(
            workspace_root=workspace_root,
            host=os.environ.get("LAURA_HOST", DEFAULT_HOST),
            port=int(os.environ.get("LAURA_PORT", str(DEFAULT_PORT))),
            token=os.environ.get("LAURA_TOKEN") or None,
            worker_concurrency=int(os.environ.get("LAURA_WORKERS", "3")),
            job_max_runtime_seconds=int(os.environ.get("LAURA_JOB_MAX_RUNTIME", "3600")),
            database_url=os.environ.get("DATABASE_URL") or None,
            rate_limit_rpm=int(os.environ.get("LAURA_RATE_LIMIT_RPM", "0")),
            rate_limit_burst=int(os.environ.get("LAURA_RATE_LIMIT_BURST", "0")),
        )


def ensure_workspace(settings: Settings) -> None:
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
