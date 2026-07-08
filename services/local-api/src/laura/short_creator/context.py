"""Context tools for the Describer + Transcript-Analyst agents.

* :func:`transcript_window` — the words spoken around a candidate frame (± a frame window),
  over the asset's latest analysis run. Fully local, DB-only.
* :func:`describe_moment` — a short VLM description of a candidate frame. Injectable backend +
  frame extractor, graceful when no model or no proxy frame.

Both return JSON-serialisable dicts. The window filter is pure; frame extraction reuses
``analysis.transition_review.extract_frames`` (CFR proxy, exact ``-ss``).
"""

from __future__ import annotations

import re
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
    """Segments whose half-open ``[start_frame, end_frame)`` overlaps ``[lo, hi]``. Pure.

    ``end_frame`` is end-exclusive (the frame after the last spoken sample), so a segment
    overlaps iff ``start_frame <= hi`` and ``end_frame > lo`` (a segment ending exactly at ``lo``
    covers only up to ``lo-1`` and is outside the window).
    """
    lo, hi = center_frame - window_frames, center_frame + window_frames
    out: list[dict[str, Any]] = []
    for seg in segments:
        start_frame, end_frame = seg.get("start_frame"), seg.get("end_frame")
        if start_frame is None or end_frame is None:
            continue
        if int(end_frame) > lo and int(start_frame) <= hi:
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


