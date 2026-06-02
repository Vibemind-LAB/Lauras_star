"""Service configuration (docs/04-api.md). Local-first defaults; bind to loopback."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


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

    @property
    def db_path(self) -> Path:
        return self.workspace_root / "laura.db"

    @classmethod
    def load(cls) -> Settings:
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
        )


def ensure_workspace(settings: Settings) -> None:
    settings.workspace_root.mkdir(parents=True, exist_ok=True)
