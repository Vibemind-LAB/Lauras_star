"""Shared editorial cut placement: joint visual+audio+transcript boundary refinement.

This is the one canonical placement stage used by BOTH the explicit
``POST /projects/{id}/timelines/from-shots`` endpoint and the zero-click auto-build path
(:func:`laura.scenes.build.populate_rough_cut_from_shots`). Previously the strong
:func:`laura.analysis.joint.joint_place` logic only ran on the manual endpoint, so the default
import landed raw shot boundaries; extracting it here lets every imported asset get clean,
transcript+audio-aware cuts with no new ML and no new UI.

Frame-accuracy is preserved end to end: every boundary stays an integer source frame
(invariant #1), ranges are end-exclusive (#2), and the placement is a pure refinement that
degrades gracefully to the raw cut when no words / silence / video are available.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..db import repos
from ..db.database import Database
from .editorial import Word
from .eval_cut import FrameLoader, load_gray_frames_ffmpeg
from .joint import bias_to_weights, joint_place
from .semantic import sentence_end_frames, speaker_turn_frames
from .silence import detect_silence

# Max frames a cut may move during auto-build editorial placement (~0.4s @ 30fps); mirrors the
# from-shots endpoint's ``editorial_window`` default so the auto path and the explicit endpoint
# refine cuts by the same amount.
AUTO_EDITORIAL_WINDOW = 12


def editorial_autocut_enabled() -> bool:
    """Whether the zero-click auto-build applies editorial placement. Default on; opt out with
    ``LAURA_EDITORIAL_AUTOCUT=0`` to land raw shot boundaries (the pre-unification behaviour)."""
    raw = os.environ.get("LAURA_EDITORIAL_AUTOCUT")
    if raw is None:
        return True
    return raw.strip().lower() not in ("0", "false", "no", "off")


def resolve_video_path(db: Database, asset_id: str, asset: dict[str, Any]) -> str | None:
    """The readable video for the joint visual signal — proxy preferred, else original/source.

    The CFR editorial proxy is the right surface for the diff signal (it is what the shot detector
    ran on), falling back to an ``original`` derived file and finally the asset's ``source_path``.
    Returns ``None`` when nothing on disk is readable, in which case joint placement degrades to the
    editorial-only choice.
    """
    by_kind = {f["kind"]: f["path"] for f in repos.list_asset_files(db, asset_id)}
    for kind in ("proxy", "original"):
        path = by_kind.get(kind)
        if path and Path(path).is_file():
            return str(path)
    src = asset.get("source_path")
    if src and Path(src).is_file():
        return str(src)
    return None


def detect_asset_silence(
    video_path: Path | str | None, asset: dict[str, Any]
) -> list[tuple[int, int]]:
    """Real audio silences of the asset as source-frame ranges, or ``[]`` when unavailable.

    Maps the seconds-based ``silencedetect`` output into the asset's source-frame space via its
    frame rate. Returns ``[]`` when there is no readable video or the asset rate is unknown —
    silence is purely additive, so its absence just leaves placement to the word-gap proxy.
    """
    if video_path is None:
        return []
    rate_num = asset.get("rate_num")
    rate_den = asset.get("rate_den")
    if not rate_num or not rate_den:
        return []
    return detect_silence(video_path, rate_num=rate_num, rate_den=rate_den)


def place_editorial_cuts(
    rows: list[dict[str, Any]],
    words: list[Word],
    *,
    window: int,
    w_visual: float = 0.6,
    w_editorial: float = 0.4,
    silence: list[tuple[int, int]] | None = None,
    sentence_frames: set[int] | None = None,
    speaker_frames: set[int] | None = None,
    video_path: Path | str | None = None,
    total_frames: int | None = None,
    frame_loader: FrameLoader = load_gray_frames_ffmpeg,
) -> None:
    """Place each clip's source IN by JOINT visual+editorial quality, mutating ``rows`` in place.

    For every clip after the first, its ``src_in_frame`` is the cut between it and the previous
    clip. :func:`laura.analysis.joint.joint_place` picks the single frame in
    ``[cut-window, cut+window]`` that maximises ``w_visual*visual_score + w_editorial*editorial`` —
    so a clean word edge only displaces the frame-exact visual peak when the blend says the trade is
    worth it. The visual signal is decoded around the cut via ``frame_loader``; with no video it
    reduces to the editorial-only choice, with no words to the visual-only choice.

    ``silence`` (end-exclusive source-frame ranges of real audio silence), ``sentence_frames`` and
    ``speaker_frames`` (semantic seams) are all optional and additive: a cut prefers a speaker turn
    > sentence end > real silence > word edge > mid-word. With none of them, placement is unchanged.

    The match is applied to BOTH sides only when the two clips are source-contiguous
    (``prev.src_out == cur.src_in``); otherwise the cut sits across a source gap and only the
    current clip's IN moves, never inventing source frames. The first clip's start is never touched.
    After all IN frames settle the sequence is repacked back-to-back from the new source lengths.
    """
    if window <= 0:
        return
    if not words and not silence and video_path is None:
        return
    for i in range(1, len(rows)):
        cur = rows[i]
        prev = rows[i - 1]
        cut = cur["src_in_frame"]
        aligned, _score = joint_place(
            cut,
            words,
            window=window,
            w_visual=w_visual,
            w_editorial=w_editorial,
            silence=silence,
            sentence_frames=sentence_frames,
            speaker_frames=speaker_frames,
            video_path=video_path,
            total_frames=total_frames,
            frame_loader=frame_loader,
        )
        if aligned == cut:
            continue
        # Keep both source ranges non-empty; skip a move that would collapse a clip.
        if aligned <= prev["src_in_frame"] or aligned >= cur["src_out_frame_exclusive"]:
            continue
        contiguous = prev["src_out_frame_exclusive"] == cut
        cur["src_in_frame"] = aligned
        if contiguous:
            prev["src_out_frame_exclusive"] = aligned

    # Repack the sequence from the (possibly changed) source lengths: contiguous, end-exclusive.
    offset = rows[0]["seq_in_frame"] if rows else 0
    for row in rows:
        length = row["src_out_frame_exclusive"] - row["src_in_frame"]
        row["seq_in_frame"] = offset
        row["seq_out_frame_exclusive"] = offset + length
        offset += length


def gather_editorial_signals(
    db: Database, asset: dict[str, Any], run_id: str, *, video_path: Path | str | None
) -> tuple[list[Word], set[int], set[int], list[tuple[int, int]]]:
    """Collect the placement signals for an asset+run: (words, sentence_frames, speaker_frames,
    silence). Each degrades gracefully to empty when its source is absent (no transcript -> no
    words/semantics; no readable video / unknown rate -> no silence)."""
    word_rows = repos.list_words_for_run(db, asset["id"], run_id)
    words = [
        Word(
            start_frame=w["start_frame"],
            end_frame=w["end_frame"],
            text=w.get("text"),
            speaker=w.get("speaker_label"),
        )
        for w in word_rows
    ]
    sentence_frames = sentence_end_frames(words)
    speaker_frames = speaker_turn_frames(words)
    silence = detect_asset_silence(video_path, asset)
    return words, sentence_frames, speaker_frames, silence


def apply_editorial_placement(
    db: Database,
    *,
    asset: dict[str, Any],
    run_id: str,
    rows: list[dict[str, Any]],
    cut_bias: float | None = None,
    window: int = AUTO_EDITORIAL_WINDOW,
    video_path: Path | str | None = None,
) -> None:
    """Gather an asset's transcript/audio/video signals and refine the in-memory clip ``rows`` in
    place (the high-level entry both the from-shots endpoint and the auto-build path can call).

    A pure refinement: with no transcript and no readable video this is a no-op and ``rows`` keep
    their raw shot boundaries. Cuts stay integer frames; the sequence is repacked end-exclusive.
    """
    if len(rows) < 2:
        return
    if video_path is None:
        video_path = resolve_video_path(db, str(asset["id"]), asset)
    words, sentence_frames, speaker_frames, silence = gather_editorial_signals(
        db, asset, run_id, video_path=video_path
    )
    w_visual, w_editorial = bias_to_weights(cut_bias)
    place_editorial_cuts(
        rows,
        words,
        window=window,
        w_visual=w_visual,
        w_editorial=w_editorial,
        silence=silence,
        sentence_frames=sentence_frames,
        speaker_frames=speaker_frames,
        video_path=video_path,
        total_frames=asset.get("duration_frames"),
    )
