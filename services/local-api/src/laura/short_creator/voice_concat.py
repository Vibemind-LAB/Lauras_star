"""Constructed single voice track from per-line clips (spec 2026-08-06 §5).

The whole point is sync BY CONSTRUCTION: video segment i is sized to clip i, and the
audio track is exactly those clips with ``gap_s`` of silence between them — offsets and
segment starts coincide with no arithmetic in between. The renderer keeps seeing ONE
mp3 + ONE word-timings sidecar, so nothing downstream changes.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

INTER_SCENE_GAP_S = 0.35
LAST_SEGMENT_CUSHION_S = 0.3


def probe_duration_s(path: Path) -> float:
    """Container duration via ffprobe; raises RuntimeError when unreadable."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {proc.stderr[:200]}")
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"ffprobe gave no duration for {path.name}") from exc


def line_offsets(durations: list[float], gap_s: float) -> list[float]:
    """Start offset of each clip inside the constructed track (gaps BETWEEN clips)."""
    offsets: list[float] = []
    cursor = 0.0
    for duration in durations:
        offsets.append(cursor)
        cursor += duration + gap_s
    return offsets


def merge_word_timings(
    per_line_words: list[list[dict[str, Any]]], offsets: list[float]
) -> dict[str, Any]:
    """One sidecar over the constructed track: every line's words shifted by its offset.

    A line without timings contributes nothing (captions there fall back to the render's
    even-spread) — never a reason to fail the voice.

    Every word carries the index of the line it came from. That index is what makes a gappy
    sidecar readable: the readers downstream (``line_starts``, ``chapter_audio_windows``) used
    to re-derive line boundaries by counting each line's whitespace tokens forward through the
    stream, which silently mis-attributes EVERY word after the first line that contributed
    none — wrong zoom anchors and wrong chapter audio windows, with nothing failing.
    """
    words: list[dict[str, Any]] = []
    for line_index, (line_words, offset) in enumerate(zip(per_line_words, offsets, strict=True)):
        for word in line_words:
            words.append(
                {
                    "text": str(word.get("text", "")),
                    "start_s": float(word.get("start_s", 0.0)) + offset,
                    "end_s": float(word.get("end_s", 0.0)) + offset,
                    "line": line_index,
                }
            )
    return {"words": words}


def concat_with_gaps(clip_paths: list[Path], gap_s: float, out_path: Path) -> None:
    """Concat clips with ``gap_s`` silence BETWEEN them (n-1 gaps) into one mp3.

    filter_complex re-encodes; no gap after the last clip, so track end == last word's
    breath and ``-shortest`` at mux time has nothing to trim.
    """
    if not clip_paths:
        raise RuntimeError("no clips to concat")
    args: list[str] = ["ffmpeg", "-y"]
    for clip in clip_paths:
        args += ["-i", str(clip)]
    parts: list[str] = []
    labels: list[str] = []
    for i in range(len(clip_paths)):
        labels.append(f"[{i}:a]")
        if i < len(clip_paths) - 1:
            parts.append(
                f"aevalsrc=0:d={gap_s}:s=44100[g{i}]"
            )
            labels.append(f"[g{i}]")
    graph = ";".join(parts + [
        "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"
    ])
    args += ["-filter_complex", graph, "-map", "[out]", "-q:a", "4", str(out_path)]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {proc.stderr[-300:]}")
