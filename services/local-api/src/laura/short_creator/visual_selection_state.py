"""Stable source-media identity for resumable visual-selection proposals."""

from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from ..db import repos
from ..db.database import Database
from ..ingest.probe import sha256_file


class SourceMediaStaleError(RuntimeError):
    """The source or one of its deterministic editorial parents changed."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class SourceMediaSnapshot:
    quick_hash: str
    strong_hash: str | None
    resolved_path: str
    size: int
    mtime_ns: int


def _canonical_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return sha256(raw.encode("utf-8")).hexdigest()


def _canonical_fps(fps: float) -> str:
    return format(Decimal(str(fps)).normalize(), "f")


def capture_source_media_snapshot(
    db: Database,
    *,
    asset_id: str,
    rough_cut_hash: str,
    fps: float,
    voice_hash: str,
    voice_total_frames: int,
    script_hash: str,
    request_hash: str,
    strong: bool,
) -> SourceMediaSnapshot:
    """Capture quick metadata identity and, when requested, current file content."""
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise SourceMediaStaleError("asset_missing")
    try:
        source = Path(str(asset["source_path"])).resolve(strict=True)
    except (OSError, RuntimeError):
        raise SourceMediaStaleError("source_missing") from None
    if not source.is_file():
        raise SourceMediaStaleError("source_missing")
    try:
        stat = source.stat()
    except OSError:
        raise SourceMediaStaleError("source_unreadable") from None

    stored_sha = str(asset["sha256"]) if asset.get("sha256") else None
    current_sha: str | None = None
    if strong:
        try:
            current_sha = sha256_file(source)
        except OSError:
            raise SourceMediaStaleError("source_unreadable") from None
        if stored_sha is None:
            stored_sha = repos.set_asset_sha256_if_missing(db, asset_id, current_sha)
            if stored_sha is None:
                raise SourceMediaStaleError("asset_missing")
        if current_sha != stored_sha:
            raise SourceMediaStaleError("source_content_changed")
    elif stored_sha is None:
        raise SourceMediaStaleError("source_identity_missing")

    quick_payload: dict[str, Any] = {
        "asset_id": asset_id,
        "asset_sha256": stored_sha,
        "fps": _canonical_fps(fps),
        "mtime_ns": stat.st_mtime_ns,
        "request_hash": request_hash,
        "resolved_path": str(source),
        "rough_cut_hash": rough_cut_hash,
        "script_hash": script_hash,
        "size": stat.st_size,
        "voice_hash": voice_hash,
        "voice_total_frames": voice_total_frames,
    }
    quick_hash = _canonical_hash(quick_payload)
    strong_hash = (
        _canonical_hash({**quick_payload, "content_sha256": current_sha})
        if current_sha is not None
        else None
    )
    return SourceMediaSnapshot(
        quick_hash=quick_hash,
        strong_hash=strong_hash,
        resolved_path=str(source),
        size=stat.st_size,
        mtime_ns=stat.st_mtime_ns,
    )


def validate_source_media_snapshot(
    db: Database,
    *,
    asset_id: str,
    rough_cut_hash: str,
    fps: float,
    voice_hash: str,
    voice_total_frames: int,
    script_hash: str,
    request_hash: str,
    expected_quick_hash: str,
    expected_strong_hash: str | None,
    strong: bool,
) -> SourceMediaSnapshot:
    """Capture current identity and reject differences from a proposal's parents."""
    current = capture_source_media_snapshot(
        db,
        asset_id=asset_id,
        rough_cut_hash=rough_cut_hash,
        fps=fps,
        voice_hash=voice_hash,
        voice_total_frames=voice_total_frames,
        script_hash=script_hash,
        request_hash=request_hash,
        strong=strong,
    )
    if current.quick_hash != expected_quick_hash:
        raise SourceMediaStaleError("source_metadata_changed")
    if strong:
        if expected_strong_hash is None:
            raise SourceMediaStaleError("source_identity_missing")
        if current.strong_hash != expected_strong_hash:
            raise SourceMediaStaleError("source_content_changed")
    return current