def _group_segments_into_blocks(
    segments: Sequence[dict[str, Any]], *, blocks: int
) -> list[dict[str, Any]]:
    """Group ordered transcript segments into ≤ *blocks* contiguous chunks. Pure.

    Each block carries its frame range and the joined text — a compact per-section view of the
    video that an agent can summarize (the user's "Transkript pro Szene zusammenfassen" idea).
    Never emits empty blocks (fewer segments than blocks → one block per segment).
    """
    rows = [s for s in segments if s.get("start_frame") is not None]
    if not rows or blocks < 1:
        return []
    per_block = max(1, -(-len(rows) // blocks))  # ceil division
    out: list[dict[str, Any]] = []
    for i in range(0, len(rows), per_block):
        chunk = rows[i : i + per_block]
        out.append(
            {
                "start_frame": int(chunk[0]["start_frame"]),
                "end_frame": int(chunk[-1]["end_frame"]),
                "text": " ".join(str(s.get("text") or "").strip() for s in chunk).strip(),
            }
        )
    return out


def transcript_overview(db: Database, asset_id: str, blocks: int = 8) -> dict[str, Any]:
    """The whole transcript grouped into ≤ *blocks* time blocks — the video at a glance."""
    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None:
        return {"ok": False, "reason": "no analysis run", "blocks": []}
    segs = repos.get_transcript(db, asset_id, str(run["id"]))
    grouped = _group_segments_into_blocks(segs, blocks=blocks)
    return {"ok": True, "asset_id": asset_id, "blocks": grouped}


def _scene_src_ranges(
    clips: Sequence[dict[str, Any]], *, seq_in: int, seq_out_exclusive: int
) -> list[tuple[int, int]]:
    """SOURCE frame ranges covered by a scene's SEQUENCE range, via the lane-0 clips. Pure.

    Scenes carry sequence positions on the rough cut; transcript segments live in source
    frames. Each clip maps ``seq -> src`` linearly (speed 1); the scene's overlap with a clip
    projects to ``src_in + (overlap_start - clip.seq_in)``. Integer frames, end-exclusive.
    """
    out: list[tuple[int, int]] = []
    for c in clips:
        if int(c.get("lane") or 0) != 0:
            continue
        c_seq_in = int(c["seq_in_frame"])
        c_seq_out = int(c["seq_out_frame_exclusive"])
        lo = max(seq_in, c_seq_in)
        hi = min(seq_out_exclusive, c_seq_out)
        if hi <= lo:
            continue
        src_in = int(c["src_in_frame"])
        out.append((src_in + (lo - c_seq_in), src_in + (hi - c_seq_in)))
    out.sort()
    return out


def _segments_in_ranges(
    segments: Sequence[dict[str, Any]], ranges: Sequence[tuple[int, int]]
) -> list[dict[str, Any]]:
    """Segments whose half-open ``[start_frame, end_frame)`` overlaps ANY source range. Pure."""
    out: list[dict[str, Any]] = []
    for seg in segments:
        start_frame, end_frame = seg.get("start_frame"), seg.get("end_frame")
        if start_frame is None or end_frame is None:
            continue
        s, e = int(start_frame), int(end_frame)
        if any(e > lo and s < hi for (lo, hi) in ranges):
            out.append(seg)
    return out


_TOKEN = re.compile(r"[a-zA-ZäöüÄÖÜß0-9]{3,}")


def _rank_texts(topic: str, texts: Sequence[tuple[int, str]]) -> list[tuple[int, float, str]]:
    """Rank ``(number, text)`` pairs by lexical overlap with *topic*. Pure.

    Score = matched unique topic tokens + a small term-frequency bonus, length-normalized so a
    long rambling scene does not beat a short on-topic one. Returns ``(number, score, snippet)``
    sorted best-first; zero-score entries are dropped.
    """
    topic_tokens = {t.lower() for t in _TOKEN.findall(topic)}
    if not topic_tokens:
        return []
    ranked: list[tuple[int, float, str]] = []
    for number, text in texts:
        words = [t.lower() for t in _TOKEN.findall(text)]
        if not words:
            continue
        matched = {w for w in words if w in topic_tokens}
        if not matched:
            continue
        tf = sum(1 for w in words if w in topic_tokens)
        score = len(matched) + tf / (1.0 + len(words) / 25.0) * 0.1
        snippet = text.strip()[:160]
        ranked.append((number, round(score, 4), snippet))
    ranked.sort(key=lambda r: r[1], reverse=True)
    return ranked


def scene_transcripts(db: Database, asset_id: str) -> dict[str, Any]:
    """Per rough-cut scene (1-based number): its source-mapped transcript text."""
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        return {"ok": False, "reason": "asset not found", "scenes": []}
    timeline = repos.get_or_create_asset_rough_cut(db, str(asset["project_id"]), asset_id)
    scenes = repos.list_scenes(db, str(timeline["id"]))
    if not scenes:
        return {"ok": False, "reason": "no scenes; build_roughcut first", "scenes": []}
    run = repos.get_latest_analysis_run(db, asset_id)
    segments = repos.get_transcript(db, asset_id, str(run["id"])) if run is not None else []
    clips = repos.list_timeline_clips(db, str(timeline["id"]))
    out: list[dict[str, Any]] = []
    for scene in scenes:
        ranges = _scene_src_ranges(
            clips,
            seq_in=int(scene["seq_in_frame"]),
            seq_out_exclusive=int(scene["seq_out_frame_exclusive"]),
        )
        in_scene = _segments_in_ranges(segments, ranges)
        text = " ".join(str(seg.get("text") or "").strip() for seg in in_scene).strip()
        out.append(
            {
                "scene_number": int(scene["order_index"]) + 1,
                "seq_in_frame": int(scene["seq_in_frame"]),
                "seq_out_frame_exclusive": int(scene["seq_out_frame_exclusive"]),
                "text": text,
            }
        )
    return {"ok": True, "asset_id": asset_id, "scenes": out}


def rank_scenes_by_topic(db: Database, asset_id: str, topic: str, k: int = 10) -> dict[str, Any]:
    """The rough-cut scenes most relevant to *topic*, by their transcript text."""
    scenes = scene_transcripts(db, asset_id)
    if not scenes.get("ok"):
        return {"ok": False, "reason": scenes.get("reason", "no scenes"), "ranked": []}
    ranked = _rank_texts(topic, [(s["scene_number"], str(s["text"])) for s in scenes["scenes"]])
    return {
        "ok": True,
        "topic": topic,
        "ranked": [
            {"scene_number": n, "score": score, "snippet": snippet}
            for (n, score, snippet) in ranked[: max(1, int(k))]
        ],
    }


def _voice_alignment(
    words: Sequence[dict[str, Any]], *, start_frame: int, end_frame_exclusive: int
) -> dict[str, Any]:
    """Does the cut ``[start_frame, end_frame_exclusive)`` respect word boundaries? Pure.

    A word is *clipped* when a cut lands inside it (word starts before the cut and ends after
    it). ``lead_in_frames``/``tail_frames`` measure silence padding between the cuts and the
    first/last fully-contained word — useful to judge breathing room.
    """
    clipped: list[str] = []
    inside: list[dict[str, Any]] = []
    for w in words:
        w_start, w_end = int(w["start_frame"]), int(w["end_frame"])
        if w_start < start_frame < w_end or w_start < end_frame_exclusive < w_end:
            clipped.append(str(w.get("text") or "").strip())
        elif start_frame <= w_start and w_end <= end_frame_exclusive:
            inside.append(w)
    lead_in = int(inside[0]["start_frame"]) - start_frame if inside else 0
    tail = end_frame_exclusive - int(inside[-1]["end_frame"]) if inside else 0
    return {
        "aligned": not clipped,
        "clipped_words": clipped,
        "lead_in_frames": lead_in,
        "tail_frames": tail,
        "words_inside": len(inside),
    }


def _export_voice_alignment(db: Database, export: dict[str, Any]) -> dict[str, Any]:
    """Voice alignment for a whole EXPORT: every segment it was cut from is checked.

    The QA gate habitually feeds the Editor's export_id into alignment (live-run finding) —
    instead of erroring, resolve the export's candidate_ids (or raw segments + asset_id) and
    aggregate: aligned only when every segment keeps its words intact.
    """
    opts: dict[str, Any] = export.get("options") or {}
    ranges: list[tuple[int, int]] = []
    asset_id = str(opts.get("asset_id") or "")
    for cid in opts.get("candidate_ids") or []:
        row = repos.get_short_candidate(db, str(cid))
        if row is not None:
            asset_id = asset_id or str(row["asset_id"])
            ranges.append((int(row["start_frame"]), int(row["end_frame_exclusive"])))
    for seg in opts.get("segments") or []:
        ranges.append((int(seg[0]), int(seg[1])))
    if not asset_id or not ranges:
        return {"ok": False, "reason": "export has no checkable segments", "aligned": False}
    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None:
        return {"ok": False, "reason": "no analysis run", "aligned": False}
    words = repos.list_words_for_run(db, asset_id, str(run["id"]))
    clipped: list[str] = []
    for start, end in ranges:
        result = _voice_alignment(words, start_frame=start, end_frame_exclusive=end)
        clipped.extend(result["clipped_words"])
    return {
        "ok": True,
        "export_id": str(export["id"]),
        "segments_checked": len(ranges),
        "aligned": not clipped,
        "clipped_words": clipped,
    }


def check_voice_alignment(db: Database, candidate_id: str) -> dict[str, Any]:
    """Verify a candidate's cut keeps every word intact (voice aligned to the scene).

    Objective ground for the QA gate: sentence-snapped candidates should never clip words; if
    this reports clipped words, the short's voice is cut mid-word and the verdict must be weak.
    Accepts an EXPORT id too (all its segments are checked) — QA gates pass those in practice.
    """
    candidate = repos.get_short_candidate(db, candidate_id)
    if candidate is None:
        export = repos.get_export(db, candidate_id)
        if export is not None:
            return _export_voice_alignment(db, export)
        return {"ok": False, "reason": "candidate not found", "aligned": False}
    asset_id = str(candidate["asset_id"])
    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None:
        return {"ok": False, "reason": "no analysis run", "aligned": False}
    words = repos.list_words_for_run(db, asset_id, str(run["id"]))
    result = _voice_alignment(
        words,
        start_frame=int(candidate["start_frame"]),
        end_frame_exclusive=int(candidate["end_frame_exclusive"]),
    )
    return {
        "ok": True,
        "candidate_id": candidate_id,
        "start_boundary": candidate.get("start_boundary"),
        "end_boundary": candidate.get("end_boundary"),
        **result,
    }


FrameExtractor = Callable[[Database, str, int], list[bytes]]


def _proxy_path(db: Database, asset_id: str) -> str | None:
    for file in repos.list_asset_files(db, asset_id):
        if file.get("is_proxy"):
            return str(file["path"])
    return None


def _frame_rate(db: Database, asset: dict[str, Any]) -> tuple[int, int] | None:
    """The asset's own probed rate, falling back to the project's sequence rate.

    Projects store ``sequence_rate_num``/``sequence_rate_den`` (NOT ``rate_num`` —
    live-run finding); assets carry ``rate_num``/``rate_den`` from probe, which may be NULL.
    """
    num, den = asset.get("rate_num"), asset.get("rate_den")
    if num and den:
        return int(num), int(den)
    project = repos.get_project(db, str(asset["project_id"]))
    if project is None:
        return None
    return int(project["sequence_rate_num"]), int(project["sequence_rate_den"])


def _default_extract(db: Database, asset_id: str, frame: int) -> list[bytes]:
    """Extract one JPEG for (asset, frame) from the asset's proxy at the asset/project rate."""
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        return []
    proxy = _proxy_path(db, asset_id)
    rate = _frame_rate(db, asset)
    if proxy is None or rate is None:
        return []
    return extract_frames(
        {asset_id: proxy}, [(asset_id, frame)], rate_num=rate[0], rate_den=rate[1]
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
