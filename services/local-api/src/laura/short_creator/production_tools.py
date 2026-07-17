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
window the entry references (offset, duration cap, per-window roi), clamped inside its own
source range, with an optional zoom timed to when the scene's script line is actually spoken
(the voice sidecar's word ``start_s``, via :func:`line_starts`, offset by ``transition_lead_s``).
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
import math
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..analysis.transition_review import extract_frames
from ..db import repos
from ..db.database import Database
from ..ingest.ffmpeg import ffmpeg_bin
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
    SceneReview,
    Script,
    ScriptLine,
    Storyline,
    VoiceArtifact,
    as_scene_window,
    canvas_for,
)
from .describe import DescribeBackend, resolve_describe_backend
from .toolset import RENDER_WAIT_SECONDS, ToolSpec
from .voice import VoiceBackend, resolve_voice_backend

# Signature of laura.mcp.tools.tool_render_segments — wired in from Task 6 on.
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
    "  \"hook_score\": int 0-10 (how visually gripping for a cold viewer),\n"
    "  \"windows\": [{{\"offset_s\": float, \"duration_s\": float, \"roi\": {{\"x\": float, "
    "\"y\": float, \"w\": float, \"h\": float}} | null}}] (1-4 strong moments, STRONGEST "
    "FIRST, non-overlapping, offsets relative to scene start; a long scene with several "
    "distinct beats should list each beat as its own window; {roi_rule}),\n"
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
    run = repos.get_latest_analysis_run(db, asset_id)
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


# --- voice + cutlist (pure) -------------------------------------------------------------------


def _lines_in_storyline_order(script: Script, storyline: Storyline) -> list[ScriptLine]:
    """The script's lines reordered to follow the STORYLINE's playback order.

    For each chapter in ``storyline.arc`` (in arc order), for each scene_number in that
    chapter's ``scene_numbers`` (in list order), appends the matching line(s) — same chapter +
    scene_number, preserving their relative order for multiple lines per scene. This is the
    order the VIDEO plays in (:func:`build_cutlist` walks ``storyline.arc``/``scene_numbers`` the
    same way), so it is also the order the voice, captions and zoom timing must walk — writing
    a chapter's lines in a different order than its ``scene_numbers`` would otherwise desync
    narration from picture.

    Lines the storyline does not reference (a stale chapter/scene left over from an earlier
    script draft) are appended at the end, in their original ``script.lines`` order — never
    dropped, since the voice must contain every line the author wrote.

    Window references collapse to their scene: a scene listed several times in a chapter
    (with different windows) speaks its line(s) once, at its FIRST occurrence.
    """
    by_key: dict[tuple[int, int], list[ScriptLine]] = {}
    for line in script.lines:
        by_key.setdefault((line.chapter, line.scene_number), []).append(line)

    placed: set[tuple[int, int]] = set()
    ordered: list[ScriptLine] = []
    for chapter in sorted(storyline.arc, key=lambda c: c.chapter):
        for entry in chapter.scene_numbers:
            key = (chapter.chapter, as_scene_window(entry)[0])
            if key in by_key and key not in placed:
                ordered.extend(by_key[key])
                placed.add(key)

    for line in script.lines:
        if (line.chapter, line.scene_number) not in placed:
            ordered.append(line)
    return ordered


def script_text(lines: list[ScriptLine]) -> str:
    """The given lines' spoken text, joined with a single space, in the given order. This exact
    string is what goes to the voice backend and is hashed (:func:`script_hash`) as the
    synthesis cache key — callers pass lines already in a stable, playback-meaningful order
    (see :func:`_lines_in_storyline_order`)."""
    return " ".join(line.text for line in lines)


def script_hash(lines: list[ScriptLine]) -> str:
    """sha256 hex digest over :func:`script_text` of the given (ordered) lines — the
    voice-synthesis cache key (Task 5): an unchanged ordered text (even across an unrelated
    re-save) hits the cache instead of re-synthesizing, while a storyline reorder changes the
    text and therefore correctly busts the cache."""
    return hashlib.sha256(script_text(lines).encode("utf-8")).hexdigest()


