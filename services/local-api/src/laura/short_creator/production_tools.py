"""Production-board infrastructure, board-read tools, and the VLM scene review (Slice 3).

``build_production_tool_specs`` wraps the Production Board (:mod:`.board`) as agent-facing
tools, mirroring :func:`laura.short_creator.toolset.build_tool_specs`'s closure pattern: ``db``
and ``board`` are captured once, each function stays a clean ``dict[str, Any]``-in/-out
callable, and its docstring becomes the tool's LLM-facing description. Pure — no autogen import
here; the agent wiring wraps these as ``FunctionTool``s elsewhere.

:class:`ProductionDeps` is the seam every model/ffmpeg call goes through: tests inject fakes,
production passes ``deps=None`` and each tool resolves the real backend lazily (mirrors
:func:`laura.short_creator.context.describe_moment`'s ``backend if backend is not None else
resolve_describe_backend()`` pattern) — so a missing model or proxy degrades instead of failing.

The Slice-3 core is ``review_scene``: it looks at 3 real frames (start/middle/end) of a
rough-cut scene with a VLM and writes a validated ``SceneReview`` to the board. It NEVER raises
and never blocks the pipeline — no configured backend, an empty reply, unparseable JSON, or
failed frame extraction all fall back to a **degraded** review built from the scene's transcript
alone (``degraded=True``, ``roi=None``, a neutral ``hook_score``), which is still written to the
board so the pipeline can proceed. Every tool function additionally catches unexpected
exceptions at its own boundary and reports them as ``{"ok": False, "reason": ...}`` instead of
raising — the agent loop must never die on a tool.

``save_storyline``/``save_script_chapter`` are the first *write* tools past scene review: they
pydantic-validate their input and report a malformed payload as ``{"ok": False, "errors": [...]}``
(loc+msg strings, agent-correctable) rather than raising. ``save_storyline`` additionally refuses
to save chapters that reference a scene without a review yet, or a review window the scene does
not have — a ``scene_numbers`` entry is a plain scene number (window 0) or ``{"scene": N,
"window": K}``, the same scene may recur with different windows, the same pair never twice.
``save_script_chapter`` merges by
chapter (other chapters' lines are kept, only the given chapter's lines are replaced) and, like
every ``board.save()`` call, invalidates every artifact downstream in the chain (voice, cutlist,
render report, qa report).

``synthesize_script_voice``/``build_cutlist`` are the Slice-3 core toolkit (Task 5).
``synthesize_script_voice`` turns the board's script into an mp3 + word-timings sidecar through
``deps.voice_backend`` (falling back to :func:`laura.short_creator.voice.resolve_voice_backend`,
same graceful-without-config pattern as the VLM). It, ``build_cutlist`` and ``render_production``
all read the script's lines through :func:`_lines_in_storyline_order` first — the STORYLINE's
scene order, not the order the lines happen to be written in — so the spoken narration, the
word-time map and the picture all walk the same sequence; a sha256 of that ORDERED text
(:func:`script_hash`) is the synthesis cache key, so an unrelated board change is a no-op re-run
while a storyline reorder (which changes the text) correctly busts the cache. ``build_cutlist``
is a *pure derivation* — no model or backend calls — turning storyline + script + voice into a
frame-accurate ``Cutlist``: one ``CutSegment`` per scene entry in arc order, cut from the review
window the entry references (offset, per-window roi — duration is budget-driven, never the
window's own duration), clamped inside its own source range, with an optional zoom timed to when
the scene's script line is actually spoken (the voice sidecar's word ``start_s``, via
:func:`line_starts`, offset by ``transition_lead_s``). ``zoom="off"`` is the user's framing
lever: it drops every roi and ``zoom_start_s`` regardless of the storyline's window references
(live 2026-08-04: "zeig das volle Bild" was otherwise not executable — the team would have had
to re-save the storyline without its window refs, and failed to in three follow-up runs).
Segment durations are coupled to that same sidecar: :func:`chapter_audio_windows` tiles the
voice track into per-chapter windows (boundaries midway between adjacent chapters' words, the
last chapter running to voice end + a short tail) and ``_scale_chapter_durations`` rescales each
chapter's base durations (:func:`_segment_duration_s`) to fill exactly its window — 2s floor per
segment, hard-capped at the scene's end-exclusive frame boundary — so the picture's chapter
boundaries track the continuous voice instead of drifting apart (chapter word-shares are not
chapter time-shares).

``save_contact_sheet`` is the Kontaktbogen checkpoint between cutlist and render: one grid PNG
over the cutlist (each segment's window-middle frame out of the editorial proxy, tiles in segment
order, labeled ``<order> S<scene_number>``), saved as the board's ``contact_sheet`` artifact —
the chain link between ``cutlist`` and ``render_report``, so a cutlist change archives and
invalidates the sheet like any other downstream artifact. Purely mechanical ffmpeg (PNG frames —
NOT mjpeg, which breaks on non-full-range YUV proxies — then the ``tile`` filter); labels are
drawn with ``drawtext`` from a small cross-platform font-candidate list and degrade to an
unlabeled sheet (``labeled=False``) when no usable font exists, rather than failing the
checkpoint. The user steers around this checkpoint purely via follow-up ``/message`` calls
("build up to the contact sheet, then stop" / "render now") — no new session state.

``render_production``/``review_export``/``save_qa_report`` close out the pipeline (Task 6).
``render_production`` turns the cutlist into the actual render call (``deps.render_segments``,
falling back to the real :func:`laura.mcp.tools.tool_render_segments`, resolved lazily like every
other backend seam): segments and an index-aligned zoom hint per segment, the board's voice as
the new audio track, captions on, blur-filled onto the board format's canvas — then polls the
resulting export
(bounded by ``RENDER_WAIT_SECONDS``, same pattern as :func:`laura.short_creator.toolset`'s
``export_status``) and grades it against three checks (``voice_fits``, ``export_ready``,
``has_voice_timings``); the ``RenderReport`` is saved regardless of the verdict so a failing
render stays inspectable, and the coding-agent's remedy for a too-short video is a longer cutlist
budget, NEVER a shortened voice (see the tool's own docstring). ``review_export`` grabs a few real
frames of the finished export (``_grab_video_frames``, one ffmpeg seek per timestamp — a failed
grab just yields fewer frames, never raises) and has the VLM QA-check each one individually,
degrading to an empty, explicitly-flagged note list without a configured backend. ``save_qa_report``
is the last board write: pydantic-validated verdict + findings, same self-correcting error contract
as ``save_storyline``/``save_script_chapter``.

``revert_artifact`` (Slice 4, Task 2) is the coding agent's one content-editing tool: it restores
an archived version of a singleton artifact as current and invalidates everything downstream of
it, exactly like a fresh ``board.save`` would — the normal pipeline tools then regenerate those
downstream artifacts. It reads ``downstream_of(name)`` and snapshots which of those are actually
present BEFORE calling ``board.revert`` (which deletes them), so the returned ``invalidated`` list
reflects what is about to disappear rather than what is already gone. An unknown artifact name
never reaches ``board.revert`` at all — it is rejected with the valid name list up front — and a
version that was never archived reports ``{"ok": False, "reason": "no archived <name> v<version>"}``
instead of raising, matching every other tool's error contract.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypeVar

from pydantic import ValidationError

from ..analysis.transition_review import extract_frames
from ..db import repos
from ..db.database import Database
from ..ingest.ffmpeg import FFmpegError, ffmpeg_bin, probe
from ..render.zoom import roi_to_window
from ..util import new_id, utcnow_iso
from . import context
from .board import Board, downstream_of
from .board_models import (
    BestWindow,
    Chapter,
    ContactSheet,
    ContactSheetTile,
    Cutlist,
    CutSegment,
    QaFinding,
    QaReport,
    RenderCheck,
    RenderReport,
    Roi,
    SceneCandidate,
    SceneReview,
    SceneSelection,
    Script,
    ScriptLine,
    Storyline,
    VoiceArtifact,
    VoiceSegment,
    as_scene_window,
    canvas_for,
    stage_direction_label,
)
from .board_models import content_hash as _content_hash
from .board_models import lines_in_storyline_order as _lines_in_storyline_order_impl
from .board_models import script_hash as _script_hash
from .board_models import script_text as _script_text
from .brain_tools import brain_root, read_brain_note, search_second_brain
from .describe import DescribeBackend, resolve_describe_backend
from .script_match import match_lines_to_scenes
from .toolset import RENDER_WAIT_SECONDS, ToolSpec
from .voice import VoiceBackend, resolve_voice_backend
from .voice_concat import (
    INTER_SCENE_GAP_S,
    concat_with_gaps,
    line_offsets,
    merge_word_timings,
    probe_duration_s,
)

# Signature of laura.mcp.tools.tool_render_segments — wired in from Task 6 on.
logger = logging.getLogger(__name__)

RenderSegmentsFn = Callable[..., dict[str, Any]]

_MIN_WINDOW_S = 0.01
_DEFAULT_WINDOW_S = 4.0
_SEGMENT_FLOOR_S = 2.0
_VOICE_TAIL_S = 0.6
_DEFAULT_HOOK_SCORE = 5
_SNIPPET_CHARS = 300
_DESCRIPTION_PREVIEW_CHARS = 200

_REVIEW_PROMPT = (
    "You are reviewing {n} frames (start/middle/end) of scene {scene} from a screen "
    "recording ({duration_s:.1f}s). Transcript of the scene: \"{snippet}\".\n"
    "Reply ONLY with a JSON object, no prose, no code fences:\n"
    "{{\"description\": str (what is on screen),\n"
    "  \"whats_happening\": str (what changes across the frames),\n"
    "  \"hook_score\": int 0-10 (how visually gripping for a cold viewer; this is a SCREEN "
    "recording, so a held/static frame whose content is clearly readable is a strong hook — "
    "stillness is not a penalty and must not lower the score; judge what the frame SAYS, "
    "not how much it moves),\n"
    "  \"windows\": [{{\"offset_s\": float, \"duration_s\": float, \"roi\": {{\"x\": float, "
    "\"y\": float, \"w\": float, \"h\": float}} | null}}] (1-4 strong moments, STRONGEST "
    "FIRST, non-overlapping, offsets relative to scene start; a long scene with several "
    "distinct beats should list each beat as its own window; a held/static screen whose "
    "content stays readable is ONE window spanning the whole readable stretch — never chop "
    "stillness into sub-second beats; {roi_rule}),\n"
    "  \"legibility_notes\": str}}"
)

# A roi is a CROP. Whether that rescues the shot or ruins it depends entirely on how the
# canvas compares to the footage, so the reviewer is told which situation it is in.
_ROI_RULE_CROP = (
    "roi is the normalized 0-1 box around the ONE region a viewer must read DURING that "
    "window, null if the whole frame matters"
)
_ROI_RULE_NATIVE = (
    "roi must be null: the output canvas already matches this footage's shape, so the frame "
    "is shown as recorded. A roi here would CROP content away rather than enlarge it. Set one "
    "ONLY if a small detail is genuinely unreadable at full size and nothing else in the frame "
    "matters"
)
# Below this ratio of canvas-aspect to source-aspect, the source cannot fill the canvas and
# would sit in a letterbox — that is when cropping to a region earns its keep. 16:9 into 9:16
# scores 0.32; 16:9 into 1:1 scores 0.56; 16:9 into 16:9 scores 1.0.
_ROI_NEEDED_BELOW = 0.75


def _roi_rule(*, src_w: int, src_h: int, out_w: int, out_h: int) -> str:
    """The roi instruction for this canvas/source pair.

    Live finding: on a 16:9 screen recording rendered to a 16:9 canvas, the reviewer cropped an
    org chart captioned "36 agents, 9 teams" to 2% of its area — the scale was the whole point.
    The old wording ("the ONE region a viewer must read") is v1 reel advice: correct when a wide
    frame has to survive a narrow canvas, actively harmful when it does not.

    Unknown source dimensions keep the v1 rule — a silent switch on missing metadata would be
    worse than the behaviour that has shipped so far.
    """
    if src_w <= 0 or src_h <= 0:
        return _ROI_RULE_CROP
    return (
        _ROI_RULE_NATIVE
        if (out_w / out_h) / (src_w / src_h) >= _ROI_NEEDED_BELOW
        else _ROI_RULE_CROP
    )

_RENDER_POLL_INTERVAL_S = 2.0
_VOICE_FIT_TOLERANCE_S = 0.05
# A hard cap on real renders per production — the net for a loop that keeps revising for a
# reason the budget fix cannot remove (a QA verdict, the model second-guessing itself). One
# render plus one revise round is the charter; past that, render_production ships the last cut
# instead of spending another render, and the turn budget stops the rest. A prompt saying "one
# revise round" did not hold — gpt-5-mini rendered four times, gpt-5.5 more.
# The cap guards against the TEAM's loops, not against the USER: an explicit follow-up message
# raises it via ProductionDeps.max_render_cycles = follow_up_render_cap(board).
_MAX_RENDER_CYCLES = 2
# How much of a storyline may stay unwritten before the render reports a failing check. Some
# slack is deliberate — a short closing beat the author folded into the previous chapter is not
# a broken film — but a majority of the planned seconds without narration is.
_SILENT_SHARE_LIMIT = 0.4
# How much of the usable length an arc must plan for before save_storyline warns. The plan is
# the ceiling: a film cannot come out longer than the seconds its chapters asked for.
_PLAN_COVERAGE_MIN = 0.8


def _probe_duration(path: str) -> float | None:
    """The measured length of a rendered file in seconds, or None when it cannot be measured.

    Measuring must never break a finished render: a missing ffprobe, a file the renderer wrote
    somewhere else, a container without a duration — all of them mean "unknown", which the
    report says plainly instead of falling back to a number that would read as measured.
    """
    try:
        if not Path(path).is_file():
            return None
        raw = probe(path).get("format", {}).get("duration")
        seconds = float(raw)
    except Exception:  # ffprobe failure, unparsable JSON, no duration field
        return None
    return seconds if seconds > 0.0 else None


def _shape_of(out_w: int, out_h: int) -> str:
    if out_h > out_w:
        return "vertical"
    return "square" if out_h == out_w else "landscape"


def _qa_prompt(out_w: int, out_h: int) -> str:
    """The QA prompt for the canvas actually rendered.

    A VLM told to judge framing "inside the vertical canvas" will invent vertical faults on a
    landscape frame, so the shape is stated rather than assumed.
    """
    shape = _shape_of(out_w, out_h)
    return (
        f"You are QA-checking a finished {shape} video ({out_w}x{out_h}) before it ships. Look "
        "at this single frame and reply in ONE short, concrete sentence: is the subject/text "
        f"legible, well framed inside the {shape} canvas, and free of visual glitches? Name "
        'anything a viewer would notice as wrong; say "looks fine" if nothing is.'
    )


@dataclass
class ProductionDeps:
    """Injectable seams — tests pass fakes, production passes None (=resolve real)."""

    describe_backend: DescribeBackend | None = None
    frame_extract: Callable[[Database, str, list[int]], list[bytes]] | None = None
    voice_backend: VoiceBackend | None = None  # used from Task 5 on
    render_segments: RenderSegmentsFn | None = None  # used from Task 6 on
    probe_duration: Callable[[str], float | None] | None = None  # measures the rendered file
    # render_production's revision cap (None = _MAX_RENDER_CYCLES). run_production raises it to
    # follow_up_render_cap(board) for a run carrying an explicit user follow-up message — the
    # cap exists to stop the team's own revision loops, never an operator-requested change.
    max_render_cycles: int | None = None


# --- reply parsing + clamping (pure) ------------------------------------------------------------


def _parse_review_reply(text: str) -> dict[str, Any] | None:
    """Best-effort JSON object out of a VLM reply.

    Takes the substring from the first ``{`` to the last ``}`` (which strips code fences and any
    surrounding prose as a side effect) and ``json.loads`` it. ``None`` on any failure — garbage
    text, unbalanced braces, or a JSON value that isn't an object.
    """
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp_hook_score(value: Any) -> int:
    """Coerce to an int and clamp to [0, 10]; any unusable value falls back to the neutral score."""
    try:
        score = int(round(float(value)))
    except (TypeError, ValueError):
        return _DEFAULT_HOOK_SCORE
    return max(0, min(10, score))


def _clamp_best_window(raw: Any, scene_duration_s: float) -> BestWindow:
    """Clamp a proposed window inside ``[0, scene_duration_s]`` (plus its own optional roi).

    Length is preserved where possible — only the offset gives way when the requested window
    would run past the end of the scene — and length itself is capped to the scene's own
    duration, with a small floor so the window is always positive (a pydantic requirement).
    """
    offset_raw, length_raw = 0.0, min(_DEFAULT_WINDOW_S, scene_duration_s)
    roi: Roi | None = None
    if isinstance(raw, dict):
        offset_raw = _as_float(raw.get("offset_s"), offset_raw)
        length_raw = _as_float(raw.get("duration_s"), length_raw)
        roi = _clamp_roi(raw.get("roi"))
    length = max(_MIN_WINDOW_S, min(length_raw, scene_duration_s))
    max_offset = max(0.0, scene_duration_s - length)
    offset = max(0.0, min(offset_raw, max_offset))
    return BestWindow(offset_s=offset, duration_s=length, roi=roi)


_MAX_WINDOWS = 4


def _clamp_windows(raw: Any, scene_duration_s: float) -> list[BestWindow]:
    """1-4 clamped, non-overlapping windows out of a VLM reply, strongest-first.

    Each dict item is clamped like :func:`_clamp_best_window`; an item that (after clamping)
    overlaps an earlier, stronger accepted window is DISCARDED — not merged — so every kept
    window stays one distinct moment with its own roi (touching windows are fine, the
    end-exclusive analog). Anything unusable degrades to the single default window,
    mirroring ``_clamp_best_window(None)``.
    """
    items = raw if isinstance(raw, list) else [raw]
    accepted: list[BestWindow] = []
    for item in items:
        if len(accepted) == _MAX_WINDOWS:
            break
        if not isinstance(item, dict):
            continue
        window = _clamp_best_window(item, scene_duration_s)
        end = window.offset_s + window.duration_s
        if any(
            window.offset_s < w.offset_s + w.duration_s - 1e-9 and w.offset_s < end - 1e-9
            for w in accepted
        ):
            continue
        accepted.append(window)
    if not accepted:
        return [_clamp_best_window(None, scene_duration_s)]
    return accepted


def _clamp_roi(raw: Any) -> Roi | None:
    """The proposed ROI clamped to [0,1] per axis, or None if it stays invalid after clamping
    (e.g. ``x + w > 1``, or a missing/non-numeric field) — degrade to "whole frame matters"."""
    if not isinstance(raw, dict):
        return None
    try:
        x, y = float(raw["x"]), float(raw["y"])
        w, h = float(raw["w"]), float(raw["h"])
    except (KeyError, TypeError, ValueError):
        return None
    x, y = min(max(x, 0.0), 1.0), min(max(y, 0.0), 1.0)
    w, h = min(max(w, 0.0), 1.0), min(max(h, 0.0), 1.0)
    try:
        return Roi(x=x, y=y, w=w, h=h)
    except ValidationError:
        return None


def _three_frames(start: int, end_exclusive: int) -> list[int]:
    """Integer start/middle/end frame numbers inside ``[start, end_exclusive)``."""
    last = max(start, end_exclusive - 1)
    mid = min(max(start + (end_exclusive - start) // 2, start), last)
    return [start, mid, last]


# --- scene + asset lookups -----------------------------------------------------------------------


def _fps(db: Database, asset: dict[str, Any]) -> float:
    rate = context._frame_rate(db, asset)
    if rate is None or rate[1] == 0:
        return 30.0
    return rate[0] / rate[1]


def _default_extract_frames(db: Database, asset_id: str, frames: list[int]) -> list[bytes]:
    """Extract JPEGs for (asset, frame) from the asset's proxy — one ffmpeg call per frame.

    Mirrors ``context._default_extract`` (same proxy-path + rate resolution) but for a whole
    list of frames in a single call. No proxy or no rate -> ``[]`` (the caller degrades).
    """
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        return []
    proxy = context._proxy_path(db, asset_id)
    rate = context._frame_rate(db, asset)
    if proxy is None or rate is None:
        return []
    refs = [(asset_id, frame) for frame in frames]
    return extract_frames({asset_id: proxy}, refs, rate_num=rate[0], rate_den=rate[1])


def _grab_video_frames(path: Path, at_seconds: list[float]) -> list[bytes]:
    """One JPEG (bytes) per timestamp, seeked directly out of a finished export file.

    Same ffmpeg invocation as :func:`laura.analysis.transition_review.extract_frames`
    (``-ss <t> -frames:v 1 -f image2pipe``) but against an arbitrary video path instead of an
    asset's proxy — ``review_export`` grabs a few frames of the RENDERED short, not the source.
    A failed seek/decode for one timestamp (path missing, ffmpeg missing, timestamp past the end)
    is silently skipped, so the returned list may be shorter than ``at_seconds`` or empty; this
    never raises — a QA review always degrades instead of blocking the pipeline.
    """
    out: list[bytes] = []
    for t in at_seconds:
        cmd = [
            ffmpeg_bin(),
            "-v",
            "error",
            "-ss",
            f"{max(0.0, t):.6f}",
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-q:v",
            "2",
            "-f",
            "image2pipe",
            "-vcodec",
            "mjpeg",
            "-",
        ]
        try:
            proc = subprocess.run(cmd, capture_output=True)  # noqa: S603
        except OSError:
            continue
        if proc.returncode == 0 and proc.stdout:
            out.append(proc.stdout)
    return out


# --- contact sheet (ffmpeg-only: PNG frames + tile filter) ---------------------------------------

_SHEET_TILE_WIDTH = 480
# First existing candidate wins; labels degrade to "none" (labeled=False) when the list misses —
# a font must never be a hard dependency of the checkpoint.
_SHEET_FONT_CANDIDATES = (
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeui.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
)


def _grid_shape(n: int) -> tuple[int, int]:
    """Near-square grid for ``n`` tiles: ``cols = ceil(sqrt(n))``, rows to fit."""
    cols = max(1, math.ceil(math.sqrt(n)))
    return cols, max(1, math.ceil(n / cols))


def _find_fontfile() -> str | None:
    return next((c for c in _SHEET_FONT_CANDIDATES if Path(c).is_file()), None)


def _filter_quote(value: str) -> str:
    """Quote a value for use inside an ffmpeg filter argument: forward slashes (Windows drive
    paths), the filter-level specials (``:``, ``'``) backslash-escaped, single-quoted."""
    normalized = value.replace("\\", "/")
    escaped = normalized.replace("'", r"\'").replace(":", r"\:")
    return f"'{escaped}'"


def _label_filter(label: str, fontfile: str) -> str:
    return (
        f"drawtext=fontfile={_filter_quote(fontfile)}:text={_filter_quote(label)}"
        ":x=10:y=8:fontsize=30:fontcolor=white:box=1:boxcolor=black@0.55:boxborderw=8"
    )


def _run_ffmpeg_quiet(args: list[str]) -> bool:
    """Run one ffmpeg invocation, success as bool — an OSError (no ffmpeg) is just failure."""
    try:
        proc = subprocess.run([ffmpeg_bin(), "-v", "error", "-y", *args], capture_output=True)  # noqa: S603
    except OSError:
        return False
    return proc.returncode == 0


def _probe_video_dims(path: str) -> tuple[int, int]:
    """The video stream's own width/height, ``(0, 0)`` when unprobeable.

    ``(0, 0)`` flows into the letterbox-never-crop fallback downstream — a sheet
    without zoom framing beats no sheet at all.
    """
    try:
        streams = probe(path).get("streams", [])
    except FFmpegError:
        return 0, 0
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        return 0, 0
    return int(video.get("width") or 0), int(video.get("height") or 0)


def _tile_filter(
    *, roi: tuple[float, float, float, float] | None, src_w: int, src_h: int,
    out_w: int, out_h: int, fontfile: str | None, label: str,
) -> str:
    """The ``-vf`` for one tile, framed the way the RENDER frames that segment.

    A tile showing the bare source frame cannot show the faults it exists to catch — a crop
    cutting text, or content sitting tiny inside the letterbox. So a segment with a roi is
    cropped through ``roi_to_window`` (the renderer's own function, not a lookalike), and one
    without is padded into the output aspect, which is exactly what the blur-fill path leaves
    on screen minus the cosmetic blur. ``out_w``/``out_h`` are the production's canvas: a
    landscape delivery must be sheeted landscape or the sheet gates the wrong frame.
    """
    tile_h = round(_SHEET_TILE_WIDTH * out_h / out_w / 2) * 2
    if roi is not None:
        x, y, w, h = roi_to_window(roi, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h)
        vf = f"crop={w}:{h}:{x}:{y},scale={_SHEET_TILE_WIDTH}:{tile_h}"
    else:
        vf = (
            f"scale={_SHEET_TILE_WIDTH}:{tile_h}:force_original_aspect_ratio=decrease,"
            f"pad={_SHEET_TILE_WIDTH}:{tile_h}:(ow-iw)/2:(oh-ih)/2:black"
        )
    if fontfile is not None:
        vf += "," + _label_filter(label, fontfile)
    return vf


def _extract_sheet_tiles(
    proxy: Path,
    times_labels: list[tuple[float, str, tuple[float, float, float, float] | None]],
    tiles_dir: Path,
    fontfile: str | None,
    *,
    src_w: int,
    src_h: int,
    out_w: int,
    out_h: int,
) -> tuple[bool, bool, int | None]:
    """One PNG per (timestamp, label, roi) into ``tiles_dir/tile_%03d.png``, framed as rendered.

    PNG, never mjpeg — mjpeg breaks on non-full-range YUV proxies. Labels use ``drawtext``;
    when the labeled pass fails (broken font, ffmpeg without freetype) the WHOLE sheet is
    retried once unlabeled instead of mixing labeled and plain tiles. Returns
    ``(ok, labeled, failed_index)``."""
    labeled = fontfile is not None
    while True:
        failed: int | None = None
        for i, (t, label, roi) in enumerate(times_labels):
            vf = _tile_filter(
                roi=roi, src_w=src_w, src_h=src_h, out_w=out_w, out_h=out_h,
                fontfile=fontfile if labeled else None, label=label,
            )
            args = [
                "-ss",
                f"{max(0.0, t):.6f}",
                "-i",
                str(proxy),
                "-frames:v",
                "1",
                "-vf",
                vf,
                str(tiles_dir / f"tile_{i:03d}.png"),
            ]
            if not _run_ffmpeg_quiet(args):
                failed = i
                break
        if failed is None:
            return True, labeled, None
        if labeled:
            labeled = False  # drawtext is the usual culprit — one plain retry of the whole sheet
            continue
        return False, False, failed


def _compose_sheet_grid(tiles_dir: Path, cols: int, rows: int, out_png: Path) -> bool:
    """All ``tile_%03d.png`` frames tiled into ONE ``cols x rows`` grid PNG (empty cells stay
    padding — the tile filter flushes a partial last grid at EOF)."""
    return _run_ffmpeg_quiet(
        [
            "-framerate",
            "1",
            "-i",
            str(tiles_dir / "tile_%03d.png"),
            "-frames:v",
            "1",
            "-vf",
            f"tile={cols}x{rows}",
            str(out_png),
        ]
    )


def _resolve_scene(db: Database, asset_id: str, scene_number: int) -> tuple[int, int, str] | None:
    """One rough-cut scene's SOURCE frame range (end-exclusive) + its transcript text.

    ``None`` when the asset, its rough cut, or the 1-based scene number does not exist.
    """
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        return None
    timeline = repos.get_or_create_asset_rough_cut(db, str(asset["project_id"]), asset_id)
    scenes = repos.list_scenes(db, str(timeline["id"]))
    by_number = {int(s["order_index"]) + 1: s for s in scenes}
    scene = by_number.get(int(scene_number))
    if scene is None:
        return None
    clips = repos.list_timeline_clips(db, str(timeline["id"]))
    ranges = context._scene_src_ranges(
        clips,
        seq_in=int(scene["seq_in_frame"]),
        seq_out_exclusive=int(scene["seq_out_frame_exclusive"]),
    )
    if not ranges:
        return None
    src_start, src_end_exclusive = ranges[0][0], ranges[-1][1]
    run = repos.get_latest_transcript_run(db, asset_id)
    segments = repos.get_transcript(db, asset_id, str(run["id"])) if run is not None else []
    in_scene = context._segments_in_ranges(segments, ranges)
    text = " ".join(str(seg.get("text") or "").strip() for seg in in_scene).strip()
    return src_start, src_end_exclusive, text


def _expected_scenes(db: Database, asset_id: str) -> list[int]:
    """Scene numbers this asset's rough cut has — the reviews ``review_scene`` must cover."""
    result = context.scene_transcripts(db, asset_id)
    if not result.get("ok"):
        return []
    return [int(s["scene_number"]) for s in result.get("scenes", [])]


# --- validation-error formatting (pure) -----------------------------------------------------


def _validation_errors(exc: ValidationError) -> list[str]:
    """Up to 5 ``"loc: msg"`` strings out of a ValidationError — compact enough for the agent
    to read and self-correct on, without dumping pydantic's full error payload."""
    return [f"{'.'.join(str(part) for part in e['loc'])}: {e['msg']}" for e in exc.errors()[:5]]


# --- scene selection gate (Gate S, pure) ------------------------------------------------------


def scene_selection_block_reason(
    selection: SceneSelection | None, referenced_scenes: list[int], *, gate_on: bool
) -> str | None:
    """Why a storyline/script write must be refused under Gate S — or None to proceed.

    Structural, not prompt (I2 lesson): the contract that only USER-picked scenes may be
    written lives at the write site.
    """
    if not gate_on:
        return None
    if not isinstance(selection, SceneSelection):
        return (
            "scene selection gate: no proposal on the board yet — call "
            "propose_scene_selection first; the user then confirms in chat"
        )
    if selection.confirmed_utc is None:
        return (
            "scene selection gate: awaiting the user's pick — the user must confirm "
            "the scene selection in chat before storyline or script are written"
        )
    allowed = set(selection.selected_scene_numbers)
    stray = sorted(set(referenced_scenes) - allowed)
    if stray:
        return (
            f"scenes {stray} are outside the user's confirmed selection "
            f"{sorted(allowed)} — only selected scenes may be used"
        )
    return None


# --- voice + cutlist (pure) -------------------------------------------------------------------


# Moved to board_models next to script_hash: the played order DEFINES the text identity every
# derived artifact records, and the board's own staleness check must reproduce it exactly.
# (Review finding: a second copy of the ordering here and a raw-order hash in the board made
# fresh renders read stale.) Aliased for this module's many callers.
_lines_in_storyline_order = _lines_in_storyline_order_impl


script_text = _script_text


_Reparentable = TypeVar("_Reparentable", Script, VoiceArtifact)


def _reparent(artifact: _Reparentable, updates: dict[str, str]) -> _Reparentable:
    """Copy ``artifact`` with its recorded parent hashes refreshed to CURRENT values.

    Only keys the artifact already records are touched, and only when ``updates`` supplies a
    replacement for them — an unrecorded key stays absent and an empty ``parents`` (a
    pre-provenance board) stays empty. Never fabricates provenance that was not already there.
    Used by ``save_storyline``'s structure-preserving carry-over: it re-saves an unchanged
    script/voice against a NEW storyline, so their ``parents["storyline"]`` (and, since content
    hashes fold in the WHOLE model including ``parents``, the script's own re-stamp changes its
    hash too — anything that recorded that hash, i.e. voice's ``parents["script"]``, must move
    with it or a corrected script would itself read as a drifted parent) need to move with it.
    """
    if not artifact.parents:
        return artifact
    refreshed = {**artifact.parents, **{k: v for k, v in updates.items() if k in artifact.parents}}
    return artifact.model_copy(update={"parents": refreshed})


def _chapter_structure(storyline: Storyline) -> list[tuple[int, list[tuple[int, int]]]]:
    """What the SCRIPT structurally depends on: chapters and their (scene, window) refs.

    Messages, targets and the red thread are presentation — changing them does not make a
    written script wrong. Refs are normalized via ``as_scene_window`` so plain ``1`` and
    ``{"scene": 1, "window": 0}`` compare equal: notation is not structure.
    """
    return [
        (chapter.chapter, [as_scene_window(entry) for entry in chapter.scene_numbers])
        for chapter in storyline.arc
    ]


def silent_chapters(script: Script, storyline: Storyline | None) -> list[int]:
    """Storyline chapters the script never wrote a line for.

    Live finding: a storyline planned six chapters summing to the full target and the script
    covered two. The film came out at 63% of its length. Nothing noticed, because
    ``save_script_chapter`` validates the chapter it is handed and nobody ever asked which
    chapters were never handed to it — the same gap as the stale render, one link up: a valid
    artifact that does not correspond to the one above it.

    Coverage only. Whether a chapter has ENOUGH words is the budget's question, not this one.
    """
    if storyline is None:
        return []
    written = {line.chapter for line in script.lines}
    return [chapter.chapter for chapter in storyline.arc if chapter.chapter not in written]


def numbered_chapters(chapters: list[Any]) -> list[Any]:
    """Fill in each chapter's missing ``chapter`` number from its position in the list.

    Live 2026-08-02 (Drive-Test): ``chapter: Field required`` came back 42, 53 and 22 times
    across three runs — by a wide margin the most repeated failure in the pipeline, and the one
    that actually burned the turn budgets. The required field is called ``chapter`` and sits
    inside a list called ``chapters``: an author reads ``chapters: [{...}]``, takes the object
    to BE the chapter, and never supplies the number. That is not a wrong guess so much as the
    only reading the shape suggests.

    The arc is an ordered sequence, so position IS the number wherever one is missing. An
    explicit number is never touched (an author that renumbers on purpose keeps its intent),
    and a non-dict entry is passed through untouched so it still fails validation as before.
    """
    return [
        {**raw, "chapter": index} if isinstance(raw, dict) and "chapter" not in raw else raw
        for index, raw in enumerate(chapters, start=1)
    ]


def _with_material_hint(reply: dict[str, Any], hint: str | None) -> dict[str, Any]:
    """Attach ``material_hint`` to a rejection when there is one to give (never otherwise)."""
    if hint is not None:
        reply["material_hint"] = hint
    return reply


def silent_seconds_share(script: Script, storyline: Storyline | None) -> tuple[list[int], float]:
    """The silent chapters and the share of the storyline's planned seconds they carry.

    Counting chapters alone would weigh a 2s sting the same as the 25s payoff that actually made
    the live film a fifth of its target. The share is what says how much of the story is gone.
    """
    silent = silent_chapters(script, storyline)
    if storyline is None or not silent:
        return silent, 0.0
    planned = sum(chapter.target_seconds for chapter in storyline.arc)
    if planned <= 0.0:
        return silent, 0.0
    unwritten = sum(c.target_seconds for c in storyline.arc if c.chapter in set(silent))
    return silent, unwritten / planned


# script_hash lives with the model it hashes (board_models) so the board can compute it too —
# it is the one identity every derived artifact is checked against, and a second copy of that
# rule is precisely the drift these checks exist to catch. Re-exported here for its callers.
script_hash = _script_hash


def line_starts(
    lines: list[ScriptLine], words: list[dict[str, Any]]
) -> dict[tuple[int, int], float]:
    """Each line's ``(chapter, scene_number)`` mapped to its FIRST word's ``start_s``.

    ``words`` (a voice backend's timings sidecar) are assumed to be the whitespace tokens of
    exactly :func:`script_text` of these SAME ``lines``, in the SAME order — so each line
    "claims" as many words as it has whitespace-split tokens, walking the shared word stream
    forward in that order. A line is absent from the result if the word stream runs out before
    reaching it (e.g. a sidecar shorter than the script) — callers treat a missing entry as "no
    known start" (skip the zoom for that line).

    Two lines can share a ``(chapter, scene_number)`` key (a scene spoken by multiple lines,
    identity-paired into one cutlist segment by VS3) — the FIRST line's start wins, never a
    later one's: the only consumer anchors a zoom to when the scene's narration BEGINS, and a
    later write here would silently point it at a later sub-line's moment instead (or drop the
    zoom entirely, if the later time falls too close to the segment's end).
    """
    out: dict[tuple[int, int], float] = {}
    idx = 0
    for line in lines:
        n_tokens = len(line.text.split())
        key = (line.chapter, line.scene_number)
        if n_tokens and idx < len(words) and key not in out:
            out[key] = _as_float(words[idx].get("start_s"), 0.0)
        idx += n_tokens
    return out


def _read_words(timings_path: str | None) -> list[dict[str, Any]]:
    """The voice sidecar's ``words`` list, or ``[]`` for a missing/unreadable/malformed file."""
    if not timings_path:
        return []
    try:
        return list(json.loads(Path(timings_path).read_text(encoding="utf-8"))["words"])
    except (OSError, ValueError, KeyError, TypeError):
        return []


# Fitted over every ElevenLabs synthesis this board produced:
#    78 words -> 38.5s (0.49 s/word) | 89 -> 57.3s (0.64)
#   179 words -> 108.4s (0.61)       | 228 -> 158.0s (0.69)
# The per-word rate genuinely moves with word length, punctuation and phrasing, so this
# rate carries a real +/-20% spread and is a STARTING budget, never a promise.
#
# A per-line pause term was tried first and looked exact on the one sample it was fitted
# to — then missed another by 9s (+23% on a third). TTS pauses at punctuation, not at line
# breaks: the 179-word script had 51 lines but only ~20 audible pauses. Dropped on purpose.
# Seconds per spoken word, per language — measured on real ElevenLabs syntheses of this
# project's own scripts, never guessed. German: 0.58 (fitted over four scripts, +-20%
# spread). English: 0.41 (aggregate over three real syntheses — 308w->104.8s, 407w->156.4s,
# 462w->219.9s = 481.1s/1177w = 0.409). An earlier 0.340 came from ONE terse hand-written
# script and was optimistic: natural agent prose, with names and numbers and pauses, runs
# slower. German is slower still because its compounds are long words.
_SECONDS_PER_WORD: dict[str, float] = {"German": 0.58, "English": 0.41}
# ISO-ish short codes agents actually pass ("mach das in english" → the team sent "en" live,
# 2026-08-05). ``_SECONDS_PER_WORD`` and ``BoardMeta.language`` speak full English names, so a
# raw code silently falls back to the German rate. Matched case-insensitively; anything not in
# this map passes through unchanged.
_LANGUAGE_ALIASES: dict[str, str] = {"en": "English", "de": "German"}
_DEFAULT_LANGUAGE = "German"
_VOICE_RATE_TOLERANCE = 0.20


def seconds_per_word(language: str) -> float:
    """The measured TTS rate for *language*.

    An unmeasured language falls back to German's rate: the pipeline shipped on it, and an
    invented number would be worse than a known one nobody can audit.
    """
    return _SECONDS_PER_WORD.get(language, _SECONDS_PER_WORD[_DEFAULT_LANGUAGE])


def estimate_voice_seconds(words: int, language: str = _DEFAULT_LANGUAGE) -> float:
    """Roughly how long TTS speaks *words* in *language*. Good to about +/-20% —
    synthesize to know."""
    return words * seconds_per_word(language)


def word_budget_for(target_seconds: float, language: str = _DEFAULT_LANGUAGE) -> int:
    """A STARTING word count for *target_seconds* in *language*.

    Write to it ONCE, synthesize, then correct against the MEASURED ``voice_s`` — the rate
    varies +/-20% per script. Iterating the script by feel instead is what burned a whole
    job on 34 saves that never reached a render.
    """
    return max(0, int(target_seconds / seconds_per_word(language)))


# Words too ordinary to carry a claim — a term made only of these is prose, not a capability.
# Kept as readable prose rather than 150 quoted literals.
_STOPWORD_TEXT = (
    "a an the and or but so then than that this these those it its is are was were be been "
    "am do does did have has had can could will would shall should may might must of in on "
    "at to for with from by as if not no yes you your we our they their he she his her i me "
    "my what which who whom when where why how all any both each few more most other some "
    "such only own same too very just now here there also into over under again once about "
    "because while during before after above below up down out off further one two three "
    "you're it's don't cannot every real live full clear exact whole plain single next "
    "screen show shows shown see seen watch run runs running work works step steps thing "
    "things use uses used make makes made need needs needed give gives given ask asks asked "
    "human agent agents system demo video first second last"
)
_GROUNDING_STOPWORDS = frozenset(_STOPWORD_TEXT.split())
# Invented capabilities land on a small set of nouns — a fabricated claim is almost always
# "<qualifier> <capability-noun>": "prompt histories", "health-check endpoint", "signed-off
# config". Scanning for those heads instead of every phrase is what makes the check precise
# enough to be worth reading: ordinary prose ("Engineers inspect agent", "the picker lists")
# does not end on one, so it never trips.
_CAPABILITY_NOUNS = (
    "endpoint|endpoints|hash|hashes|schema|schemas|id|ids|config|configs|configuration"
    "|metadata|knob|knobs|history|histories|contract|contracts|trail|trails|budget|budgets"
    "|artifact|artifacts|checksum|checksums|manifest|manifests|token|tokens|sdk|api|apis"
    "|webhook|webhooks|dashboard|dashboards|telemetry|audit|audits|ledger|ledgers|policy"
    "|policies|quota|quotas|namespace|namespaces|registry|registries"
)
_CLAIM_PHRASE = re.compile(
    rf"\b((?:[a-z][a-z0-9-]*\s+){{1,2}}(?:{_CAPABILITY_NOUNS}))\b", re.IGNORECASE
)


def _seen_in(word: str, ground: str) -> bool:
    """Was *word* seen, allowing a trailing plural? "lists" counts as seen for "list"."""
    if word in ground:
        return True
    return len(word) > 3 and word.endswith("s") and word[:-1] in ground


def ungrounded_terms(script_text_: str, grounding_text: str) -> list[str]:
    """Multi-word technical claims in *script_text_* that appear nowhere in *grounding_text*.

    Live finding: filling a word budget from held screens, the author invented nine
    capabilities — "health-check endpoint", "code hashes", "prompt histories" — none of them
    in any review. The film's own claim is that the system does not fabricate.

    Reports rather than rejects: no mechanical check can judge whether prose is true, only
    whether a specific term was ever seen. False positives are cheap (the author reads the
    list); a silent invention is not.
    """
    ground = grounding_text.lower()
    found: list[str] = []
    seen: set[str] = set()
    for match in _CLAIM_PHRASE.finditer(script_text_):
        phrase = match.group(1).strip()
        words = [w for w in re.split(r"[\s-]+", phrase.lower()) if w]
        if len(words) < 2 or all(w in _GROUNDING_STOPWORDS for w in words):
            continue
        # Only flag when NO content word of the phrase was ever seen — one shared term is
        # enough to call it grounded, so "SQLite for state" passes on "SQLite".
        # The head noun carries the claim: "code hashes" is invented even though a code editor
        # is on screen. Judge the head, not the qualifier that happens to be grounded.
        if _seen_in(words[-1], ground):
            continue
        key = phrase.lower()
        if key not in seen:
            seen.add(key)
            found.append(phrase)
    return found


# The two directions are not equally bad. A voice that runs long truncates the ending (the
# export is cut to the shorter stream); one that runs short holds the last frames a moment
# longer. So the word budget aims BELOW the usable length rather than at it. 10% covers the
# overshoot actually measured (0.431 s/word against the table's 0.41) with room to spare, and
# still spends ~90% of the footage. The same margin lowers the pressure that made the author
# pad a grounded line with an invented one to reach the count.
_BUDGET_HEADROOM = 0.10


def budget_words_for(
    usable_seconds: float,
    language: str = _DEFAULT_LANGUAGE,
    *,
    measured_rate_wps: float | None = None,
) -> int:
    """The word count to ASK FOR — the usable length minus headroom for the rate's variance.

    ``word_budget_for`` converts seconds to words at the language heuristic; this is the number
    a script should actually be written to. Live finding: budgeting the full usable length put
    the voice 2s past the video and voice_fits failed.

    A ``measured_rate_wps`` (words/second, from :func:`_measured_rate_wps` — this board's OWN
    last synthesis) overrides the language heuristic entirely: ``int(usable_seconds *
    measured_rate_wps)``, no headroom. Calibration finding 2026-08-04: a 93-word heuristic
    budget (assuming ~1.55 w/s) actually spoke at ~1.9 w/s and the film came in 10s short — the
    heuristic's headroom was compensating for the WRONG uncertainty (a per-language guess) and a
    rate measured on this exact board's own text needs none of it.
    """
    if measured_rate_wps is not None and measured_rate_wps > 0:
        return max(0, int(usable_seconds * measured_rate_wps))
    return word_budget_for(usable_seconds * (1.0 - _BUDGET_HEADROOM), language)


def _measured_rate_wps(
    storyline: Storyline, script: Script | None, voice: VoiceArtifact | None
) -> float | None:
    """The speech rate (words/second) measured from the board's own last voice synthesis, or
    None when there is nothing to measure from or the measurement would be stale.

    Trustworthy only when ``voice`` was synthesized from the CURRENT ``script`` — checked the
    same way ``build_cutlist`` checks it, by re-hashing the script in storyline (played) order
    and comparing against ``voice.script_hash``. A script edited after the last synthesis makes
    the stored ``voice_s`` describe words that are no longer what will be spoken; smuggling that
    stale rate into a fresh budget would be worse than the heuristic it replaces, so this falls
    back to None (caller uses the heuristic) rather than guess.
    """
    if script is None or voice is None or voice.voice_s is None or voice.voice_s <= 0:
        return None
    ordered_lines = _lines_in_storyline_order(script, storyline)
    if voice.script_hash != script_hash(ordered_lines):
        return None
    total_words = len(script_text(ordered_lines).split())
    if total_words <= 0:
        return None
    return total_words / voice.voice_s


# The under-budget gate (live 2026-08-04): three 45-60s targets shipped as 20-34s films
# because the team wrote ~50-word scripts against a computed 93-word allocation.
# script_budget's docstring says "call it ONCE before writing" — prompts do not bind, so the
# floor lives on the write path (same lesson as the storyline-order guard). Both bounds must
# hold before the gate speaks: the chapter is below RATIO of its allocation AND the missing
# words are at least MISSING_S of film — tiny chapters (a 7-word hook) must never nag. The
# allocation itself is capacity-limited (usable = min(target, material)), so the gate never
# demands words the footage could not cover; what it blocks is leaving budgeted film unwritten.
_BUDGET_GATE_RATIO = 0.7
_BUDGET_GATE_MISSING_S = 5.0


def segment_capacity_seconds(window: BestWindow, scene_duration_s: float) -> float:
    """How long the CUT can make this segment — the stretch cap build_cutlist applies.

    The reviewed window is a quality mark, not a length limit: build_cutlist starts a segment
    at the window's offset and stretches it toward the scene's end when the voice needs it.
    Budgeting against the window instead measured a different quantity than the cut delivers —
    chapter 3 was offered two words for a 1s window inside a 45s scene the cut could fill.
    """
    return min(scene_duration_s, max(_SEGMENT_FLOOR_S, scene_duration_s - window.offset_s))


def chapter_word_budgets(
    material_per_chapter: dict[int, float],
    language: str = _DEFAULT_LANGUAGE,
    *,
    measured_rate_wps: float | None = None,
) -> dict[int, int]:
    """Words each chapter may spend, from the material that chapter's own windows hold.

    Live finding: a correct TOTAL hides a broken distribution. 161s of voice against 170s of
    material looked healthy while chapter 3 carried 26.8s of narration for a 1.0s reviewed
    window and chapters 5 and 6 left 48s unused. The cutlist cannot cover voice its scenes do
    not hold, so the video came out 13s short of the audio however well the total added up.

    A chapter budgeted at almost nothing is not a bug in this function — it is the storyline
    saying that beat has one second of reviewed footage, which is the thing worth seeing.

    ``measured_rate_wps``, when given, is forwarded to :func:`budget_words_for` for every
    chapter — the same measured rate throughout, never a mix of chapters on the heuristic and
    one on a measured number.
    """
    return {
        chapter: budget_words_for(seconds, language, measured_rate_wps=measured_rate_wps)
        for chapter, seconds in material_per_chapter.items()
    }


def allocate_chapter_seconds(
    capacity_per_chapter: dict[int, float], *, usable_seconds: float
) -> dict[int, float]:
    """Share the film's usable length out across the chapters, in proportion to capacity.

    Capacity says what a chapter CAN hold; the target says what the film SHOULD run. Budgeting
    every chapter at its own capacity asks for the sum of the capacities — 266s of script for a
    174s film on the live board. Scaling down keeps each chapter inside its own capacity, so the
    cut can still cover whatever the voice turns out to be. Scarce capacity is left alone: when
    the footage is short the film is short, which is the honest answer.
    """
    total = sum(capacity_per_chapter.values())
    if total <= usable_seconds or total <= 0.0:
        return dict(capacity_per_chapter)
    scale = usable_seconds / total
    return {chapter: seconds * scale for chapter, seconds in capacity_per_chapter.items()}


def plan_coverage(*, planned_seconds: float, usable_seconds: float) -> dict[str, Any] | None:
    """How much of the usable length the storyline's chapters actually plan for.

    The arc is the film's ceiling: the cut is built per chapter against the chapter's target, so
    seconds nobody planned are seconds nobody shoots. Live 2026-08-02: a 60s short was planned
    as 3+12+25=40s and nothing weighed the plan against the length it was for. Measured against
    the SAME usable length ``script_budget`` uses, not the raw target — under-planning a target
    the footage cannot reach anyway is the honest answer, not a mistake.
    """
    if usable_seconds <= 0.0:
        return None
    pct = planned_seconds / usable_seconds * 100.0
    out: dict[str, Any] = {
        "planned_seconds": round(planned_seconds, 1),
        "usable_seconds": round(usable_seconds, 1),
        "coverage_pct": round(pct, 1),
    }
    if planned_seconds < usable_seconds * _PLAN_COVERAGE_MIN:
        out["plan_warning"] = (
            f"this arc plans {planned_seconds:.1f}s of the {usable_seconds:.1f}s the material "
            f"can carry ({pct:.0f}%) — the film cannot come out longer than its plan. Add a "
            "chapter or give the existing ones longer targets before writing the script."
        )
    return out


def usable_budget_seconds(*, material_seconds: float, target_seconds: float) -> float:
    """The length the script may actually fill: the smaller of the target and the material.

    Two bounds, and the tighter one wins. Never more than the target (a longer material must
    not overshoot the requested length). Never more than the material (the footage cannot hold
    more voice than there is video to cover it — asking for more is the unsatisfiable
    voice_fits that made the agent thrash the whole render chain). Zero material means no
    storyline yet, so the target is the only bound.
    """
    if material_seconds <= 0.0:
        return target_seconds
    return min(target_seconds, material_seconds)


def storyline_material_seconds(windows: Iterable[tuple[BestWindow, float]]) -> float:
    """The longest video worth cutting from these ``(window, scene_duration_s)`` refs.

    This is the QUALITY ceiling — the sum of the moments the reviewer actually marked, each
    floored at the segment floor and clamped inside its scene. Segments *can* stretch past
    their window when the voice runs long (see ``_scale_chapter_durations``), but that pads
    with footage nobody reviewed. Write narration against this number, not the hard one.
    """
    return sum(
        min(max(window.duration_s, _SEGMENT_FLOOR_S), scene_duration_s)
        for window, scene_duration_s in windows
    )


def _segment_duration_s(
    *, target_seconds: float, n_scenes: int, scene_duration_s: float
) -> float:
    """One segment's BASE cutlist length: the chapter's per-segment time budget, floored at
    2s and clamped inside the scene's own duration.

    The review window is deliberately NOT a length input — it marks WHERE the segment starts
    and WHICH beat is cut. Using its duration as a weight let a reel-trained reviewer's 0.5s
    windows starve held screens of screen time (spec 2026-07-20-window-bias-design.md §2;
    baseline: the shipped films cut a 45s org chart from three 0.5s windows).

    With a usable voice sidecar these are only the WEIGHTS that ``_scale_chapter_durations``
    rescales to fill the chapter's audio window (:func:`chapter_audio_windows`); without one
    they are the segment durations themselves — decoupled there too, a cap only in one path
    would re-import the bias."""
    budget = target_seconds / n_scenes
    return min(max(_SEGMENT_FLOOR_S, budget), scene_duration_s)


def chapter_audio_windows(
    ordered_lines: list[ScriptLine],
    words: list[dict[str, Any]],
    *,
    tail_s: float = _VOICE_TAIL_S,
) -> dict[int, tuple[float, float]]:
    """Each chapter's share of the continuous voice track, as ``{chapter: (start_s, end_s)}``
    windows tiling the track from 0.0 to voice end + ``tail_s``.

    The word stream is walked exactly like :func:`line_starts` (each line claims as many words
    as it has whitespace tokens, in ``ordered_lines`` order — pass the storyline-ordered lines),
    giving each chapter a raw extent from its first claimed word's ``start_s`` to its last
    claimed word's ``end_s``. The boundary between two adjacent chapters is the MIDPOINT of the
    gap between them (the inter-chapter pause belongs half to each side); the first chapter
    starts at 0.0 and the last runs to the voice end plus ``tail_s`` of breathing room.

    A chapter whose lines claim no words (sidecar shorter than the script) gets NO entry —
    callers fall back to the target_seconds budget for it. Empty ``words`` -> ``{}``. Boundaries
    are clamped monotonically non-decreasing, so windows stay valid even for a degenerate
    (out-of-order) sidecar.
    """
    if not words:
        return {}
    extents: dict[int, tuple[float, float]] = {}
    chapter_order: list[int] = []
    idx = 0
    for line in ordered_lines:
        n_tokens = len(line.text.split())
        claimed = words[idx : idx + n_tokens]
        idx += n_tokens
        if not claimed:
            continue
        start = _as_float(claimed[0].get("start_s"), 0.0)
        end = _as_float(claimed[-1].get("end_s"), start)
        if line.chapter in extents:
            first_start, last_end = extents[line.chapter]
            extents[line.chapter] = (first_start, max(last_end, end))
        else:
            extents[line.chapter] = (start, end)
            chapter_order.append(line.chapter)
    if not chapter_order:
        return {}

    voice_end = _as_float(words[-1].get("end_s"), 0.0)
    windows: dict[int, tuple[float, float]] = {}
    boundary = 0.0
    for pos, chapter in enumerate(chapter_order):
        if pos + 1 < len(chapter_order):
            next_boundary = (extents[chapter][1] + extents[chapter_order[pos + 1]][0]) / 2.0
        else:
            next_boundary = voice_end + tail_s
        next_boundary = max(next_boundary, boundary)
        windows[chapter] = (boundary, next_boundary)
        boundary = next_boundary
    return windows


def _scale_chapter_durations(
    base_s: list[float],
    upper_s: list[float],
    total_s: float,
    *,
    floor_s: float = _SEGMENT_FLOOR_S,
) -> list[float]:
    """``base_s`` rescaled by ONE common factor so the clamped result sums to ``total_s`` (the
    chapter's audio-window length), each item clamped to ``[min(floor_s, upper), upper]``.

    The common-factor-then-clamp form keeps segments proportional to their base durations
    wherever no bound binds; the factor is found by bisection (the clamped sum is continuous and
    non-decreasing in it). Infeasible totals saturate instead of violating a bound: a window
    shorter than the summed floors leaves everything at its floor (the video overhangs the
    chapter's audio — accepted), one longer than the summed caps leaves everything at its cap
    (the scene material simply ends — accepted).
    """
    if not base_s:
        return []
    lows = [min(floor_s, upper) for upper in upper_s]

    def clamped(factor: float) -> list[float]:
        return [
            min(max(base * factor, low), upper)
            for base, low, upper in zip(base_s, lows, upper_s, strict=True)
        ]

    if total_s <= sum(lows):
        return lows
    if total_s >= sum(upper_s):
        return list(upper_s)
    lo = 0.0
    hi = max(
        (upper / base for base, upper in zip(base_s, upper_s, strict=True) if base > 0),
        default=1.0,
    )
    hi += 1.0
    for _ in range(60):
        mid = (lo + hi) / 2.0
        if sum(clamped(mid)) < total_s:
            lo = mid
        else:
            hi = mid
    return clamped(hi)


# --- tool builder ----------------------------------------------------------------------------


def _renders_so_far(board: Board) -> int:
    """How many real renders this production has done — the highest render_report version ever
    reached. ``board.save`` stamps ``max(current, *archived) + 1``, so the number survives the
    invalidation an upstream re-save causes: it is the true render count, not the current one.
    """
    archived = board.versions("render_report")
    current = board.load("render_report")
    current_v = current.version if isinstance(current, RenderReport) else 0
    return max([0, current_v, *archived])


def follow_up_render_cap(board: Board) -> int:
    """The render-cycle cap for a run carrying an explicit user follow-up message.

    ``_MAX_RENDER_CYCLES`` is a runaway-loop backstop against the TEAM re-rendering on its own
    judgment; an operator-requested change is not a runaway loop. Live 2026-08-04: the user
    asked for a reframe after the cap was spent, and the cap silently shipped the old cut.
    One render above what has already been spent — never below the plain cap, so a board with
    render budget left gains nothing extra. Wired in by ``run_production`` through
    ``ProductionDeps.max_render_cycles``, so each follow-up run grants at most ONE re-render
    and the backstop holds again right after.
    """
    return max(_MAX_RENDER_CYCLES, _renders_so_far(board) + 1)


def _storyline_material(
    db: Database, board: Board, asset_id: str, storyline: Storyline
) -> tuple[dict[int, float], list[int], int]:
    """``(material_seconds PER CHAPTER, missing_scenes, resolved_count)``.

    Per chapter, not just the total: a correct total hides a broken distribution — 161s of
    voice against 170s of material while one chapter carried 27s of narration for a 1s window.

    material is the sum of the reviewed windows (the longest video worth cutting). Shared by
    ``script_budget`` and ``get_script`` so the word count and the shortfall it is checked
    against are computed from the SAME number — the two diverging is what let the shortfall
    drive the script past the footage.
    """
    reviews_by_scene = {r.scene_number: r for r in board.scene_reviews()}
    asset = repos.get_asset(db, asset_id)
    fps = _fps(db, asset) if asset is not None else 30.0
    resolved: list[tuple[BestWindow, float]] = []
    missing: list[int] = []
    per_chapter: dict[int, float] = {}
    for chapter in storyline.arc:
        windows: list[tuple[BestWindow, float]] = []
        for entry in chapter.scene_numbers:
            scene_number, window_idx = as_scene_window(entry)
            scene = _resolve_scene(db, asset_id, scene_number)
            if scene is None:
                missing.append(scene_number)
                continue
            src_start, src_end, _text = scene
            scene_duration_s = (src_end - src_start) / fps
            review = reviews_by_scene.get(scene_number)
            if review is not None and window_idx < len(review.windows):
                window = review.windows[window_idx]
            else:
                window = BestWindow(
                    offset_s=0.0, duration_s=min(_DEFAULT_WINDOW_S, scene_duration_s)
                )
            windows.append((window, scene_duration_s))
            resolved.append((window, scene_duration_s))
        # Capacity, not the reviewed window: this must be the length the CUT can deliver, or
        # the script is budgeted against a different quantity than the video is built from.
        per_chapter[chapter.chapter] = sum(
            segment_capacity_seconds(w, scene_len) for w, scene_len in windows
        )
    return per_chapter, missing, len(resolved)


def build_production_tool_specs(
    db: Database, board: Board, *, asset_id: str, deps: ProductionDeps | None = None
) -> list[ToolSpec]:
    """Board-bound production tools: board status, scene context, VLM scene review, reviews.

    ``db``/``board``/``asset_id`` are captured in the closures (same pattern as
    :func:`laura.short_creator.toolset.build_tool_specs`); ``deps`` injects the VLM/ffmpeg seams
    for tests, defaulting to the real backends in production.
    """
    d = deps or ProductionDeps()

    def board_status() -> dict[str, Any]:
        """Current production-board state: artifact versions and the resume point."""
        try:
            expected = _expected_scenes(db, asset_id)
            status = board.status()
            status["resume_point"] = board.resume_point(expected)
            status["expected_scenes"] = expected
            return {"ok": True, **status}
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def get_scene_context(scene_number: int) -> dict[str, Any]:
        """A rough-cut scene's transcript text and source frame range (no VLM call)."""
        try:
            resolved = _resolve_scene(db, asset_id, scene_number)
            if resolved is None:
                return {"ok": False, "reason": "unknown scene"}
            src_start, src_end_exclusive, text = resolved
            asset = repos.get_asset(db, asset_id)
            fps = _fps(db, asset) if asset is not None else 30.0
            duration_s = (src_end_exclusive - src_start) / fps
            return {
                "ok": True,
                "scene_number": scene_number,
                "src_start_frame": src_start,
                "src_end_frame_exclusive": src_end_exclusive,
                "duration_s": round(duration_s, 2),
                "text": text,
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def get_scene_transcript(scene_number: int) -> dict[str, Any]:
        """The scene's verbatim source transcript — the ground truth script lines are built
        from. Each spoken segment with scene-relative start/end seconds, speaker and text,
        plus verbatim_words: the exact word stream inside the scene's frame range (word rows
        past the scene boundary are excluded even when their segment straddles it). Quote or
        tightly paraphrase THESE words and the review's visible facts — a claim supported by
        neither is invented. Empty segments mean the scene has no speech: narrate what the
        review says is visible instead."""
        try:
            asset = repos.get_asset(db, asset_id)
            if asset is None:
                return {"ok": False, "reason": "unknown scene"}
            timeline = repos.get_or_create_asset_rough_cut(
                db, str(asset["project_id"]), asset_id
            )
            scenes = repos.list_scenes(db, str(timeline["id"]))
            by_number = {int(s["order_index"]) + 1: s for s in scenes}
            scene = by_number.get(int(scene_number))
            if scene is None:
                return {"ok": False, "reason": "unknown scene"}
            clips = repos.list_timeline_clips(db, str(timeline["id"]))
            ranges = context._scene_src_ranges(
                clips,
                seq_in=int(scene["seq_in_frame"]),
                seq_out_exclusive=int(scene["seq_out_frame_exclusive"]),
            )
            if not ranges:
                return {"ok": False, "reason": "unknown scene"}
            src_start, src_end_exclusive = ranges[0][0], ranges[-1][1]
            fps = _fps(db, asset)
            run = repos.get_latest_transcript_run(db, asset_id)
            segments = (
                repos.get_transcript(db, asset_id, str(run["id"])) if run is not None else []
            )
            in_scene = context._segments_in_ranges(segments, ranges)

            def _word_in_ranges(word: dict[str, Any]) -> bool:
                start_frame, end_frame = word.get("start_frame"), word.get("end_frame")
                if start_frame is None or end_frame is None:
                    return False
                s, e = int(start_frame), int(end_frame)
                return any(e > lo and s < hi for lo, hi in ranges)

            seg_rows: list[dict[str, Any]] = []
            verbatim: list[str] = []
            for seg in in_scene:
                start_s = max(0.0, (int(seg["start_frame"]) - src_start) / fps)
                end_s = max(start_s, (int(seg["end_frame"]) - src_start) / fps)
                seg_rows.append(
                    {
                        "start_s": round(start_s, 2),
                        "end_s": round(end_s, 2),
                        "speaker": seg.get("speaker_label"),
                        "text": str(seg.get("text") or "").strip(),
                    }
                )
                verbatim.extend(
                    text
                    for w in seg.get("words") or []
                    if _word_in_ranges(w) and (text := str(w.get("text") or "").strip())
                )
            return {
                "ok": True,
                "scene_number": int(scene_number),
                "src_start_frame": src_start,
                "src_end_frame_exclusive": src_end_exclusive,
                "duration_s": round((src_end_exclusive - src_start) / fps, 2),
                "segments": seg_rows,
                "verbatim_words": " ".join(verbatim),
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    # Per-run refinement budget for healthy reviews. A VLM is a stochastic oracle: asked
    # twice about the same frames it gives a different answer, not a better one. A live run
    # reviewed every scene six times — 36 VLM calls, 24 minutes, exactly one of which fixed
    # anything — and died at the turn budget with no storyline. Degraded reviews are exempt:
    # fixing degradation is the one re-review with a real target.
    healthy_re_reviews: dict[int, int] = {}

    def review_scene(scene_number: int) -> dict[str, Any]:
        """Look at 3 real frames (start/middle/end) of a scene with the VLM and write a
        validated SceneReview to the board. The VLM proposes 1-4 non-overlapping strong
        windows (strongest first, each with its own optional roi); windows[0] is the
        best_window, and the storyline may reference any window by index. Never fails the
        pipeline: without a configured VLM, with an empty or unparseable reply, or when no
        frames could be extracted, it writes a transcript-only *degraded* review instead
        (``degraded=True``, neutral hook_score, one default window, no roi) so downstream
        steps can still proceed."""
        try:
            resolved = _resolve_scene(db, asset_id, scene_number)
            if resolved is None:
                return {"ok": False, "reason": "unknown scene"}
            existing = next(
                (r for r in board.scene_reviews() if r.scene_number == scene_number), None
            )
            if existing is not None and not existing.degraded:
                if healthy_re_reviews.get(scene_number, 0) >= 1:
                    return {
                        "ok": False,
                        "reason": (
                            f"scene {scene_number} already has a healthy review "
                            f"(v{existing.version}, {len(existing.windows)} windows) and was "
                            "already refined once this run. Another look yields a different "
                            "answer, not a better one — move on: save_storyline."
                        ),
                    }
                healthy_re_reviews[scene_number] = healthy_re_reviews.get(scene_number, 0) + 1
            src_start, src_end_exclusive, text = resolved
            asset = repos.get_asset(db, asset_id)
            fps = _fps(db, asset) if asset is not None else 30.0
            duration_s = (src_end_exclusive - src_start) / fps
            snippet = text[:_SNIPPET_CHARS]

            extractor = d.frame_extract if d.frame_extract is not None else _default_extract_frames
            frame_numbers = _three_frames(src_start, src_end_exclusive)
            frames = extractor(db, asset_id, frame_numbers)

            backend = (
                d.describe_backend if d.describe_backend is not None else resolve_describe_backend()
            )
            model_name = type(backend).__name__ if backend is not None else ""

            parsed: dict[str, Any] | None = None
            if backend is not None and frames and backend.available():
                _, (out_w, out_h) = canvas_for(board.meta().format)
                prompt = _REVIEW_PROMPT.format(
                    n=len(frames),
                    scene=scene_number,
                    duration_s=duration_s,
                    snippet=snippet,
                    roi_rule=_roi_rule(
                        src_w=int((asset or {}).get("width") or 0),
                        src_h=int((asset or {}).get("height") or 0),
                        out_w=out_w,
                        out_h=out_h,
                    ),
                )
                reply = backend.describe(frames, prompt)
                parsed = _parse_review_reply(reply) if reply else None
                if reply and parsed is None:
                    # A reply that came back but did not parse must not vanish silently: a
                    # truncated-JSON bug (num_predict too small) once degraded five of six
                    # scenes with empty descriptions and not one line of evidence anywhere.
                    logger.warning(
                        "review_scene %s: VLM reply unparseable (%d chars): %.120s",
                        scene_number,
                        len(reply),
                        reply,
                    )

            if parsed is None:
                review = SceneReview(
                    scene_number=scene_number,
                    src_start_frame=src_start,
                    src_end_frame_exclusive=src_end_exclusive,
                    description=snippet,
                    whats_happening="",
                    hook_score=_DEFAULT_HOOK_SCORE,
                    best_window=BestWindow(
                        offset_s=0.0, duration_s=min(_DEFAULT_WINDOW_S, duration_s)
                    ),
                    roi=None,
                    legibility_notes="",
                    degraded=True,
                    model=model_name,
                    created_utc=utcnow_iso(),
                )
            else:
                raw_windows = parsed.get("windows")
                if raw_windows is None:  # legacy single-window reply shape
                    raw_windows = [parsed.get("best_window")]
                windows = _clamp_windows(raw_windows, duration_s)
                review = SceneReview(
                    scene_number=scene_number,
                    src_start_frame=src_start,
                    src_end_frame_exclusive=src_end_exclusive,
                    description=str(parsed.get("description") or ""),
                    whats_happening=str(parsed.get("whats_happening") or ""),
                    hook_score=_clamp_hook_score(parsed.get("hook_score")),
                    best_window=windows[0],
                    windows=windows,
                    roi=_clamp_roi(parsed.get("roi")),
                    legibility_notes=str(parsed.get("legibility_notes") or ""),
                    degraded=False,
                    model=model_name,
                    created_utc=utcnow_iso(),
                )

            version = board.save_scene_review(review)
            return {
                "ok": True,
                "scene_number": scene_number,
                "version": version,
                "degraded": review.degraded,
                "hook_score": review.hook_score,
                "windows": len(review.windows),
                "roi": review.roi.model_dump() if review.roi is not None else None,
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def get_reviews() -> dict[str, Any]:
        """All saved scene reviews, compact: scene, hook score, degraded flag, has-roi
        (review-level or any window), the review's windows (0-based index, offset/duration,
        per-window has_roi — a storyline entry {"scene": N, "window": K} plays window K),
        and a description blurb."""
        try:
            reviews = board.scene_reviews()
            return {
                "ok": True,
                "reviews": [
                    {
                        "scene_number": r.scene_number,
                        "hook_score": r.hook_score,
                        "degraded": r.degraded,
                        "has_roi": r.roi is not None
                        or any(w.roi is not None for w in r.windows),
                        "windows": [
                            {
                                "window": i,
                                "offset_s": w.offset_s,
                                "duration_s": w.duration_s,
                                "has_roi": w.roi is not None,
                            }
                            for i, w in enumerate(r.windows)
                        ],
                        "description": r.description[:_DESCRIPTION_PREVIEW_CHARS],
                    }
                    for r in reviews
                ],
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def _material_hint(n_chapters: int) -> str | None:
        """What the reviews can actually carry, when the arc asks for more than exists.

        Live 2026-08-02 (Drive-Test): an uncut screen recording is ONE scene and its review
        proposed ONE window. The prompt asks for the four-chapter arc; a (scene, window) pair
        may be used once; so four chapters need four distinct refs and there was one. The agent
        alternated between "window 1 is referenced but scene 1 has 1" and "window 0 is
        referenced more than once" for 269 turns and the run ended with no film.

        The schema never demanded four chapters — ``arc`` is ``min_length=1``. Only the prompt
        did. So the rejection carries the arithmetic and names the legal way out; an error that
        says only what is forbidden leaves the caller to guess what is allowed.
        """
        reviews = board.scene_reviews()
        available = sum(len(r.windows) for r in reviews)
        if available == 0 or n_chapters <= available:
            return None
        per_scene = ", ".join(f"scene {r.scene_number}: {len(r.windows)}" for r in reviews)
        return (
            f"these reviews offer {available} distinct scene/window ref(s) ({per_scene}), and a "
            f"(scene, window) pair may be used only once — so at most {available} chapter(s) "
            f"can be built from them, not {n_chapters}. Use fewer chapters (an arc of one is "
            "valid) or have review_scene propose more windows for a long scene first."
        )

    def set_board_language(language: str) -> dict[str, Any]:
        """Switch the production language (script/voice/captions) for this board.

        Call this FIRST when the user asks for another language, then rewrite every
        chapter via save_script_chapter — it picks the new language up automatically.
        Short codes normalize to the full English name before anything else ("en" ->
        "English", "de" -> "German", case-insensitive; other values pass through
        unchanged) — ``_SECONDS_PER_WORD`` only knows full names, so a raw "en" on the
        board would silently price every chapter at the German speaking rate.
        Validation mirrors the router's letters/spaces/length shape, but is deliberately
        looser: it accepts any Unicode letter (``str.isalpha()``), not just ASCII, with the
        same 2-char floor ``BoardMeta.language`` enforces (the 32-char ceiling is the
        tool's own, tighter than the model's 40).

        A switch AWAY from the previous language (with a script already on the board)
        also re-arms Gate B via ``board.clear_script_approval()``: the script text itself
        is untouched by this call, so leaving the old approval standing would let an
        unrewritten, wrong-language script keep reading as approved-current — the user's
        "mach das in english" would silently no-op on a script nobody rewrote, and the
        stale meta would poison the NEXT follow-up too. ``Board.status()``'s
        ``language_mismatch`` flag stays True until a chapter is re-saved in the new
        language.

        The success result carries a ``note`` restating the new-language order: the
        production task prompt is built at run START and keeps saying e.g. "the script
        MUST be written in German" after a mid-run switch — live 2026-08-05 the team
        switched to English correctly and then rewrote every chapter in German anyway,
        because the stale prompt outweighed the meta. A fresh tool RESULT outranks stale
        prompt text, so the instruction rides along here."""
        try:
            cleaned = (language or "").strip()
            cleaned = _LANGUAGE_ALIASES.get(cleaned.lower(), cleaned)
            if len(cleaned) < 2 or len(cleaned) > 32 or not all(
                c.isalpha() or c == " " for c in cleaned
            ):
                return {"ok": False, "reason": "language must be an English language "
                                               "name (letters/spaces, 2-32 chars)"}
            previous = board.meta().language
            board.set_language(cleaned)
            if cleaned != previous and isinstance(board.load("script"), Script):
                board.clear_script_approval()
            return {
                "ok": True,
                "previous": previous,
                "language": cleaned,
                "note": (
                    f"Language switched. Write ALL chapter text in {cleaned} from now "
                    "on, regardless of earlier language instructions in this run."
                ),
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def propose_scene_selection(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Propose scene candidates for the user's Gate-S pick and save them to the board.
        Each candidate: {"scene_number", "description", "transcript_snippet", "rationale",
        "recommended"} — frame range and thumb frame are resolved server-side from the scene
        itself, so pass only what you judged. At least one candidate must be recommended
        (the pre-checked suggestion). Saving a new proposal archives the old one and
        invalidates everything downstream; the run then STOPS and waits for the user.
        Refuses on a gate-off board (nothing reads this artifact there) and once the user
        has already confirmed a pick — a confirmed selection is final; the user changes it
        via the confirm endpoint/chat, never via a new proposal from this tool."""
        try:
            if not board.meta().scene_gate:
                return {
                    "ok": False,
                    "reason": "scene selection gate is not enabled for this session",
                }
            existing = board.load("scene_selection")
            if isinstance(existing, SceneSelection) and existing.confirmed_utc is not None:
                return {
                    "ok": False,
                    "reason": (
                        "the user already confirmed scenes "
                        f"{sorted(existing.selected_scene_numbers)} — that pick is final; "
                        "changing it happens through the user's confirm (chat), not a new "
                        "proposal from this tool"
                    ),
                }
            built: list[SceneCandidate] = []
            for cand in candidates:
                scene_number = int(cand.get("scene_number", 0))
                resolved = _resolve_scene(db, asset_id, scene_number)
                if resolved is None:
                    return {"ok": False, "reason": f"scene {scene_number} does not exist"}
                src_start, src_end, _text = resolved
                built.append(
                    SceneCandidate(
                        scene_number=scene_number,
                        src_start_frame=src_start,
                        src_end_frame_exclusive=src_end,
                        thumb_frame=src_start + (src_end - src_start) // 2,
                        description=str(cand.get("description") or "").strip()
                        or "(keine Bildanalyse verfügbar)",
                        transcript_snippet=str(
                            cand.get("transcript_snippet") or ""
                        ).strip(),
                        rationale=str(cand.get("rationale") or "").strip(),
                        recommended=bool(cand.get("recommended", False)),
                    )
                )
            try:
                selection = SceneSelection(candidates=built)
            except ValidationError as exc:
                return {"ok": False, "errors": _validation_errors(exc)}
            if not any(c.recommended for c in built):
                return {
                    "ok": False,
                    "reason": "mark at least one candidate recommended — it is the "
                    "pre-checked suggestion the user confirms with one click",
                }
            version = board.save("scene_selection", selection)
            return {
                "ok": True,
                "version": version,
                "candidates": len(built),
                "note": (
                    "proposal saved — STOP now. The user picks scenes in chat; the run "
                    "resumes automatically after their confirmation."
                ),
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def save_storyline(red_thread: str, chapters: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate and save the short's storyline (red thread + chapter arc) to the board.
        Each chapter's ``chapter`` number may be omitted — it is taken from the entry's position
        in the list, so pass the chapters in arc order.
        A scene_numbers entry is a plain scene number (= that review's primary window 0) or
        {"scene": N, "window": K} to play review window K (0-based, see get_reviews); the
        same scene may appear several times with DIFFERENT windows, the same (scene, window)
        pair only once. Every referenced scene must already have a review on the board and
        every referenced window must exist in that review — rejected with exactly the
        scenes/refs to fix so the agent reviews or corrects them first. A malformed chapter
        is rejected with field-level validation errors instead of raising."""
        try:
            try:
                storyline = Storyline(
                    red_thread=red_thread,
                    arc=[Chapter(**c) for c in numbered_chapters(chapters)],
                )
            except ValidationError as exc:
                return _with_material_hint(
                    {"ok": False, "errors": _validation_errors(exc)}, _material_hint(len(chapters))
                )
            refs = [
                as_scene_window(entry)
                for chapter in storyline.arc
                for entry in chapter.scene_numbers
            ]
            reviews = {r.scene_number: r for r in board.scene_reviews()}
            missing = sorted({scene for scene, _window in refs if scene not in reviews})
            if missing:
                return {"ok": False, "reason": f"scenes without review: {missing}"}
            bad_refs = sorted({(s, w) for s, w in refs if w >= len(reviews[s].windows)})
            if bad_refs:
                detail = "; ".join(
                    f"scene {s} has {len(reviews[s].windows)} windows "
                    f"(0..{len(reviews[s].windows) - 1}) but window {w} is referenced"
                    for s, w in bad_refs
                )
                return _with_material_hint(
                    {"ok": False, "reason": detail}, _material_hint(len(chapters))
                )
            # Gate S: under an active gate, only scenes the USER confirmed may be written into
            # the storyline — structural, not a prompt rule (I2 lesson: prompts do not bind).
            meta = board.meta()
            selection = board.load("scene_selection")
            selection = selection if isinstance(selection, SceneSelection) else None
            block = scene_selection_block_reason(
                selection, [s for s, _w in refs], gate_on=meta.scene_gate
            )
            if block is not None:
                return {"ok": False, "reason": block}
            if meta.scene_gate and selection is not None:
                storyline = storyline.model_copy(
                    update={"parents": {"scene_selection": _content_hash(selection)}}
                )
            # A storyline save invalidates the whole chain below — including a script that is
            # still perfectly right. Live finding (run 48d5660a): a re-save changing ONLY
            # messages and target_seconds wiped a finished script and its voice; the author
            # rebuilt from memory and the run died at the turn budget with no film. When the
            # chapter STRUCTURE (chapters + scene/window refs) is unchanged, the script and
            # voice are carried over; the cutlist stays invalidated — targets do change cuts.
            old_storyline = board.load("storyline")
            old_script = board.load("script")
            old_voice = board.load("voice")
            version = board.save("storyline", storyline)
            result: dict[str, Any] = {"ok": True, "version": version}
            # Weigh the plan against the length it is for, here, where it can still be fixed
            # for free. Guarded on its own: the save already happened, so a material lookup
            # that fails must cost the report, never turn a completed save into ok: False.
            try:
                per_chapter, _missing, _n = _storyline_material(db, board, asset_id, storyline)
                coverage = plan_coverage(
                    planned_seconds=sum(c.target_seconds for c in storyline.arc),
                    usable_seconds=usable_budget_seconds(
                        material_seconds=sum(per_chapter.values()),
                        target_seconds=board.meta().target_seconds,
                    ),
                )
            except Exception:  # the storyline is saved either way — reporting is best-effort
                coverage = None
            if coverage is not None:
                result.update(coverage)
            # Carry over ONLY when the save actually invalidated something. An identical
            # re-save is a complete no-op (board.save short-circuits it) and the script is
            # still on the board — "rescuing" it then would bump versions and wipe the very
            # cutlist the no-op rule exists to protect.
            if isinstance(old_script, Script) and board.load("script") is None:
                same_structure = isinstance(
                    old_storyline, Storyline
                ) and _chapter_structure(old_storyline) == _chapter_structure(storyline)
                if same_structure:
                    # The carry-over is itself a write against the NEW storyline: it re-asserts
                    # script/voice as valid for it, so it must re-stamp exactly the "storyline"
                    # parent — or status()'s parents-based staleness sees the OLD hash and
                    # reports a false positive, contradicting this branch's own "still valid"
                    # claim (review finding on f8783f9). ``_reparent`` only touches keys already
                    # present (an empty dict — pre-provenance — stays empty, never fabricated).
                    new_storyline = board.load("storyline")
                    new_hash = (
                        _content_hash(new_storyline)
                        if isinstance(new_storyline, Storyline)
                        else None
                    )
                    script_to_save = old_script
                    if new_hash is not None:
                        script_to_save = _reparent(old_script, {"storyline": new_hash})
                    board.save("script", script_to_save)
                    carried = ["script"]
                    if isinstance(old_voice, VoiceArtifact):
                        # The script re-stamp above changes ITS OWN content hash (content_hash
                        # folds in the whole model, "parents" included) — so voice's recorded
                        # "script" parent must move to match, or the very fix that clears
                        # script's staleness would newly drift voice's.
                        voice_updates = {"script": _content_hash(script_to_save)}
                        if new_hash is not None:
                            voice_updates["storyline"] = new_hash
                        voice_to_save = _reparent(old_voice, voice_updates)
                        board.save("voice", voice_to_save)
                        carried.append("voice")
                    result["carried_over"] = carried
                    result["note"] = (
                        "chapter structure unchanged — script"
                        + (" and voice" if len(carried) > 1 else "")
                        + " carried over; cutlist and below must be rebuilt for the new targets"
                    )
                else:
                    result["note"] = (
                        f"script v{old_script.version} was archived and invalidated by this "
                        "structural storyline change — rewrite every chapter"
                    )
            return result
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def get_storyline() -> dict[str, Any]:
        """The board's current storyline, or a not-found reason if none has been saved yet."""
        try:
            storyline = board.load("storyline")
            if storyline is None:
                return {"ok": False, "reason": "no storyline on the board"}
            return {"ok": True, "storyline": storyline.model_dump()}
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def script_budget() -> dict[str, Any]:
        """How many words the script may spend — ask this instead of guessing a length.

        Reads the saved storyline, adds up the reviewed windows it references, and turns
        that into a word count. Prefers a MEASURED speech rate over the language heuristic
        when this board's own last ``synthesize_script_voice`` run still matches the current
        script (``rate_source: "measured"``); falls back to the per-language heuristic
        otherwise (``rate_source: "heuristic"`` — no voice yet, or the script changed since).
        Call it ONCE before writing, write to ``words``, then synthesize and correct against
        the MEASURED ``voice_s`` from render_production. Do not iterate the script by feel:
        that burned a whole run on 34 saves that never reached a render.
        """
        try:
            storyline = board.load("storyline")
            if not isinstance(storyline, Storyline):
                return {"ok": False, "reason": "no storyline on the board; save_storyline first"}
            per_chapter, missing, n_segments = _storyline_material(
                db, board, asset_id, storyline
            )
            material = sum(per_chapter.values())
            target = board.meta().target_seconds
            usable = usable_budget_seconds(material_seconds=material, target_seconds=target)
            language = board.meta().language
            script = board.load("script")
            voice = board.load("voice")
            measured_rate_wps = _measured_rate_wps(
                storyline,
                script if isinstance(script, Script) else None,
                voice if isinstance(voice, VoiceArtifact) else None,
            )
            rate_source = "measured" if measured_rate_wps is not None else "heuristic"
            # Per chapter as well as in total: a right total over a wrong distribution still
            # breaks the film — one chapter carried 27s of narration for a 1s window.
            shares = allocate_chapter_seconds(per_chapter, usable_seconds=usable)
            chapter_budgets = chapter_word_budgets(
                shares, language, measured_rate_wps=measured_rate_wps
            )
            effective_seconds_per_word = (
                1.0 / measured_rate_wps if measured_rate_wps else seconds_per_word(language)
            )
            return {
                "ok": True,
                "material_seconds": round(material, 1),
                "usable_seconds": round(usable, 1),
                "words": budget_words_for(usable, language, measured_rate_wps=measured_rate_wps),
                "per_chapter": [
                    {
                        "chapter": ch,
                        "material_seconds": round(per_chapter[ch], 1),
                        "seconds": round(shares[ch], 1),
                        "words": chapter_budgets[ch],
                    }
                    for ch in sorted(per_chapter)
                ],
                "language": language,
                "seconds_per_word": effective_seconds_per_word,
                "rate_source": rate_source,
                "tolerance": _VOICE_RATE_TOLERANCE,
                "segments": n_segments,
                "unresolved_scenes": missing,
                "how": (
                    "usable_seconds is the smaller of the target and material_seconds (the sum "
                    "of the reviewed windows this storyline references). Write about 'words' "
                    f"words of {language} to fill it — no more, or the voice runs past the "
                    "footage. Spend them PER CHAPTER as per_chapter says: a right total over a "
                    "wrong split still breaks the film, because a chapter's video cannot cover "
                    "voice its own scenes do not hold. A chapter budgeted at almost nothing "
                    "means its window is a second long — say less there, or give that beat a "
                    "longer window in the storyline. "
                    + (
                        "This budget uses the MEASURED speech rate from the board's own last "
                        "synthesis, not a guess."
                        if rate_source == "measured"
                        else (
                            "Synthesize ONCE and correct from the measured voice_s; the "
                            f"language rate is only good to +/-{int(_VOICE_RATE_TOLERANCE * 100)}%."
                        )
                    )
                ),
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    # Per-run acknowledgement for the under-budget gate: the FIRST far-under-budget save of a
    # chapter is rejected with the numbers; saving that chapter again is the author saying
    # "the shorter film is deliberate" and is accepted, with the gap still named in the reply.
    # Same per-run-closure lifetime as ``healthy_re_reviews``.
    budget_gate_hit: set[int] = set()

    def save_script_chapter(chapter: int, lines: list[dict[str, Any]]) -> dict[str, Any]:
        """Replace one chapter's script lines; every other chapter's lines are kept as-is
        (merge semantics). Lines are validated (each needs scene_number + text; a malformed
        line is rejected with field-level validation errors). The script's language follows the
        board's — an English board produces an English-tagged script. A chapter far below its
        script_budget per_chapter allocation is rejected ONCE with the exact numbers: write the
        missing words from the scene's transcript (get_scene_transcript) and reviews, or save
        the chapter again unchanged to deliberately accept a shorter film. Saving invalidates
        every downstream artifact (voice, cutlist, render report, qa report) so they get
        regenerated from the new script."""
        try:
            # The whole load-merge-save must be ONE step: agent turns fire tool calls in
            # parallel, and a five-way save_script_chapter batch once interleaved between
            # a sibling's load and save — chapters silently overwrote each other (and the
            # unlocked writer of that era corrupted script.json outright).
            with board.transaction():
                # The chain is storyline -> script, and a save_storyline wipes everything
                # below. A script written FIRST is doomed work: a live run wrote a complete
                # 433-word script before its storyline, and the storyline save erased all of
                # it. The prompt mandates the order; prompts do not bind — so the contract
                # lives here.
                storyline_for_guard = board.load("storyline")
                if not isinstance(storyline_for_guard, Storyline):
                    return {
                        "ok": False,
                        "reason": (
                            "no storyline on the board — call save_storyline first. A script "
                            "written before the storyline is wiped by the storyline save."
                        ),
                    }
                # Gate S: the same structural refusal as save_storyline — a script line for a
                # scene outside the user's confirmed selection must never reach the board.
                meta_for_gate = board.meta()
                selection_for_gate = board.load("scene_selection")
                selection_for_gate = (
                    selection_for_gate
                    if isinstance(selection_for_gate, SceneSelection)
                    else None
                )
                block = scene_selection_block_reason(
                    selection_for_gate,
                    [int(line.get("scene_number", 0)) for line in lines],
                    gate_on=meta_for_gate.scene_gate,
                )
                if block is not None:
                    return {"ok": False, "reason": block}
                try:
                    new_lines = [ScriptLine(chapter=chapter, **line) for line in lines]
                except ValidationError as exc:
                    return {"ok": False, "errors": _validation_errors(exc)}
                # Screenplay labels go straight into the voice: three autonomous runs spoke
                # "Narration:" and "CAPTION:" eight times each. Rejected here, on the write
                # path, rather than in the model — the model also validates on load, and a
                # board written before this rule must stay readable.
                spoken_labels = [
                    (line.scene_number, label)
                    for line in new_lines
                    if (label := stage_direction_label(line.text)) is not None
                ]
                if spoken_labels:
                    detail = "; ".join(f"scene {n}: '{label}:'" for n, label in spoken_labels)
                    return {
                        "ok": False,
                        "reason": (
                            f"stage-direction labels would be read out loud ({detail}). Write "
                            f"only the spoken words — what is on screen is already on screen, "
                            f"so narrate what it MEANS instead of describing it."
                        ),
                    }
                # The board decides the language, not a hard-coded "de": two English runs wrote
                # English text tagged "de" because this line ignored the board.
                language = board.meta().language
                # A chapter can only be as long as its scenes (see the capacity_warning below) —
                # but VS2/VS3 went further and bound each storyline LINE's video segment to its
                # OWN scene's clip length, so a line that grossly outspeaks its own scene now
                # fails at CUTLIST time, once redistribution is expensive. Caught here instead,
                # at the write, where moving words to another scene still costs nothing. Several
                # NEW lines for the SAME scene in one call each add speech to that one scene, so
                # this sums THIS CALL's estimated seconds per scene_number before comparing
                # against capacity — three lines each under cap can still overflow together, and
                # that is exactly the overflow the per-line version would miss. The 1.15 factor
                # plus a 0.5s floor gives the +/-20% estimate headroom before hard-blocking;
                # narrower misses stay a capacity_warning below, not a refusal.
                asset_row = repos.get_asset(db, asset_id)
                fps = _fps(db, asset_row) if asset_row is not None else 30.0
                scene_word_totals: dict[int, int] = {}
                for line in new_lines:
                    scene_word_totals[line.scene_number] = scene_word_totals.get(
                        line.scene_number, 0
                    ) + len(line.text.split())
                for scene_number, scene_words in scene_word_totals.items():
                    resolved_scene = _resolve_scene(db, asset_id, scene_number)
                    if resolved_scene is None:
                        continue  # unknown scene: the storyline guard owns that failure
                    src_start, src_end, _scene_text = resolved_scene
                    scene_capacity_s = (src_end - src_start) / fps
                    if scene_capacity_s <= 0.0:
                        continue  # unresolved/zero-length scene: not this guard's false alarm
                    scene_est_s = estimate_voice_seconds(scene_words, language)
                    if scene_est_s > scene_capacity_s * 1.15 + 0.5:
                        return {
                            "ok": False,
                            "reason": (
                                f"scene {scene_number}: this call's line(s) would speak "
                                f"~{scene_est_s:.1f}s but the scene only holds "
                                f"{scene_capacity_s:.1f}s — shorten the text or split it "
                                "across more scenes; per-scene voice binds each line's video "
                                "segment to its own clip length"
                            ),
                        }
                # The under-budget gate (_BUDGET_GATE_*), computed from the SAME material as
                # script_budget so the number in this message and the number the author was told
                # to write to can never diverge. Live 2026-08-04: 45-60s targets shipped as
                # 20-34s films off ~50-word chapters nobody stopped.
                per_chapter, _missing_scenes, _n_resolved = _storyline_material(
                    db, board, asset_id, storyline_for_guard
                )
                usable = usable_budget_seconds(
                    material_seconds=sum(per_chapter.values()),
                    target_seconds=board.meta().target_seconds,
                )
                shares = allocate_chapter_seconds(per_chapter, usable_seconds=usable)
                # Same measured-vs-heuristic rate as script_budget, and for the same reason:
                # the number in this gate's message and the number script_budget told the
                # author to write to must never diverge (see _measured_rate_wps).
                script_for_rate = board.load("script")
                voice_for_rate = board.load("voice")
                measured_rate_wps = _measured_rate_wps(
                    storyline_for_guard,
                    script_for_rate if isinstance(script_for_rate, Script) else None,
                    voice_for_rate if isinstance(voice_for_rate, VoiceArtifact) else None,
                )
                budget_words = chapter_word_budgets(
                    shares, language, measured_rate_wps=measured_rate_wps
                ).get(chapter, 0)
                words_after = sum(len(line.text.split()) for line in new_lines)
                effective_seconds_per_word = (
                    1.0 / measured_rate_wps if measured_rate_wps else seconds_per_word(language)
                )
                missing_s = (budget_words - words_after) * effective_seconds_per_word
                under_budget = (
                    budget_words > 0
                    and words_after < _BUDGET_GATE_RATIO * budget_words
                    and missing_s >= _BUDGET_GATE_MISSING_S
                )
                if under_budget and chapter not in budget_gate_hit:
                    budget_gate_hit.add(chapter)
                    return {
                        "ok": False,
                        "reason": (
                            f"chapter {chapter}: {words_after} words against its "
                            f"{budget_words}-word share of script_budget — about {missing_s:.0f}s "
                            "of the film would simply be missing. Nothing was saved. Write the "
                            "missing words from the SOURCE, not from imagination: "
                            "get_scene_transcript(scene_number) quotes what is actually said, the "
                            "reviews say what is visible. If the scenes truly hold nothing more "
                            "worth saying, save this chapter again and the shorter film is "
                            "accepted."
                        ),
                    }
                existing = board.load("script")
                kept: list[ScriptLine] = []
                replaced: list[ScriptLine] = []
                if isinstance(existing, Script):
                    kept = [line for line in existing.lines if line.chapter != chapter]
                    replaced = [line for line in existing.lines if line.chapter == chapter]
                merged = sorted(kept + new_lines, key=lambda line: line.chapter)
                merged_script = Script(
                    language=language,
                    lines=merged,
                    parents={"storyline": _content_hash(storyline_for_guard)},
                )
                version = board.save("script", merged_script)
            # The word arithmetic, in the reply the agent actually reads. This save REPLACES
            # the chapter, and "replace" reads as "append" under expansion pressure: a live
            # run told to ADD ~200 words saved only the new lines per chapter, six times, and
            # 263 words fell to 123 — a 174s film with ~50s of voice — without anyone
            # noticing, because the reply named only version and line count.
            words_before = sum(len(line.text.split()) for line in replaced)
            result: dict[str, Any] = {
                "ok": True,
                "version": version,
                "total_lines": len(merged),
                "total_words": sum(len(line.text.split()) for line in merged),
                "chapter_words_before": words_before,
                "chapter_words_after": words_after,
            }
            if words_after < words_before:
                result["warning"] = (
                    f"chapter {chapter} REPLACED: {words_before} words -> {words_after}. This "
                    "tool replaces the chapter's lines with exactly what you pass — it does "
                    "not append. To EXPAND a chapter, pass its existing lines plus the new "
                    "ones."
                )
            # A chapter can only be as long as its scenes: voice beyond that has no picture.
            # The first full agent-built film shipped 62 words into an 11.5s chapter — the
            # TOTAL was on budget, the distribution was not, and 14s of narration fell off the
            # end (voice_fits FAIL by 26s). Say it here, where redistribution is still cheap.
            # ``per_chapter`` is the gate's computation above — a script save never touches
            # the storyline, so it is still current here.
            capacity = per_chapter.get(chapter)
            voice_s = estimate_voice_seconds(words_after, language)
            # capacity 0.0 means the chapter's scenes could not be RESOLVED (unknown), not
            # that they hold nothing — a "0.0s" false alarm teaches agents to ignore the
            # real one.
            if capacity is not None and capacity > 0.0 and voice_s > capacity + 0.5:
                result["capacity_warning"] = (
                    f"chapter {chapter}: {words_after} words are ~{voice_s:.1f}s of voice, "
                    f"but its scenes hold only {capacity:.1f}s — the overflow will have no "
                    "picture. Move the extra words to a chapter with spare capacity (see "
                    "script_budget's per_chapter) or cut them."
                )
            if under_budget:
                result["budget_warning"] = (
                    f"chapter {chapter} stays at {words_after} of its {budget_words}-word "
                    f"share of script_budget (~{missing_s:.0f}s of film shorter) — accepted "
                    "as a deliberate shorter film."
                )
            return result
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def get_script() -> dict[str, Any]:
        """The board's current script, plus how it measures against its word budget, or a
        not-found reason if none has been saved yet.

        A chapter's video length is its share of the voice, so a short script is a short film
        — the author wrote 140 words against a 300-word budget once and nothing said the film
        would come out half length. ``shortfall_pct`` is that gap, reported where the author
        verifies its own work."""
        try:
            script = board.load("script")
            if not isinstance(script, Script):
                return {"ok": False, "reason": "no script on the board"}
            language = board.meta().language
            words = len(script_text(script.lines).split())
            # Budget against the SAME usable length script_budget uses: the smaller of the
            # target and the material. Measuring the shortfall against the raw target is what
            # drove the author to write more voice than the footage could ever cover.
            storyline = board.load("storyline")
            target = board.meta().target_seconds
            if isinstance(storyline, Storyline):
                per_chapter, _missing, _n = _storyline_material(db, board, asset_id, storyline)
                usable = usable_budget_seconds(
                    material_seconds=sum(per_chapter.values()), target_seconds=target
                )
            else:
                usable = target
            budget = budget_words_for(usable, language)
            shortfall = 0.0 if budget <= 0 else max(0.0, (budget - words) / budget * 100.0)
            # Filling a budget from held screens made the author invent capabilities. Report
            # the specifics no review ever saw, so padding is visible where the work is checked.
            grounding = " ".join(
                f"{r.description} {r.whats_happening}" for r in board.scene_reviews()
            )
            ungrounded = ungrounded_terms(script_text(script.lines), grounding)
            # Which planned chapters were never written at all. A live run left four of six
            # silent and shipped a 109s film against a 174s target; the shortfall percentage
            # alone reads like "write more", not "you skipped two thirds of the story".
            planned = storyline if isinstance(storyline, Storyline) else None
            silent = silent_chapters(script, planned)
            return {
                "ok": True,
                "script": script.model_dump(),
                "words": words,
                "budget_words": budget,
                "estimated_voice_s": round(estimate_voice_seconds(words, language), 1),
                "shortfall_pct": round(shortfall, 1),
                "ungrounded_terms": ungrounded,
                "silent_chapters": silent,
                "chapters_written": sorted({line.chapter for line in script.lines}),
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def suggest_scenes_for_script() -> dict[str, Any]:
        """Deterministic, LLM-free check: for each of the board's script lines, which rough-cut
        scene actually carries that text (same matching discovery.search_material uses,
        restricted to this asset) — ``scene_number: null`` when no scene's transcript matches.
        This is the TEXT'S opinion of where each line belongs, independent of what scene the
        author assigned it to; call it after every script (re-)approval and compare its
        suggestions against the storyline before treating the script as final. Requires
        save_script_chapter to have run first."""
        try:
            script = board.load("script")
            if not isinstance(script, Script):
                return {"ok": False, "reason": "no script on the board; save_script_chapter first"}
            asset = repos.get_asset(db, asset_id)
            if asset is None:
                return {"ok": False, "reason": "unknown asset"}
            project_id = str(asset["project_id"])
            lines = [line.text for line in script.lines]
            suggestions = match_lines_to_scenes(db, project_id, asset_id, lines)
            return {"ok": True, "suggestions": suggestions}
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def synthesize_script_voice() -> dict[str, Any]:
        """Speak the board's current script — in STORYLINE scene order, not the order the
        lines were written in (see _lines_in_storyline_order) — with the configured voice
        backend, caching by a hash of that ordered text: a re-run after an unrelated board
        change is a no-op (``cached: True``), while a storyline reorder changes the text and
        correctly busts the cache. Requires save_storyline and save_script_chapter to have both
        run first. On a fresh synthesis, the mp3 plus a word-timings sidecar (used for caption
        burn-in and build_cutlist's zoom timing) are saved as the board's voice artifact.
        Gracefully reports ``ok: False`` without raising when no voice backend is configured or
        the backend itself fails. Deterministically refuses first of all when Gate B is active
        and unapproved (script_gate: the user must approve the script in chat — approve_script —
        before voice, cutlist or render may run); this is enforced HERE, not just in the
        orchestrator prompt, so a run cannot talk its way past the checkpoint. The refusal is
        content-aware, not just a bare timestamp check: a script edited (or reverted to a
        DIFFERENT version) AFTER approval changes its content_hash while
        ``script_approved_utc`` stays set, so the approval only still counts when the stamped
        hash matches the CURRENT script — otherwise voice would run on text the user never
        actually signed off on (review finding). Synthesizes PER LINE with an on-disk line
        cache (only changed lines hit the TTS API) and constructs the single track + merged
        sidecar from the clips."""
        try:
            meta = board.meta()
            if meta.script_gate:
                if meta.script_approved_utc is None:
                    return {
                        "ok": False,
                        "reason": (
                            "script gate: awaiting user approval — the user must approve the "
                            "script in chat before voice is synthesized"
                        ),
                    }
                gate_script = board.load("script")
                gate_hash = (
                    _content_hash(gate_script) if isinstance(gate_script, Script) else None
                )
                if gate_hash is None or meta.script_approved_script_hash != gate_hash:
                    return {
                        "ok": False,
                        "reason": (
                            "script gate: script changed after approval — the user must "
                            "re-approve the script in chat"
                        ),
                    }
            storyline = board.load("storyline")
            if not isinstance(storyline, Storyline):
                return {
                    "ok": False,
                    "reason": "no storyline on the board; run save_storyline first",
                }
            script = board.load("script")
            if not isinstance(script, Script):
                return {"ok": False, "reason": "no script on the board"}
            ordered_lines = _lines_in_storyline_order(script, storyline)
            new_hash = script_hash(ordered_lines)

            existing = board.load("voice")
            if isinstance(existing, VoiceArtifact) and existing.script_hash == new_hash:
                return {
                    "ok": True,
                    "cached": True,
                    "mp3_path": existing.mp3_path,
                    "voice_s": existing.voice_s,
                }

            backend = d.voice_backend if d.voice_backend is not None else resolve_voice_backend()
            if backend is None:
                return {"ok": False, "reason": "no voice backend configured"}
            asset = repos.get_asset(db, asset_id)
            if asset is None:
                return {"ok": False, "reason": "asset not found"}
            project = repos.get_project(db, str(asset["project_id"]))
            if project is None:
                return {"ok": False, "reason": "project not found"}

            workspace = Path(str(project["workspace_root"]))
            lines_dir = workspace / "voiceovers" / "lines"
            gap = INTER_SCENE_GAP_S

            clip_paths: list[Path] = []
            durations: list[float] = []
            per_line_words: list[list[dict[str, Any]]] = []
            metas: list[tuple[int, int, str]] = []  # (scene_number, chapter, line_hash)
            for line in ordered_lines:
                lh = hashlib.sha256(line.text.encode("utf-8")).hexdigest()
                clip = lines_dir / f"{lh}.mp3"
                timings = Path(str(clip) + ".timings.json")
                if not clip.is_file():
                    # ElevenLabs mp3s default to 44.1 kHz, matching concat_with_gaps' hardcoded
                    # silence sample rate — if a backend ever emits a different rate, resample
                    # inputs in voice_concat's filter graph instead (see VS1 review note).
                    synth = backend.synthesize(line.text, clip)
                    if not synth.get("ok"):
                        synth = backend.synthesize(line.text, clip)  # exactly one retry
                    if not synth.get("ok"):
                        return {
                            "ok": False,
                            "reason": (
                                f"voice synthesis failed for scene {line.scene_number} "
                                f"(chapter {line.chapter}): "
                                f"{str(synth.get('reason') or 'synthesis failed')[:120]}"
                            ),
                        }
                clip_paths.append(clip)
                durations.append(probe_duration_s(clip))
                per_line_words.append(_read_words(str(timings)) if timings.is_file() else [])
                metas.append((line.scene_number, line.chapter, lh))

            offsets = line_offsets(durations, gap)
            out_path = workspace / "voiceovers" / f"{new_id()}.mp3"
            concat_with_gaps(clip_paths, gap, out_path)
            merged = merge_word_timings(per_line_words, offsets)
            timings_path: str | None = None
            if merged["words"]:
                timings_path = str(out_path) + ".timings.json"
                Path(timings_path).write_text(
                    json.dumps(merged, ensure_ascii=False), encoding="utf-8"
                )
            voice_s = probe_duration_s(out_path)
            artifact = VoiceArtifact(
                script_hash=new_hash,
                mp3_path=str(out_path),
                timings_path=timings_path,
                voice_s=voice_s,
                segments=[
                    VoiceSegment(
                        scene_number=scene, chapter=chap, line_hash=lh,
                        mp3_path=str(clip), duration_s=dur, offset_s=off,
                    )
                    for (scene, chap, lh), clip, dur, off in zip(
                        metas, clip_paths, durations, offsets, strict=True
                    )
                ],
                parents={
                    "storyline": _content_hash(storyline),
                    "script": _content_hash(script),
                },
            )
            version = board.save("voice", artifact)
            return {
                "ok": True,
                "cached": False,
                "version": version,
                "mp3_path": artifact.mp3_path,
                "voice_s": voice_s,
                "lines": len(clip_paths),
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def build_cutlist(transition_lead_s: float = 0.4, zoom: str = "auto") -> dict[str, Any]:
        """Deterministically derive a frame-accurate cutlist from storyline + script + voice:
        one CutSegment per scene entry in arc order (chapter, then that chapter's
        scene_numbers order). An entry that references a review window ({"scene": N,
        "window": K}) is cut from THAT window — its offset is the segment start, its roi
        the zoom region (falling back to the review-level roi); the segment's LENGTH comes
        from the chapter's time budget / audio window, never from the window's duration —
        so the same scene can appear several times with different windows. Segment
        lengths are COUPLED TO THE VOICE so picture chapters stay in sync with the one
        continuous voice track: each chapter's audio window (from the word-timings sidecar;
        boundaries midway between adjacent chapters' words, the last chapter running to voice
        end + a short tail) is distributed over its segments proportionally to their
        per-scene chapter budget — 2s floor per segment, each segment starting at
        its window's offset and stretching past the window's duration_s if needed, but never
        past its scene's end. A chapter the sidecar doesn't cover (or a missing sidecar)
        falls back to the plain target_seconds budget. An optional zoom-in is timed to when
        the scene's script line is actually spoken (word starts, offset ahead by
        transition_lead_s so the zoom lands just before the word lands, not on it).
        zoom is the FRAMING LEVER: "auto" (default) keeps each segment's window/review roi
        and the spoken-word zoom timing; zoom="off" drops EVERY roi and zoom_start_s
        regardless of the storyline's window references, so the render shows the full frame
        (blur-filled) — when the user asks for the full picture / no tight zoom, call
        build_cutlist(zoom="off") directly; the storyline does NOT need to be re-saved for a
        framing change. Any other zoom value is rejected. Requires
        save_storyline, save_script_chapter and synthesize_script_voice to have all run first
        — reports which one is missing instead of raising, and rejects a storyline window
        reference the scene's review does not have (fix the storyline or re-review)."""
        try:
            if zoom not in ("auto", "off"):
                return {"ok": False, "reason": 'zoom must be "auto" or "off"'}
            storyline = board.load("storyline")
            if not isinstance(storyline, Storyline):
                return {
                    "ok": False,
                    "reason": "no storyline on the board; run save_storyline first",
                }
            script = board.load("script")
            if not isinstance(script, Script):
                return {
                    "ok": False,
                    "reason": "no script on the board; run save_script_chapter first",
                }
            voice = board.load("voice")
            if not isinstance(voice, VoiceArtifact):
                return {
                    "ok": False,
                    "reason": "no voice on the board; run synthesize_script_voice first",
                }

            asset = repos.get_asset(db, asset_id)
            fps = _fps(db, asset) if asset is not None else 30.0
            ordered_lines = _lines_in_storyline_order(script, storyline)
            # WHOSE voice, not just whether one exists. The cut's audio windows and zoom
            # timings come from this voice's sidecar and the render muxes its mp3 — a voice
            # reverted to an older take (revert_artifact is a documented follow-up flow)
            # would cut the current script's pictures to a different narration, and the
            # cutlist would still stamp the current hash: stale=False on a film that lies.
            if voice.script_hash and voice.script_hash != script_hash(ordered_lines):
                return {
                    "ok": False,
                    "reason": (
                        "the voice on the board was synthesized from a DIFFERENT script than "
                        "the current one — run synthesize_script_voice first so the cut and "
                        "the narration agree"
                    ),
                }
            # VS3: a voice with per-line segments (VS2) sizes each cutlist segment to its OWN
            # clip(s) instead of proportionally scaling the chapter's audio window (the legacy
            # path below, kept byte-identical for segments=None — every board voiced before
            # per-scene synthesis). Pairing is by IDENTITY, not position: each storyline entry's
            # (chapter, scene_number) key is looked up in a one-time grouping of voice_segments
            # by that same key (clips of one key are consecutive in the constructed track by
            # construction — synthesize_script_voice walks the identical
            # _lines_in_storyline_order grouping). A key is POPPED from the group on first use,
            # so a chapter that references one scene twice (two review windows, one line) finds
            # nothing the second time — a hard, actionable refusal instead of a silent
            # zero-duration segment that would drift every later scene. Any group left unpopped
            # after the whole arc is walked is a line the storyline no longer references at all
            # (checked once, after the loop below).
            voice_segments = voice.segments  # None = legacy single-track board
            clips_by_key: dict[tuple[int, int], list[VoiceSegment]] = {}
            if voice_segments is not None:
                for seg in voice_segments:
                    clips_by_key.setdefault((seg.chapter, seg.scene_number), []).append(seg)
            words = _read_words(voice.timings_path)
            line_map = line_starts(ordered_lines, words)
            audio_windows = chapter_audio_windows(ordered_lines, words)
            reviews_by_scene = {r.scene_number: r for r in board.scene_reviews()}

            segments: list[CutSegment] = []
            video_start_s = 0.0
            order = 0
            for chapter in sorted(storyline.arc, key=lambda c: c.chapter):
                n_scenes = len(chapter.scene_numbers)
                # First pass: resolve the chapter's scene entries (scene + review window) and
                # their base durations, so the chapter's audio window can be distributed over
                # them BEFORE any segment is cut.
                resolved_scenes: list[tuple[int, int, int, BestWindow, Roi | None]] = []
                # Parallel to resolved_scenes — only needed for the per-scene-voice path's
                # window-naming refusals below, kept separate rather than widening the
                # resolved_scenes tuple everywhere (including the legacy path's consumers).
                window_idxs: list[int] = []
                base_durations: list[float] = []
                stretch_caps: list[float] = []
                for entry in chapter.scene_numbers:
                    scene_number, window_idx = as_scene_window(entry)
                    resolved = _resolve_scene(db, asset_id, scene_number)
                    if resolved is None:
                        if voice_segments is not None:
                            # The legacy path's silent skip would desync the identity pairing
                            # below (a scene the voice HAS a clip for would simply never be
                            # matched, and its clip would misreport as "no longer referenced").
                            return {
                                "ok": False,
                                "reason": (
                                    f"scene {scene_number} is referenced by the storyline but "
                                    "does not exist in this asset's rough cut — fix the "
                                    "storyline (save_storyline)"
                                ),
                            }
                        continue
                    src_start, src_end, _text = resolved
                    scene_duration_s = (src_end - src_start) / fps

                    review = reviews_by_scene.get(scene_number)
                    if review is not None:
                        if window_idx >= len(review.windows):
                            return {
                                "ok": False,
                                "reason": (
                                    f"scene {scene_number} has {len(review.windows)} windows "
                                    f"(0..{len(review.windows) - 1}) but the storyline "
                                    f"references window {window_idx}; fix the storyline or "
                                    "re-review the scene"
                                ),
                            }
                        window = review.windows[window_idx]
                        roi = window.roi if window.roi is not None else review.roi
                    else:
                        if window_idx > 0:
                            return {
                                "ok": False,
                                "reason": (
                                    f"scene {scene_number} has no review; window "
                                    f"{window_idx} can only be cut from a reviewed scene"
                                ),
                            }
                        window = BestWindow(
                            offset_s=0.0, duration_s=min(_DEFAULT_WINDOW_S, scene_duration_s)
                        )
                        roi = None

                    if zoom == "off":  # framing lever: full frame, window offsets kept
                        roi = None

                    resolved_scenes.append((scene_number, src_start, src_end, window, roi))
                    window_idxs.append(window_idx)
                    base_durations.append(
                        _segment_duration_s(
                            target_seconds=chapter.target_seconds,
                            n_scenes=n_scenes,
                            scene_duration_s=scene_duration_s,
                        )
                    )
                    # A segment may stretch past its window's duration_s but keeps the offset
                    # as its start and never crosses the scene's end (the 2s floor still wins
                    # over an offset too close to the end — the frame clamp below pulls the
                    # start back for exactly that case, as before).
                    stretch_caps.append(segment_capacity_seconds(window, scene_duration_s))

                if voice_segments is not None:
                    durations = []
                    for idx, (scene_number, src_start, src_end, _w, _r) in enumerate(
                        resolved_scenes
                    ):
                        window_idx = window_idxs[idx]
                        group = clips_by_key.pop((chapter.chapter, scene_number), None)
                        if group is None:
                            # Either a repeat reference to a (chapter, scene) key another
                            # entry in this arc already consumed (a scene reused via a second
                            # review window, with only ONE script line — see the precompute
                            # comment above), or a scene with no script line at all. Both are
                            # the SAME author-facing problem: this storyline entry has no line
                            # of its own to speak.
                            return {
                                "ok": False,
                                "reason": (
                                    f"per-scene voice: storyline entry scene {scene_number} "
                                    f"(window {window_idx}) has no own narration line — every "
                                    "entry needs its own line; drop the repeated window or "
                                    "give the scene a line (save_script_chapter), then re-run "
                                    "synthesize_script_voice"
                                ),
                            }
                        # This entry's own clip(s), consecutive in the constructed track (VS1's
                        # concat_with_gaps): the group's own inner gaps (k-1 of them) plus one
                        # trailing gap to the NEXT entry's clip — except when this group holds
                        # the single, globally LAST voice segment, matching VS1/VS2's
                        # n-1-gaps-total, no-trailing-gap construction exactly.
                        contains_last_clip = group[-1] is voice_segments[-1]
                        inner_gaps = (len(group) - 1) * INTER_SCENE_GAP_S
                        trailing_gap = 0.0 if contains_last_clip else INTER_SCENE_GAP_S
                        total_spoken = sum(seg.duration_s for seg in group)
                        want = total_spoken + inner_gaps + trailing_gap
                        capacity = (src_end - src_start) / fps
                        if want > capacity + 1e-6:
                            return {
                                "ok": False,
                                "reason": (
                                    f"the line(s) for scene {scene_number} speak "
                                    f"{total_spoken:.1f}s but the scene only holds "
                                    f"{capacity:.1f}s — shorten the line(s) "
                                    "(save_script_chapter), then re-run "
                                    "synthesize_script_voice"
                                ),
                            }
                        durations.append(want)
                else:
                    audio_window = audio_windows.get(chapter.chapter)
                    if audio_window is not None:
                        durations = _scale_chapter_durations(
                            base_durations, stretch_caps, audio_window[1] - audio_window[0]
                        )
                    else:
                        durations = base_durations

                for scene_info, seg_dur_s in zip(resolved_scenes, durations, strict=True):
                    scene_number, src_start, src_end, window, roi = scene_info
                    dur_frames = round(seg_dur_s * fps)
                    raw_start = src_start + round(window.offset_s * fps)
                    start_frame = min(raw_start, src_end - dur_frames)
                    end_frame_exclusive = start_frame + max(dur_frames, 1)
                    actual_dur_s = (end_frame_exclusive - start_frame) / fps

                    zoom_start_s: float | None = None
                    line_start = line_map.get((chapter.chapter, scene_number))
                    if roi is not None and line_start is not None:
                        candidate = max(0.0, line_start - video_start_s + transition_lead_s)
                        if candidate < actual_dur_s - 0.7:
                            zoom_start_s = candidate

                    segments.append(
                        CutSegment(
                            order=order,
                            scene_number=scene_number,
                            start_frame=start_frame,
                            end_frame_exclusive=end_frame_exclusive,
                            roi=roi,
                            zoom_start_s=zoom_start_s,
                        )
                    )
                    video_start_s += actual_dur_s
                    order += 1

            if voice_segments is not None and clips_by_key:
                # Every entry the storyline still references popped its own group above — what
                # is left is a line the CURRENT storyline no longer references at all (a scene
                # dropped from the arc after the voice was synthesized; lines_in_storyline_order
                # never drops a line from the voice itself, so it is still sitting in
                # voice_segments with nothing left to claim it).
                return {
                    "ok": False,
                    "reason": (
                        "voice has clips for lines the storyline no longer references — "
                        "re-run synthesize_script_voice"
                    ),
                }

            if not segments:
                return {"ok": False, "reason": "no scenes resolved from the storyline"}

            board.save(
                "cutlist",
                Cutlist(
                    segments=segments,
                    script_hash=script_hash(ordered_lines),
                    parents={
                        "storyline": _content_hash(storyline),
                        "script": _content_hash(script),
                        "voice": _content_hash(voice),
                    },
                ),
            )
            total_seconds = sum((s.end_frame_exclusive - s.start_frame) / fps for s in segments)
            with_zoom = sum(1 for s in segments if s.zoom_start_s is not None)
            reply: dict[str, Any] = {
                "ok": True,
                "segments": len(segments),
                "total_seconds": round(total_seconds, 3),
                "with_zoom": with_zoom,
            }
            if zoom == "off":
                reply["note"] = (
                    "zoom off: every roi and zoom_start_s dropped — the render shows the "
                    "full frame"
                )
            return reply
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def save_contact_sheet() -> dict[str, Any]:
        """Build the Kontaktbogen: ONE grid PNG showing every cutlist segment's middle frame
        (tiles in segment order, each labeled "<order> S<scene_number>"), saved to the board
        as the contact_sheet artifact. This is the user's visual pre-render checkpoint: call
        it ALWAYS right after build_cutlist and BEFORE render_production, and again after
        every cutlist rebuild (saving a cutlist archives the current sheet). Purely
        mechanical (ffmpeg on the editorial proxy — no model calls, cheap to re-run); when no
        usable font is found the tiles come back unlabeled (labeled: false) instead of
        failing. Requires a cutlist on the board and the asset's proxy — reports which one is
        missing instead of raising."""
        try:
            cutlist = board.load("cutlist")
            if not isinstance(cutlist, Cutlist):
                return {"ok": False, "reason": "no cutlist on the board; run build_cutlist first"}
            asset = repos.get_asset(db, asset_id)
            if asset is None:
                return {"ok": False, "reason": "asset not found"}
            proxy = context._proxy_path(db, asset_id)
            rate = context._frame_rate(db, asset)
            if proxy is None or rate is None:
                return {
                    "ok": False,
                    "reason": "no proxy for asset - the contact sheet samples the editorial proxy",
                }
            rate_num, rate_den = rate

            tiles = [
                ContactSheetTile(
                    order=s.order,
                    scene_number=s.scene_number,
                    frame=s.start_frame + (s.end_frame_exclusive - s.start_frame) // 2,
                    label=f"{s.order} S{s.scene_number}",
                )
                for s in sorted(cutlist.segments, key=lambda s: s.order)
            ]
            cols, rows = _grid_shape(len(tiles))
            out_dir = board.root.parent / "contact_sheets"
            out_dir.mkdir(parents=True, exist_ok=True)
            out_png = out_dir / f"{new_id()}.png"

            # Tiles are framed the way the render frames them (crop for a zoomed segment,
            # letterbox otherwise), so the sheet can show the framing faults it gates.
            # The crop window must live in the PROXY's pixel space, not the source's:
            # the sheet samples the proxy, and a 4K source's 1080p proxy is smaller than
            # a source-space window — every roi'd tile then overflows the frame and the
            # PNG encoder dies with "Invalid argument" (live finding 2026-08-04). The ROI
            # is normalized, so proxy-space windows frame identically, just smaller.
            src_w, src_h = _probe_video_dims(proxy)
            _, (out_w, out_h) = canvas_for(board.meta().format)
            roi_by_order = {
                s.order: (s.roi.x, s.roi.y, s.roi.w, s.roi.h) if s.roi is not None else None
                for s in cutlist.segments
            }
            framed = src_w > 0 and src_h > 0

            with tempfile.TemporaryDirectory(prefix="laura-contact-sheet-") as tmp:
                tiles_dir = Path(tmp)
                ok, labeled, failed = _extract_sheet_tiles(
                    Path(proxy),
                    [
                        (
                            t.frame * rate_den / rate_num,
                            t.label,
                            roi_by_order.get(t.order) if framed else None,
                        )
                        for t in tiles
                    ],
                    tiles_dir,
                    _find_fontfile(),
                    src_w=src_w or out_h,  # unknown dims -> letterbox, never crop
                    src_h=src_h or out_w,
                    out_w=out_w,
                    out_h=out_h,
                )
                if not ok:
                    bad = tiles[failed] if failed is not None else tiles[0]
                    return {
                        "ok": False,
                        "reason": (
                            f"frame extraction from the proxy failed for segment {bad.order} "
                            f"(frame {bad.frame})"
                        ),
                    }
                if not _compose_sheet_grid(tiles_dir, cols, rows, out_png):
                    return {"ok": False, "reason": "tile grid composition failed"}

            artifact = ContactSheet(
                png_path=str(out_png),
                cols=cols,
                rows=rows,
                labeled=labeled,
                tiles=tiles,
                # The sheet is a projection of the cutlist, so it INHERITS the cutlist's
                # provenance rather than recomputing it — the two can never disagree, and an
                # unknown (pre-provenance) cutlist propagates its unknown honestly.
                script_hash=cutlist.script_hash,
                parents={"cutlist": _content_hash(cutlist)},
            )
            version = board.save("contact_sheet", artifact)
            return {
                "ok": True,
                "version": version,
                "png_path": str(out_png),
                "cols": cols,
                "rows": rows,
                "labeled": labeled,
                "tiles": [t.model_dump() for t in tiles],
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def render_production() -> dict[str, Any]:
        """Render the board's cutlist to a finished export in the board's format and grade it.

        Requires build_cutlist, voice, script and storyline to have all run first — reports
        which one is missing instead of raising (storyline is also a transitive prerequisite of
        build_cutlist, but is re-checked directly here since it is needed again to put the
        caption/voiceover text back in the SAME storyline scene order the cutlist's segments
        are in — see _lines_in_storyline_order). Turns the cutlist into (start_frame,
        end_frame_exclusive) segments plus an index-aligned zoom hint per segment
        (only where that segment has BOTH a roi and a zoom_start_s; otherwise None), and renders
        them with captions on and a blur-filled letterbox onto the canvas the board's format
        selects (insta 1080x1920, x 1920x1080, linkedin 1080x1080), plus the board's voice as
        the new audio track. Polls the resulting export (bounded by
        RENDER_WAIT_SECONDS) until it leaves the "rendering" state, MEASURES the finished file
        with ffprobe (``delivered_s`` — the mux ends with whichever stream runs out first, so
        the cut is not the film), then grades four checks: voice_fits (the rendered video
        covers the whole voice track, small tolerance), export_ready, has_voice_timings
        (captions can be burned in), and story_covered (the script wrote the chapters the
        storyline planned). The RenderReport is saved to the board regardless of the verdict,
        so a failing render stays inspectable.

        CODING-AGENT CHARTER: if voice_fits comes back False, do NOT shorten or cut the voice —
        build_cutlist already sizes segments to the voice's chapter audio windows, so a
        shortfall means the scene material ran out (segments hit their scene-end caps) or the
        voice has no timings sidecar. Re-run build_cutlist and render again; if it still fails,
        the chapters need more/longer scenes (a storyline decision — report it), or, in the
        sidecar-less fallback, a longer per-chapter time budget. The voice is the script the
        team already agreed on; the video must fit it.
        """
        try:
            cutlist = board.load("cutlist")
            if not isinstance(cutlist, Cutlist):
                return {"ok": False, "reason": "no cutlist on the board; run build_cutlist first"}
            voice = board.load("voice")
            if not isinstance(voice, VoiceArtifact):
                return {
                    "ok": False,
                    "reason": "no voice on the board; run synthesize_script_voice first",
                }
            script = board.load("script")
            if not isinstance(script, Script):
                return {
                    "ok": False,
                    "reason": "no script on the board; run save_script_chapter first",
                }
            storyline = board.load("storyline")
            if not isinstance(storyline, Storyline):
                return {
                    "ok": False,
                    "reason": "no storyline on the board; run save_storyline first",
                }

            # Revision cap: once this production has rendered render_cap times, do not spend
            # another render. Ship the last one instead. An upstream re-save may have
            # invalidated the current render_report — restore the newest archived one so the
            # finished export is still reported. deps raise the cap for an explicit user
            # follow-up (see follow_up_render_cap) — an operator-requested reframe must not
            # be eaten by the team's runaway-loop backstop.
            render_cap = (
                d.max_render_cycles if d.max_render_cycles is not None else _MAX_RENDER_CYCLES
            )
            if _renders_so_far(board) >= render_cap:
                last = board.load("render_report")
                if not isinstance(last, RenderReport):
                    newest = max(board.versions("render_report"), default=0)
                    if newest > 0:
                        board.revert("render_report", newest)
                        last = board.load("render_report")
                if isinstance(last, RenderReport):
                    # The restored render may predate the script now on the board — live, a
                    # v14-era render sat on a v39 board and its voice_fits check read OK for a
                    # pairing that no longer existed. Shipping it is still the right call at the
                    # cap, but calling it final without saying that is how the board came to
                    # claim a finished film nobody had made.
                    current_hash = script_hash(_lines_in_storyline_order(script, storyline))
                    stale = bool(last.script_hash) and last.script_hash != current_hash
                    note = (
                        f"revision limit reached ({render_cap} renders); shipping "
                        "this cut instead of rendering again"
                    )
                    if stale:
                        note += (
                            " — WARNING: this render was made from an earlier script; the "
                            "script on the board has changed since and is NOT what this cut "
                            "speaks"
                        )
                    return {
                        "ok": not stale,
                        "final": True,
                        "stale": stale,
                        "export_id": last.export_id,
                        "checks": [c.model_dump() for c in last.checks],
                        "note": note,
                    }

            ordered_lines = _lines_in_storyline_order(script, storyline)

            asset = repos.get_asset(db, asset_id)
            fps = _fps(db, asset) if asset is not None else 30.0
            video_s = sum((s.end_frame_exclusive - s.start_frame) / fps for s in cutlist.segments)

            segments: list[tuple[int, int]] = [
                (s.start_frame, s.end_frame_exclusive) for s in cutlist.segments
            ]
            zoom: list[dict[str, Any] | None] = [
                {"roi": s.roi.model_dump(), "zoom_start_s": s.zoom_start_s}
                if s.roi is not None and s.zoom_start_s is not None
                else None
                for s in cutlist.segments
            ]

            render_fn = d.render_segments
            if render_fn is None:
                from ..mcp.tools import tool_render_segments

                render_fn = tool_render_segments

            vertical, (out_w, out_h) = canvas_for(board.meta().format)
            result = render_fn(
                db,
                asset_id,
                segments,
                captions=True,
                fit="blur",
                vertical=vertical,
                out_size=(out_w, out_h),
                voiceover_path=voice.mp3_path,
                voiceover_text=script_text(ordered_lines),
                zoom=zoom,
            )
            if not result.get("ok"):
                return {"ok": False, "reason": str(result.get("error") or "render failed")}
            export_id = str(result["export_id"])

            row = repos.get_export(db, export_id)
            deadline = time.monotonic() + RENDER_WAIT_SECONDS
            while (
                row is not None
                and str(row.get("status")) == "rendering"
                and time.monotonic() < deadline
            ):
                time.sleep(_RENDER_POLL_INTERVAL_S)
                row = repos.get_export(db, export_id) or row

            export_ready = row is not None and str(row.get("status")) == "ready"
            # What the mux actually produced, not what was cut. ffmpeg's -shortest ends the
            # film with whichever stream runs out first, so a cut longer than its voiceover
            # ships short and every number derived from the cut reads too high.
            delivered_s: float | None = None
            export_path = row.get("path") if row is not None else None
            if export_ready and export_path:
                measure = d.probe_duration if d.probe_duration is not None else _probe_duration
                delivered_s = measure(str(export_path))
            voice_fits = voice.voice_s is None or (
                video_s + _VOICE_FIT_TOLERANCE_S >= voice.voice_s
            )
            has_voice_timings = bool(voice.timings_path)
            voice_note = (
                f"video={video_s:.2f}s voice={voice.voice_s:.2f}s"
                if voice.voice_s is not None
                else f"video={video_s:.2f}s voice=unknown"
            )
            if delivered_s is not None:
                voice_note += f" delivered={delivered_s:.2f}s"
            silent, silent_share = silent_seconds_share(script, storyline)
            story_covered = silent_share <= _SILENT_SHARE_LIMIT

            checks = [
                RenderCheck(name="voice_fits", ok=voice_fits, note=voice_note),
                RenderCheck(
                    name="export_ready",
                    ok=export_ready,
                    note=str(row.get("status")) if row is not None else "export not found",
                ),
                RenderCheck(
                    name="has_voice_timings",
                    ok=has_voice_timings,
                    note="" if has_voice_timings else "no timings sidecar on the voice artifact",
                ),
                # Whether the script covered the story it was written for. get_script has
                # reported silent_chapters for a while and it changed nothing, because the
                # author reads its own report and moves on: only a failing check reaches the
                # loop. Unlike target adherence this is not a length to chase — the remedy is
                # "write chapter N", one save, no re-render spiral.
                RenderCheck(
                    name="story_covered",
                    ok=story_covered,
                    note=(
                        "every planned chapter has narration"
                        if not silent
                        else f"chapters {silent} were never written — {silent_share:.0%} of the "
                        "storyline's planned seconds have no narration"
                    ),
                ),
            ]
            target_s = board.meta().target_seconds
            measured_s = delivered_s if delivered_s is not None else video_s
            target_ratio = round(measured_s / target_s, 3) if target_s > 0 else None
            report = RenderReport(
                export_id=export_id,
                video_s=video_s,
                delivered_s=delivered_s,
                voice_s=voice.voice_s,
                width=out_w,
                height=out_h,
                checks=checks,
                # Provenance: which script this cut actually speaks. Without it a restored
                # render cannot be told apart from a current one.
                script_hash=script_hash(ordered_lines),
                parents={
                    "storyline": _content_hash(storyline),
                    "script": _content_hash(script),
                    "voice": _content_hash(voice),
                    "cutlist": _content_hash(cutlist),
                },
                target_ratio=target_ratio,
            )
            board.save("render_report", report)
            ok = all(c.ok for c in checks)
            reply: dict[str, Any] = {
                "ok": ok,
                "export_id": export_id,
                "checks": [c.model_dump() for c in checks],
            }
            if target_ratio is not None:
                label = "delivered" if delivered_s is not None else "video"
                note = f"{label} {measured_s:.1f}s vs target {target_s:.1f}s ({target_ratio:.0%})"
                if delivered_s is not None and delivered_s + 0.5 < video_s:
                    note += (
                        f" — cut {video_s:.1f}s, so the mux ended with the voice and dropped "
                        "the rest of the footage; lengthen the narration or shorten the cut"
                    )
                reply["target_note"] = note
            return reply
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def review_export(at_seconds: list[float] | None = None) -> dict[str, Any]:
        """Look at a few real frames of the rendered export with the VLM and collect one short
        QA note per timestamp. Requires render_production to have run first (reads its
        RenderReport plus the export's on-disk path via repos.get_export). Defaults to
        start/middle/near-end (``[1.0, video_s/2, video_s-1.5]``) when at_seconds is omitted.
        Never fails the pipeline: without a configured describe backend it reports
        ``degraded: True`` with an empty notes list instead of blocking the QA reviewer, and a
        frame-grab failure just yields fewer notes than timestamps requested (never raises)."""
        try:
            report = board.load("render_report")
            if not isinstance(report, RenderReport):
                return {
                    "ok": False,
                    "reason": "no render_report on the board; run render_production first",
                }
            row = repos.get_export(db, report.export_id)
            path = row.get("path") if row is not None else None
            if not path:
                return {"ok": False, "reason": "export has no rendered file yet"}

            backend = (
                d.describe_backend if d.describe_backend is not None else resolve_describe_backend()
            )
            if backend is None:
                return {"ok": True, "notes": [], "degraded": True}

            times = (
                at_seconds
                if at_seconds is not None
                else [1.0, report.video_s / 2, max(0.0, report.video_s - 1.5)]
            )
            # The report records the canvas that was actually rendered — judge that one.
            prompt = _qa_prompt(report.width, report.height)
            notes: list[dict[str, Any]] = []
            for t in times:
                frame = _grab_video_frames(Path(str(path)), [t])
                if frame:
                    notes.append({"at_s": t, "note": backend.describe(frame, prompt)})
            return {"ok": True, "notes": notes}
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def save_qa_report(verdict: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate and save the QA reviewer's verdict ("ship" or "revise") plus concrete
        findings (severity/where/note each) to the board. A malformed verdict or finding is
        rejected with field-level validation errors instead of raising, so the agent can
        self-correct."""
        try:
            # QA judges a render. A live run saved a fresh ship-verdict onto a board whose
            # render had just been invalidated by a revise — a verdict sitting on top of a
            # missing film. Same order-guard pattern as script-before-storyline, last link.
            render_for_guard = board.load("render_report")
            if not isinstance(render_for_guard, RenderReport):
                return {
                    "ok": False,
                    "reason": (
                        "no render_report on the board — run render_production first. A QA "
                        "verdict without a render judges a film that does not exist."
                    ),
                }
            try:
                # verdict is plain str at the tool boundary; the Literal check happens here.
                qa_report = QaReport(
                    verdict=verdict,  # type: ignore[arg-type]
                    findings=[QaFinding(**f) for f in findings],
                    parents={"render_report": _content_hash(render_for_guard)},
                )
            except ValidationError as exc:
                return {"ok": False, "errors": _validation_errors(exc)}
            version = board.save("qa_report", qa_report)
            return {"ok": True, "version": version}
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def revert_artifact(name: str, version: int) -> dict[str, Any]:
        """Restore an earlier archived version of a board artifact as current, invalidating
        every artifact downstream of it exactly like a fresh save would (their current files are
        archived and removed; the normal pipeline tools then regenerate them). Use this ONLY when
        the task or user message explicitly asks to go back to a prior version — never as a
        routine step — and rebuild anything invalidated afterwards via the normal pipeline. Valid
        names: storyline, script, voice, cutlist, contact_sheet, render_report, qa_report. An
        unknown name is
        rejected with that list instead of raising; a version that was never archived reports
        ok: False with reason "no archived <name> v<version>" instead of raising."""
        try:
            valid_names = downstream_of("scene_reviews")
            if name not in valid_names:
                return {
                    "ok": False,
                    "reason": f"unknown artifact '{name}'; valid: {', '.join(valid_names)}",
                }
            will_invalidate = [d for d in downstream_of(name) if board.load(d) is not None]
            try:
                board.revert(name, version)
            except FileNotFoundError:
                return {"ok": False, "reason": f"no archived {name} v{version}"}
            return {
                "ok": True,
                "name": name,
                "restored_version": version,
                "invalidated": will_invalidate,
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    funcs: list[Callable[..., dict[str, Any]]] = [
        board_status,
        get_scene_context,
        get_scene_transcript,
        review_scene,
        get_reviews,
        set_board_language,
        propose_scene_selection,
        save_storyline,
        get_storyline,
        script_budget,
        save_script_chapter,
        get_script,
        suggest_scenes_for_script,
        synthesize_script_voice,
        build_cutlist,
        save_contact_sheet,
        render_production,
        review_export,
        save_qa_report,
        revert_artifact,
    ]
    # Optional extra, env-gated (LAURA_SECONDBRAIN_PATH): only offered when a vault is actually
    # configured — same convention as the VLM/voice backends (see brain_tools' module docstring).
    if brain_root() is not None:
        funcs.append(search_second_brain)
        funcs.append(read_brain_note)
    return [ToolSpec(name=f.__name__, description=(f.__doc__ or "").strip(), func=f) for f in funcs]
