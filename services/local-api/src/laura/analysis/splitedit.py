"""L/J split-edit planner — independent optimal video and audio cut frames per cut.

A *hard* cut switches picture and sound on the same frame. The pro technique for an
*invisible* cut is the **split edit**: cut the picture and the sound on *different* frames so
the change of sound and the change of image do not land together and call attention to the edit.
Two shapes:

* **J-cut** — the audio of the *next* shot starts *before* its picture (the viewer hears the new
  scene a beat early). The audio cut sits *earlier* than the video: ``audio_frame < video_frame``.
* **L-cut** — the audio of the *previous* shot *trails* into the next picture (the old sound
  lingers over the new image). The audio cut sits *later* than the video cut:
  ``audio_frame > video_frame``.

This module computes, *per cut*, the two frames independently and classifies the result:

* ``video_frame`` — the frame-exact **picture** cut: the visual peak, i.e. a picture-biased
  :func:`laura.analysis.joint.joint_place` (``w_visual=1.0``, ``w_editorial=0.0``). This is where
  the image genuinely changes the most, ignoring speech.
* ``audio_frame`` — the **sound** cut: the nearest frame to the original cut that lands inside a
  detected **real audio silence** (the ideal — cut the sound where there is no sound), else the
  nearest clean **word-gap / word edge** (an ASR proxy for a pause), all within ``window``. It
  never bisects a spoken word.
* ``offset = audio_frame - video_frame`` and ``kind`` from its sign (``|offset| <= 1`` -> hard).

The result is a **recommendation only**: it surfaces, per cut, whether a J or L would help and by
how many frames, so an editor (or the UI) can SEE the opportunity. It does *not* change the stored
clips — applying a split edit means cutting two independent lanes (picture on ``video_frame``,
sound on ``audio_frame``), which is a deliberate later step (model + export + UI).

All frames are **source-frame indices** of the asset, like everywhere else in Laura, and every
range is end-exclusive. The visual signal IO is injected through ``frame_loader`` (mirroring
``eval_cut``/``joint``) so callers can hand in a precomputed ``diff`` or a ``video_path``, and
tests run with neither — with no usable visual signal ``video_frame`` falls back to the original
cut, and with no words/silence ``audio_frame`` equals ``video_frame`` (a hard cut).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .editorial import DEFAULT_WINDOW, Word, _gap_frames
from .eval_cut import FrameLoader, load_gray_frames_ffmpeg
from .joint import _in_silence, joint_place

# A cut whose audio/video frames differ by at most this many frames is treated as a hard cut: a
# 1-frame split is below the threshold of perception and not worth recommending as a split edit.
HARD_OFFSET_TOLERANCE = 1


@dataclass(frozen=True)
class SplitCut:
    """A per-cut split-edit recommendation: independent picture and sound cut frames.

    All fields are source-frame indices of the asset. ``offset`` and ``kind`` summarise how a
    split edit would shape this cut; ``kind == "hard"`` means picture and sound want the same
    frame (no split helps).
    """

    seq_cut: int          # the original (visual) cut frame on the source — the rough-cut boundary
    video_frame: int      # picture cut = visual peak (picture-biased joint_place)
    audio_frame: int      # audio cut = nearest real silence (else nearest clean word-gap/edge)
    offset: int           # audio_frame - video_frame  (0 == perfectly aligned)
    kind: str             # "hard" | "L" (audio after video) | "J" (audio before video)


def _audio_frame(
    cut_frame: int,
    video_frame: int,
    words: Sequence[Word],
    silence: Sequence[tuple[int, int]] | None,
    *,
    window: int,
) -> int:
    """The best sound-cut frame near ``cut_frame``: real silence > clean word-gap/edge > fallback.

    Resolution order (all candidates constrained to ``±window`` of ``cut_frame``):

    1. **silence interior** — the nearest frame inside a detected real audio silence interval
       ``[start, end)``. Cutting the sound where there genuinely is no sound is the ideal.
    2. **clean word-gap / edge** — failing any reachable silence, the nearest frame that sits in a
       genuine pause between words or on a word boundary (never bisecting a spoken word).
    3. **fallback** — with neither silence nor words reachable, ``video_frame`` (degenerate -> the
       cut is hard, audio and video coincide).

    Candidates are ranked by distance to ``cut_frame`` (the rough-cut boundary), ties to the lower
    frame so the audio cut never drifts later than necessary. Never returns a frame inside a word.
    """
    silence_candidates = _silence_frames_in_window(cut_frame, silence, window=window)
    best = _nearest_to(cut_frame, silence_candidates, window=window)
    if best is not None:
        return best

    gap_candidates = _clean_frames_in_window(cut_frame, words, window=window)
    best = _nearest_to(cut_frame, gap_candidates, window=window)
    if best is not None:
        return best

    # Nothing editorial to anchor the sound cut to (no words, no silence in reach) -> coincide
    # with the picture cut so the recommendation is an honest "hard".
    return video_frame


def _silence_frames_in_window(
    cut_frame: int,
    silence: Sequence[tuple[int, int]] | None,
    *,
    window: int,
) -> list[int]:
    """Every frame within ``±window`` of ``cut_frame`` that lies inside a silence interval."""
    if not silence:
        return []
    lo = cut_frame - window
    hi = cut_frame + window
    return [f for f in range(lo, hi + 1) if _in_silence(f, silence)]


def _clean_frames_in_window(
    cut_frame: int, words: Sequence[Word], *, window: int
) -> list[int]:
    """Clean word-gap / word-edge frames within ``±window`` of ``cut_frame`` (never mid-word).

    Reuses :func:`laura.analysis.editorial._gap_frames` (genuine inter-word silences plus the
    leading/trailing edges) and adds every word boundary, mirroring ``editorial._safe_frames``.
    Empty ``words`` -> ``[]`` (there is no transcript pause to anchor to; the caller falls back).
    """
    if not words:
        return []
    safe: set[int] = set(_gap_frames(words))
    for w in words:
        safe.add(w.start_frame)
        safe.add(w.end_frame)
    return [f for f in safe if abs(f - cut_frame) <= window]


def _nearest_to(cut_frame: int, candidates: Sequence[int], *, window: int) -> int | None:
    """Nearest candidate to ``cut_frame`` within ``±window``; ties to the lower frame; else None.

    Mirrors :func:`laura.analysis.editorial._nearest_within` so the audio-cut tie-breaking matches
    the editorial layer exactly (least drift, never later than necessary on a tie).
    """
    best: int | None = None
    best_dist = window + 1
    for c in candidates:
        dist = abs(c - cut_frame)
        if dist <= window and dist < best_dist:
            best, best_dist = c, dist
    return best


def _classify(offset: int) -> str:
    """Classify a split by the sign of ``offset = audio_frame - video_frame``.

    * ``|offset| <= HARD_OFFSET_TOLERANCE`` -> ``"hard"`` (picture and sound effectively coincide).
    * ``offset > 0`` (audio *after* video) -> ``"L"`` — the previous sound trails into the new
      picture (an L-cut).
    * ``offset < 0`` (audio *before* video) -> ``"J"`` — the next sound precedes its picture (a
      J-cut).
    """
    if abs(offset) <= HARD_OFFSET_TOLERANCE:
        return "hard"
    return "L" if offset > 0 else "J"


def plan_split_cut(
    cut_frame: int,
    words: list[Word],
    silence: list[tuple[int, int]] | None,
    *,
    window: int = DEFAULT_WINDOW,
    diff: Sequence[float] | None = None,
    video_path: Path | str | None = None,
    total_frames: int | None = None,
    frame_loader: FrameLoader = load_gray_frames_ffmpeg,
) -> SplitCut:
    """Plan a split edit for one cut: independent picture and sound frames, classified L/J/hard.

    ``video_frame`` is the frame-exact **picture** cut — the visual peak from a picture-biased
    :func:`laura.analysis.joint.joint_place` (``w_visual=1.0``, ``w_editorial=0.0``), so speech is
    ignored and only the strongest image change matters. The visual signal comes from the same
    seams as ``joint``/``eval_cut``: a precomputed ``diff`` aligned to the candidate band, a
    ``video_path`` decoded via ``frame_loader``, or — with neither — no signal, in which case the
    picture cut falls back to ``cut_frame`` unchanged.

    ``audio_frame`` is the **sound** cut: the nearest frame to ``cut_frame`` inside a detected real
    audio ``silence`` interval (best), else the nearest clean word-gap / word edge from ``words``,
    all within ``±window``; it never bisects a word. With no words and no silence it coincides with
    ``video_frame`` (a hard cut).

    ``offset = audio_frame - video_frame`` and ``kind`` follows its sign (``|offset| <= 1`` ->
    ``"hard"``; audio later -> ``"L"``; audio earlier -> ``"J"``). Defensive throughout: no
    words + no silence -> ``audio_frame == video_frame``, ``kind == "hard"``; an unreadable video
    leaves ``video_frame == cut_frame``.

    Raises ``ValueError`` for a negative ``window`` (mirroring ``joint_place``).
    """
    if window < 0:
        raise ValueError("window must be >= 0")

    # Picture cut: pure visual peak (w_editorial=0 -> speech ignored). joint_place returns the
    # original cut unchanged when there is no usable visual signal, which is exactly the fallback
    # we want for video_frame.
    video_frame, _score = joint_place(
        cut_frame,
        words,
        diff,
        window=window,
        w_visual=1.0,
        w_editorial=0.0,
        silence=silence,
        video_path=video_path,
        total_frames=total_frames,
        frame_loader=frame_loader,
    )

    audio_frame = _audio_frame(
        cut_frame, video_frame, words, silence, window=window
    )

    offset = audio_frame - video_frame
    return SplitCut(
        seq_cut=cut_frame,
        video_frame=video_frame,
        audio_frame=audio_frame,
        offset=offset,
        kind=_classify(offset),
    )


def plan_split_cuts(
    cuts: list[int],
    words: list[Word],
    silence: list[tuple[int, int]] | None,
    *,
    window: int = DEFAULT_WINDOW,
    video_path: Path | str | None = None,
    total_frames: int | None = None,
    frame_loader: FrameLoader = load_gray_frames_ffmpeg,
) -> list[SplitCut]:
    """Plan a split edit for each cut in ``cuts`` (see :func:`plan_split_cut`), in order.

    One :class:`SplitCut` per input cut; the visual signal for each is decoded around that cut via
    ``frame_loader`` (no precomputed ``diff`` seam here — that is a per-cut unit-test convenience).
    The picture cuts are computed independently, so two adjacent recommendations may overlap; this
    is a *recommendation* surface, not an applied edit, so no repacking is done.
    """
    return [
        plan_split_cut(
            cut,
            words,
            silence,
            window=window,
            video_path=video_path,
            total_frames=total_frames,
            frame_loader=frame_loader,
        )
        for cut in cuts
    ]
