"""Build editable Product-Demo draft items from existing Laura analysis data."""

from __future__ import annotations

from typing import Any

from ..db import repos
from ..db.database import Database


def build_demo_draft_items(db: Database, asset_id: str) -> list[dict[str, Any]]:
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise ValueError(f"asset not found: {asset_id}")
    ranges = _shot_ranges(db, asset_id)
    if not ranges:
        ranges = _fallback_ranges(int(asset.get("duration_frames") or 0), _asset_rate(asset))
    if not ranges:
        ranges = [(0, 1)]

    transcript = _segments(db, asset_id)
    items: list[dict[str, Any]] = []
    for index, (src_in, src_out) in enumerate(ranges):
        text = _overlapping_text(transcript, src_in, src_out)
        label = _label(text, index)
        items.append(
            {
                "src_in_frame": src_in,
                "src_out_frame_exclusive": src_out,
                "label": label,
                "voiceover_text": text or f"Beschreibe Schritt {index + 1}.",
                "thumb_frame": src_in,
                "confidence": 0.8 if text else 0.5,
                "enabled": True,
            }
        )
    return items


def _shot_ranges(db: Database, asset_id: str) -> list[tuple[int, int]]:
    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None:
        return []
    ranges: list[tuple[int, int]] = []
    for shot in repos.list_shots(db, asset_id, run["id"]):
        if not bool(shot.get("keep", True)):
            continue
        src_in = int(shot["src_in_frame"])
        src_out = int(shot["src_out_frame_exclusive"])
        if src_out > src_in:
            ranges.append((src_in, src_out))
    return ranges


def _fallback_ranges(duration_frames: int, rate: tuple[int, int]) -> list[tuple[int, int]]:
    if duration_frames <= 0:
        return []
    block = max(1, round(6 * rate[0] / rate[1]))
    ranges: list[tuple[int, int]] = []
    start = 0
    while start < duration_frames:
        out = min(duration_frames, start + block)
        ranges.append((start, out))
        start = out
    return ranges


def _asset_rate(asset: dict[str, Any]) -> tuple[int, int]:
    rate_num = int(asset.get("rate_num") or 30)
    rate_den = int(asset.get("rate_den") or 1)
    return rate_num, rate_den


def _segments(db: Database, asset_id: str) -> list[dict[str, Any]]:
    run = repos.get_latest_transcript_run(db, asset_id)
    if run is None:
        return []
    return repos.get_transcript(db, asset_id, run["id"])


def _overlapping_text(segments: list[dict[str, Any]], src_in: int, src_out: int) -> str:
    texts: list[str] = []
    for seg in segments:
        seg_in = int(seg["start_frame"])
        seg_out = int(seg["end_frame"])
        if max(src_in, seg_in) < min(src_out, seg_out):
            text = str(seg["text"]).strip()
            if text:
                texts.append(text)
    return " ".join(texts).strip()


def _label(text: str, index: int) -> str:
    if not text:
        return f"Schritt {index + 1}"
    cleaned = " ".join(text.split())
    return cleaned[:60]
