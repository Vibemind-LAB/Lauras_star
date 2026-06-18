"""Machine-readable provenance manifests for Laura AI-generated media.

This is a dependency-free second labeling layer. It is not a cryptographic C2PA
claim; it is a stable Laura sidecar manifest that can later be embedded or
translated into Video Seal/C2PA.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from ..util import utcnow_iso

SCHEMA = "laura.ai.provenance.v1"


def write_ai_provenance_manifest(
    *,
    media_path: Path,
    asset_id: str,
    project_id: str,
    ai_effect: str,
    source: dict[str, Any],
    synthetic: bool = True,
) -> Path:
    """Write a JSON provenance sidecar next to an AI-generated media file."""
    manifest_path = Path(f"{media_path}.laura-provenance.json")
    data: dict[str, Any] = {
        "schema": SCHEMA,
        "asset_id": asset_id,
        "project_id": project_id,
        "synthetic": synthetic,
        "ai_effect": ai_effect,
        "media_path": str(media_path),
        "media_sha256": _sha256(media_path),
        "created_at": utcnow_iso(),
        "source": source,
    }
    manifest_path.write_text(
        json.dumps(data, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return manifest_path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