def line_starts(
    lines: list[ScriptLine], words: list[dict[str, Any]]
) -> dict[tuple[int, int], float]:
    """Each line's ``(chapter, scene_number)`` mapped to its first word's ``start_s``.

    ``words`` (a voice backend's timings sidecar) are assumed to be the whitespace tokens of
    exactly :func:`script_text` of these SAME ``lines``, in the SAME order — so each line
    "claims" as many words as it has whitespace-split tokens, walking the shared word stream
    forward in that order. A line is absent from the result if the word stream runs out before
    reaching it (e.g. a sidecar shorter than the script) — callers treat a missing entry as "no
    known start" (skip the zoom for that line).
    """
    out: dict[tuple[int, int], float] = {}
    idx = 0
    for line in lines:
        n_tokens = len(line.text.split())
        if n_tokens and idx < len(words):
            out[(line.chapter, line.scene_number)] = _as_float(words[idx].get("start_s"), 0.0)
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
# spread). English: 0.340 (one script — 308 words -> 104.77s, verified by script_hash).
# German is slower because its compounds are long words: the same word count fills 1.7x the
# time. One shared constant made script_budget lie to an English author by that factor — it
# asked for 300 words where 174 seconds needed 512, and the film came out half length.
_SECONDS_PER_WORD: dict[str, float] = {"German": 0.58, "English": 0.340}
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
    *, target_seconds: float, n_scenes: int, window: BestWindow, scene_duration_s: float
) -> float:
    """One segment's BASE cutlist length: the chapter's per-segment time budget, floored at
    2s and capped at the chosen review window's own length (itself floored at 2s, so a short
    highlight doesn't shrink the cap below the floor) — then clamped inside the scene's own
    duration.

    With a usable voice sidecar these are only the WEIGHTS that ``_scale_chapter_durations``
    rescales to fill the chapter's audio window (:func:`chapter_audio_windows`); without one
    they are the segment durations themselves (the pre-coupling behavior)."""
    budget = target_seconds / n_scenes
    upper = max(window.duration_s, _SEGMENT_FLOOR_S)
    return min(max(_SEGMENT_FLOOR_S, min(budget, upper)), scene_duration_s)


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

    def save_storyline(red_thread: str, chapters: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate and save the short's storyline (red thread + chapter arc) to the board.
        A scene_numbers entry is a plain scene number (= that review's primary window 0) or
        {"scene": N, "window": K} to play review window K (0-based, see get_reviews); the
        same scene may appear several times with DIFFERENT windows, the same (scene, window)
        pair only once. Every referenced scene must already have a review on the board and
        every referenced window must exist in that review — rejected with exactly the
        scenes/refs to fix so the agent reviews or corrects them first. A malformed chapter
        is rejected with field-level validation errors instead of raising."""
        try:
            try:
                storyline = Storyline(red_thread=red_thread, arc=[Chapter(**c) for c in chapters])
            except ValidationError as exc:
                return {"ok": False, "errors": _validation_errors(exc)}
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
                return {"ok": False, "reason": detail}
            version = board.save("storyline", storyline)
            return {"ok": True, "version": version}
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
        that into a word count. Call it ONCE before writing, write to ``words``, then
        synthesize and correct against the MEASURED ``voice_s`` from render_production.
        Do not iterate the script by feel: that burned a whole run on 34 saves that never
        reached a render.
        """
        try:
            storyline = board.load("storyline")
            if not isinstance(storyline, Storyline):
                return {"ok": False, "reason": "no storyline on the board; save_storyline first"}
            reviews_by_scene = {r.scene_number: r for r in board.scene_reviews()}
            asset = repos.get_asset(db, asset_id)
            fps = _fps(db, asset) if asset is not None else 30.0
            resolved: list[tuple[BestWindow, float]] = []
            missing: list[int] = []
            for chapter in storyline.arc:
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
                    resolved.append((window, scene_duration_s))

            material = storyline_material_seconds(resolved)
            language = board.meta().language
            return {
                "ok": True,
                "material_seconds": round(material, 1),
                "words": word_budget_for(material, language),
                "language": language,
                "seconds_per_word": seconds_per_word(language),
                "tolerance": _VOICE_RATE_TOLERANCE,
                "segments": len(resolved),
                "unresolved_scenes": missing,
                "how": (
                    "material_seconds is the sum of the reviewed windows this storyline "
                    f"references — the longest video worth cutting. Write about 'words' "
                    f"words of {language} total, then synthesize ONCE and correct from the "
                    f"measured voice_s; the rate is only good to "
                    f"+/-{int(_VOICE_RATE_TOLERANCE * 100)}%."
                ),
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def save_script_chapter(chapter: int, lines: list[dict[str, Any]]) -> dict[str, Any]:
        """Replace one chapter's script lines; every other chapter's lines are kept as-is
        (merge semantics). Lines are validated (each needs scene_number + text; a malformed
        line is rejected with field-level validation errors). language defaults to "de" on
        the first write. Saving invalidates every downstream artifact (voice, cutlist,
        render report, qa report) so they get regenerated from the new script."""
        try:
            try:
                new_lines = [ScriptLine(chapter=chapter, **line) for line in lines]
            except ValidationError as exc:
                return {"ok": False, "errors": _validation_errors(exc)}
            existing = board.load("script")
            language = "de"
            kept: list[ScriptLine] = []
            if isinstance(existing, Script):
                language = existing.language
                kept = [line for line in existing.lines if line.chapter != chapter]
            merged = sorted(kept + new_lines, key=lambda line: line.chapter)
            version = board.save("script", Script(language=language, lines=merged))
            return {"ok": True, "version": version, "total_lines": len(merged)}
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def get_script() -> dict[str, Any]:
        """The board's current script, or a not-found reason if none has been saved yet."""
        try:
            script = board.load("script")
            if script is None:
                return {"ok": False, "reason": "no script on the board"}
            return {"ok": True, "script": script.model_dump()}
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
        the backend itself fails."""
        try:
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

            out_path = Path(str(project["workspace_root"])) / "voiceovers" / f"{new_id()}.mp3"
            result = backend.synthesize(script_text(ordered_lines), out_path)
            if not result.get("ok"):
                return {"ok": False, "reason": str(result.get("reason") or "synthesis failed")}

            words = _read_words(result.get("timings_path"))
            voice_s = _as_float(words[-1].get("end_s"), 0.0) if words else None
            artifact = VoiceArtifact(
                script_hash=new_hash,
                mp3_path=str(out_path),
                timings_path=result.get("timings_path"),
                voice_s=voice_s,
            )
            version = board.save("voice", artifact)
            return {
                "ok": True,
                "cached": False,
                "version": version,
                "mp3_path": artifact.mp3_path,
                "voice_s": voice_s,
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def build_cutlist(transition_lead_s: float = 0.4) -> dict[str, Any]:
        """Deterministically derive a frame-accurate cutlist from storyline + script + voice:
        one CutSegment per scene entry in arc order (chapter, then that chapter's
        scene_numbers order). An entry that references a review window ({"scene": N,
        "window": K}) is cut from THAT window — its offset is the segment start, its length
        the base-duration cap, its roi the zoom region (falling back to the review-level
        roi) — so the same scene can appear several times with different windows. Segment
        lengths are COUPLED TO THE VOICE so picture chapters stay in sync with the one
        continuous voice track: each chapter's audio window (from the word-timings sidecar;
        boundaries midway between adjacent chapters' words, the last chapter running to voice
        end + a short tail) is distributed over its segments proportionally to their
        target_seconds/window base durations — 2s floor per segment, each segment starting at
        its window's offset and stretching past the window's duration_s if needed, but never
        past its scene's end. A chapter the sidecar doesn't cover (or a missing sidecar)
        falls back to the plain target_seconds budget. An optional zoom-in is timed to when
        the scene's script line is actually spoken (word starts, offset ahead by
        transition_lead_s so the zoom lands just before the word lands, not on it). Requires
        save_storyline, save_script_chapter and synthesize_script_voice to have all run first
        — reports which one is missing instead of raising, and rejects a storyline window
        reference the scene's review does not have (fix the storyline or re-review)."""
        try:
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
                base_durations: list[float] = []
                stretch_caps: list[float] = []
                for entry in chapter.scene_numbers:
                    scene_number, window_idx = as_scene_window(entry)
                    resolved = _resolve_scene(db, asset_id, scene_number)
                    if resolved is None:
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

                    resolved_scenes.append((scene_number, src_start, src_end, window, roi))
                    base_durations.append(
                        _segment_duration_s(
                            target_seconds=chapter.target_seconds,
                            n_scenes=n_scenes,
                            window=window,
                            scene_duration_s=scene_duration_s,
                        )
                    )
                    # A segment may stretch past its window's duration_s but keeps the offset
                    # as its start and never crosses the scene's end (the 2s floor still wins
                    # over an offset too close to the end — the frame clamp below pulls the
                    # start back for exactly that case, as before).
                    stretch_caps.append(
                        min(
                            scene_duration_s,
                            max(_SEGMENT_FLOOR_S, scene_duration_s - window.offset_s),
                        )
                    )

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

            if not segments:
                return {"ok": False, "reason": "no scenes resolved from the storyline"}

            board.save("cutlist", Cutlist(segments=segments))
            total_seconds = sum((s.end_frame_exclusive - s.start_frame) / fps for s in segments)
            with_zoom = sum(1 for s in segments if s.zoom_start_s is not None)
            return {
                "ok": True,
                "segments": len(segments),
                "total_seconds": round(total_seconds, 3),
                "with_zoom": with_zoom,
            }
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
            src_w = int(asset.get("width") or 0)
            src_h = int(asset.get("height") or 0)
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
                png_path=str(out_png), cols=cols, rows=rows, labeled=labeled, tiles=tiles
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
        RENDER_WAIT_SECONDS) until it leaves the "rendering" state, then grades three checks:
        voice_fits (the rendered video covers the whole voice track, small tolerance),
        export_ready, and has_voice_timings (captions can be burned in). The RenderReport is
        saved to the board regardless of the verdict, so a failing render stays inspectable.

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
            voice_fits = voice.voice_s is None or (
                video_s + _VOICE_FIT_TOLERANCE_S >= voice.voice_s
            )
            has_voice_timings = bool(voice.timings_path)
            voice_note = (
                f"video={video_s:.2f}s voice={voice.voice_s:.2f}s"
                if voice.voice_s is not None
                else f"video={video_s:.2f}s voice=unknown"
            )

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
            ]
            report = RenderReport(
                export_id=export_id,
                video_s=video_s,
                voice_s=voice.voice_s,
                width=out_w,
                height=out_h,
                checks=checks,
            )
            board.save("render_report", report)
            ok = all(c.ok for c in checks)
            return {"ok": ok, "export_id": export_id, "checks": [c.model_dump() for c in checks]}
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
            try:
                # verdict is plain str at the tool boundary; the Literal check happens here.
                qa_report = QaReport(
                    verdict=verdict,  # type: ignore[arg-type]
                    findings=[QaFinding(**f) for f in findings],
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
        review_scene,
        get_reviews,
        save_storyline,
        get_storyline,
        script_budget,
        save_script_chapter,
        get_script,
        synthesize_script_voice,
        build_cutlist,
        save_contact_sheet,
        render_production,
        review_export,
        save_qa_report,
        revert_artifact,
    ]
    return [ToolSpec(name=f.__name__, description=(f.__doc__ or "").strip(), func=f) for f in funcs]
