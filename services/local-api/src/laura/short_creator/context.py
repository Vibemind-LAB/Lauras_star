"""Context tools for the Describer + Transcript-Analyst agents.

* :func:`transcript_window` — the words spoken around a candidate frame (± a frame window),
  over the asset's latest analysis run. Fully local, DB-only.
* :func:`describe_moment` — a short VLM description of a candidate frame. Injectable backend +
  frame extractor, graceful when no model or no proxy frame.

Both return JSON-serialisable dicts. The window filter is pure; frame extraction reuses
``analysis.transition_review.extract_frames`` (CFR proxy, exact ``-ss``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from ..analysis.transition_review import extract_frames
from ..db import repos
from ..db.database import Database
from .describe import DESCRIBE_PROMPT, DescribeBackend, resolve_describe_backend

DEFAULT_WINDOW_FRAMES = 450  # ~15s at 30fps


def _segments_in_window(
    segments: Sequence[dict[str, Any]], center_frame: int, window_frames: int
) -> list[dict[str, Any]]:
    """Segments whose [start_frame, end_frame] overlaps [center-window, center+window]. Pure."""
    lo, hi = center_frame - window_frames, center_frame + window_frames
    out: list[dict[str, Any]] = []
    for seg in segments:
        start_frame, end_frame = seg.get("start_frame"), seg.get("end_frame")
        if start_frame is None or end_frame is None:
            continue
        if int(end_frame) >= lo and int(start_frame) <= hi:
            out.append(seg)
    return out


def transcript_window(
    db: Database, asset_id: str, center_frame: int, window_frames: int = DEFAULT_WINDOW_FRAMES
) -> dict[str, Any]:
    """What is said around ``center_frame`` (± ``window_frames``) in the asset's transcript."""
    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None:
        return {"ok": False, "reason": "no analysis run", "segments": [], "text": ""}
    segs = repos.get_transcript(db, asset_id, str(run["id"]))
    window = _segments_in_window(segs, center_frame, window_frames)
    text = " ".join(str(seg.get("text") or "").strip() for seg in window).strip()
    return {
        "ok": True,
        "center_frame": center_frame,
        "window_frames": window_frames,
        "segments": [
            {
                "start_frame": seg.get("start_frame"),
                "end_frame": seg.get("end_frame"),
                "text": seg.get("text"),
                "speaker": seg.get("speaker_label"),
            }
            for seg in window
        ],
        "text": text,
    }


FrameExtractor = Callable[[Database, str, int], list[bytes]]


def _proxy_path(db: Database, asset_id: str) -> str | None:
    for file in repos.list_asset_files(db, asset_id):
        if file.get("is_proxy"):
            return str(file["path"])
    return None


def _default_extract(db: Database, asset_id: str, frame: int) -> list[bytes]:
    """Extract one JPEG for (asset, frame) from the asset's proxy at the project rate."""
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        return []
    project = repos.get_project(db, str(asset["project_id"]))
    proxy = _proxy_path(db, asset_id)
    if project is None or proxy is None:
        return []
    return extract_frames(
        {asset_id: proxy},
        [(asset_id, frame)],
        rate_num=int(project["rate_num"]),
        rate_den=int(project["rate_den"]),
    )


def describe_moment(
    db: Database,
    asset_id: str,
    frame: int,
    *,
    backend: DescribeBackend | None = None,
    extract: FrameExtractor | None = None,
) -> dict[str, Any]:
    """A short VLM description of the frame; graceful (ok=False) when no model or no proxy frame."""
    resolved = backend if backend is not None else resolve_describe_backend()
    if resolved is None or not resolved.available():
        return {"ok": False, "reason": "no VLM configured", "description": ""}
    extractor = extract if extract is not None else _default_extract
    frames = extractor(db, asset_id, frame)
    if not frames:
        return {"ok": False, "reason": "no proxy frame", "description": ""}
    return {"ok": True, "frame": frame, "description": resolved.describe(frames, DESCRIBE_PROMPT)}
