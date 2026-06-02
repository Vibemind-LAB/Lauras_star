"""Analysis manifest — the per-asset record of which stages ran at which version.

Anchors idempotency/audit (docs/06-storage.md): same input + same pipeline_version
=> same analysis state.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_manifest(
    dest: Path,
    *,
    pipeline_version: str,
    asset_id: str,
    stages: dict[str, Any],
    started_at: str,
    finished_at: str,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "version": 1,
        "pipeline_version": pipeline_version,
        "asset_id": asset_id,
        "stages": stages,
        "started_at": started_at,
        "finished_at": finished_at,
    }
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return payload
