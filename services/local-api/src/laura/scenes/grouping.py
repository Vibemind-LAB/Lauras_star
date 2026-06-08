# services/local-api/src/laura/scenes/grouping.py
"""Pure shot/clip -> scene grouping. Boundaries fall only on clip junctions; scenes tile
the rough-cut contiguously. A break is placed between two adjacent clips when the speaker
changes or the inter-clip silence gap reaches ``gap_frames``. When the whole asset has no
transcript words, every clip becomes its own scene (fallback)."""

from __future__ import annotations

from typing import Any


def _speaker(words: list[dict[str, Any]]) -> str | None:
    raw = words[0].get("speaker") if words else None
    return str(raw) if raw is not None else None


def _is_boundary(
    cur: list[dict[str, Any]], nxt: list[dict[str, Any]], gap_frames: int
) -> bool:
    if not cur or not nxt:
        return False  # no transcript evidence across this junction -> keep together
    if _speaker(cur) != _speaker(nxt):
        return True
    gap: int = int(nxt[0]["start_frame"]) - int(cur[-1]["end_frame"])
    return gap >= gap_frames


def group_into_scenes(
    clips: list[dict[str, Any]],
    words_by_clip: list[list[dict[str, Any]]],
    *,
    gap_frames: int,
) -> list[tuple[int, int]]:
    """``clips`` ordered by ``seq_in_frame`` (each = one kept shot). ``words_by_clip[i]`` are
    the transcript words covering ``clips[i]`` (ordered by ``start_frame``; each a dict with
    ``start_frame``/``end_frame``/``speaker``; may be empty). Returns ``(seq_in_frame,
    seq_out_frame_exclusive)`` ranges tiling the clips."""
    if not clips:
        return []
    has_any_words = any(words_by_clip)
    scenes: list[tuple[int, int]] = []
    start = clips[0]["seq_in_frame"]
    for i in range(len(clips) - 1):
        boundary = True if not has_any_words else _is_boundary(
            words_by_clip[i], words_by_clip[i + 1], gap_frames
        )
        if boundary:
            scenes.append((start, clips[i]["seq_out_frame_exclusive"]))
            start = clips[i + 1]["seq_in_frame"]
    scenes.append((start, clips[-1]["seq_out_frame_exclusive"]))
    return scenes
