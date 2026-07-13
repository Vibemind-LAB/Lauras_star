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
to save chapters that reference a scene without a review yet. ``save_script_chapter`` merges by
chapter (other chapters' lines are kept, only the given chapter's lines are replaced) and, like
every ``board.save()`` call, invalidates every artifact downstream in the chain (voice, cutlist,
render report, qa report).

``synthesize_script_voice``/``build_cutlist`` are the Slice-3 core toolkit (Task 5).
``synthesize_script_voice`` turns the board's script into an mp3 + word-timings sidecar through
``deps.voice_backend`` (falling back to :func:`laura.short_creator.voice.resolve_voice_backend`,
same graceful-without-config pattern as the VLM), cached by a sha256 of the script's text
(:func:`script_hash`) so re-running after an unrelated board change is a no-op. ``build_cutlist``
is a *pure derivation* — no model or backend calls — turning storyline + script + voice into a
frame-accurate ``Cutlist``: one ``CutSegment`` per scene in arc order, clamped inside its own
source range, with an optional zoom timed to when the scene's script line is actually spoken
(the voice sidecar's word ``start_s``, via :func:`line_starts`, offset by ``transition_lead_s``).

``render_production``/``review_export``/``save_qa_report`` close out the pipeline (Task 6).
``render_production`` turns the cutlist into the actual render call (``deps.render_segments``,
falling back to the real :func:`laura.mcp.tools.tool_render_segments`, resolved lazily like every
other backend seam): segments and an index-aligned zoom hint per segment, the board's voice as
the new audio track, captions on, blurred vertical 1080x1920 — then polls the resulting export
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
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ..analysis.transition_review import extract_frames
from ..db import repos
from ..db.database import Database
from ..ingest.ffmpeg import ffmpeg_bin
from ..util import new_id, utcnow_iso
from . import context
from .board import Board
from .board_models import (
    BestWindow,
    Chapter,
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
)
from .describe import DescribeBackend, resolve_describe_backend
from .toolset import RENDER_WAIT_SECONDS, ToolSpec
from .voice import VoiceBackend, resolve_voice_backend

# Signature of laura.mcp.tools.tool_render_segments — wired in from Task 6 on.
RenderSegmentsFn = Callable[..., dict[str, Any]]

_MIN_WINDOW_S = 0.01
_DEFAULT_WINDOW_S = 4.0
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
    "  \"best_window\": {{\"offset_s\": float, \"duration_s\": float}} (strongest moment, "
    "relative to scene start),\n"
    "  \"roi\": {{\"x\": float, \"y\": float, \"w\": float, \"h\": float}} | null (normalized "
    "0-1 box around the ONE region a viewer must read; null if the whole frame matters),\n"
    "  \"legibility_notes\": str}}"
)

_RENDER_POLL_INTERVAL_S = 2.0
_VOICE_FIT_TOLERANCE_S = 0.05
_RENDER_WIDTH = 1080
_RENDER_HEIGHT = 1920

_QA_PROMPT = (
    "You are QA-checking a finished vertical short (1080x1920) before it ships. Look at this "
    "single frame and reply in ONE short, concrete sentence: is the subject/text legible, well "
    "framed inside the vertical canvas, and free of visual glitches? Name anything a viewer "
    "would notice as wrong; say \"looks fine\" if nothing is."
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
    """Clamp a proposed best window inside ``[0, scene_duration_s]``.

    Length is preserved where possible — only the offset gives way when the requested window
    would run past the end of the scene — and length itself is capped to the scene's own
    duration, with a small floor so the window is always positive (a pydantic requirement).
    """
    offset_raw, length_raw = 0.0, min(_DEFAULT_WINDOW_S, scene_duration_s)
    if isinstance(raw, dict):
        offset_raw = _as_float(raw.get("offset_s"), offset_raw)
        length_raw = _as_float(raw.get("duration_s"), length_raw)
    length = max(_MIN_WINDOW_S, min(length_raw, scene_duration_s))
    max_offset = max(0.0, scene_duration_s - length)
    offset = max(0.0, min(offset_raw, max_offset))
    return BestWindow(offset_s=offset, duration_s=length)


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


def script_text(script: Script) -> str:
    """The script's full spoken text: lines ordered by ``(chapter, their list position)``,
    joined with a single space. This exact string is what goes to the voice backend and is
    hashed (:func:`script_hash`) as the synthesis cache key — so its ordering must be stable."""
    ordered = sorted(script.lines, key=lambda line: line.chapter)
    return " ".join(line.text for line in ordered)


def script_hash(script: Script) -> str:
    """sha256 hex digest over :func:`script_text` — the voice-synthesis cache key (Task 5):
    unchanged text (even across an unrelated re-save) hits the cache instead of re-synthesizing."""
    return hashlib.sha256(script_text(script).encode("utf-8")).hexdigest()


def line_starts(script: Script, words: list[dict[str, Any]]) -> dict[tuple[int, int], float]:
    """Each line's ``(chapter, scene_number)`` mapped to its first word's ``start_s``.

    ``words`` (a voice backend's timings sidecar) are assumed to be the whitespace tokens of
    exactly :func:`script_text`'s output, in order — so each line "claims" as many words as it
    has whitespace-split tokens, walking the shared word stream forward in the same
    ``(chapter, list position)`` order ``script_text`` joined them in. A line is absent from the
    result if the word stream runs out before reaching it (e.g. a sidecar shorter than the
    script) — callers treat a missing entry as "no known start" (skip the zoom for that line).
    """
    ordered = sorted(script.lines, key=lambda line: line.chapter)
    out: dict[tuple[int, int], float] = {}
    idx = 0
    for line in ordered:
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


def _segment_duration_s(
    *, target_seconds: float, n_scenes: int, best_window: BestWindow, scene_duration_s: float
) -> float:
    """One scene's cutlist-segment length: the chapter's per-scene time budget, floored at 2s
    and capped at the best_window's own length (itself floored at 2s, so a short highlight
    doesn't shrink the cap below the floor) — then clamped inside the scene's own duration."""
    budget = target_seconds / n_scenes
    upper = best_window.duration_s if best_window.duration_s > 2.0 else 2.0
    return min(max(2.0, min(budget, upper)), scene_duration_s)


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
        validated SceneReview to the board. Never fails the pipeline: without a configured VLM,
        with an empty or unparseable reply, or when no frames could be extracted, it writes a
        transcript-only *degraded* review instead (``degraded=True``, neutral hook_score, no
        roi) so downstream steps can still proceed."""
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
                prompt = _REVIEW_PROMPT.format(
                    n=len(frames), scene=scene_number, duration_s=duration_s, snippet=snippet
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
                review = SceneReview(
                    scene_number=scene_number,
                    src_start_frame=src_start,
                    src_end_frame_exclusive=src_end_exclusive,
                    description=str(parsed.get("description") or ""),
                    whats_happening=str(parsed.get("whats_happening") or ""),
                    hook_score=_clamp_hook_score(parsed.get("hook_score")),
                    best_window=_clamp_best_window(parsed.get("best_window"), duration_s),
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
                "roi": review.roi.model_dump() if review.roi is not None else None,
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def get_reviews() -> dict[str, Any]:
        """All saved scene reviews, compact (scene, hook score, degraded flag, has-roi, blurb)."""
        try:
            reviews = board.scene_reviews()
            return {
                "ok": True,
                "reviews": [
                    {
                        "scene_number": r.scene_number,
                        "hook_score": r.hook_score,
                        "degraded": r.degraded,
                        "has_roi": r.roi is not None,
                        "description": r.description[:_DESCRIPTION_PREVIEW_CHARS],
                    }
                    for r in reviews
                ],
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}

    def save_storyline(red_thread: str, chapters: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate and save the short's storyline (red thread + chapter arc) to the board.
        Every chapter's scene_numbers must already have a scene review on the board — a
        chapter referencing an unreviewed scene is rejected with the missing scene numbers
        so the agent reviews them first. A malformed chapter is rejected with field-level
        validation errors instead of raising."""
        try:
            referenced = sorted(
                {int(n) for c in chapters for n in (c.get("scene_numbers") or [])}
            )
            reviewed = {r.scene_number for r in board.scene_reviews()}
            missing = [n for n in referenced if n not in reviewed]
            if missing:
                return {"ok": False, "reason": f"scenes without review: {missing}"}
            try:
                storyline = Storyline(red_thread=red_thread, arc=[Chapter(**c) for c in chapters])
            except ValidationError as exc:
                return {"ok": False, "errors": _validation_errors(exc)}
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
        """Speak the board's current script with the configured voice backend, caching by a
        hash of the script text — a re-run after an unrelated board change is a no-op
        (``cached: True``). Requires save_script_chapter to have run first. On a fresh
        synthesis, the mp3 plus a word-timings sidecar (used for caption burn-in and
        build_cutlist's zoom timing) are saved as the board's voice artifact. Gracefully
        reports ``ok: False`` without raising when no voice backend is configured or the
        backend itself fails."""
        try:
            script = board.load("script")
            if not isinstance(script, Script):
                return {"ok": False, "reason": "no script on the board"}
            new_hash = script_hash(script)

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
            result = backend.synthesize(script_text(script), out_path)
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
        one CutSegment per scene in arc order (chapter, then that chapter's scene_numbers
        order), each segment's length from the chapter's time budget clamped to its scene's
        best_window and the scene's own duration, and an optional zoom-in timed to when the
        scene's script line is actually spoken (from the voice sidecar's word starts, offset
        ahead by transition_lead_s so the zoom lands just before the word lands, not on it).
        Requires save_storyline, save_script_chapter and synthesize_script_voice to have all
        run first — reports which one is missing instead of raising."""
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
            line_map = line_starts(script, _read_words(voice.timings_path))
            reviews_by_scene = {r.scene_number: r for r in board.scene_reviews()}

            segments: list[CutSegment] = []
            video_start_s = 0.0
            order = 0
            for chapter in sorted(storyline.arc, key=lambda c: c.chapter):
                n_scenes = len(chapter.scene_numbers)
                for scene_number in chapter.scene_numbers:
                    resolved = _resolve_scene(db, asset_id, scene_number)
                    if resolved is None:
                        continue
                    src_start, src_end, _text = resolved
                    scene_duration_s = (src_end - src_start) / fps

                    review = reviews_by_scene.get(scene_number)
                    if review is not None:
                        best_window, roi = review.best_window, review.roi
                    else:
                        best_window = BestWindow(
                            offset_s=0.0, duration_s=min(_DEFAULT_WINDOW_S, scene_duration_s)
                        )
                        roi = None

                    seg_dur_s = _segment_duration_s(
                        target_seconds=chapter.target_seconds,
                        n_scenes=n_scenes,
                        best_window=best_window,
                        scene_duration_s=scene_duration_s,
                    )
                    dur_frames = round(seg_dur_s * fps)
                    raw_start = src_start + round(best_window.offset_s * fps)
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

    def render_production() -> dict[str, Any]:
        """Render the board's cutlist to a finished vertical export and grade it.

        Requires build_cutlist (and transitively storyline + script + voice) to have run first
        — reports which one is missing instead of raising. Turns the cutlist into
        (start_frame, end_frame_exclusive) segments plus an index-aligned zoom hint per segment
        (only where that segment has BOTH a roi and a zoom_start_s; otherwise None), and renders
        them with the v1 short defaults (captions on, blurred vertical 1080x1920 letterbox) and
        the board's voice as the new audio track. Polls the resulting export (bounded by
        RENDER_WAIT_SECONDS) until it leaves the "rendering" state, then grades three checks:
        voice_fits (the rendered video covers the whole voice track, small tolerance),
        export_ready, and has_voice_timings (captions can be burned in). The RenderReport is
        saved to the board regardless of the verdict, so a failing render stays inspectable.

        CODING-AGENT CHARTER: if voice_fits comes back False, do NOT shorten or cut the voice —
        rebuild the cutlist with a longer per-chapter time budget (build_cutlist) and render
        again. The voice is the script the team already agreed on; the video must fit it.
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

            result = render_fn(
                db,
                asset_id,
                segments,
                captions=True,
                fit="blur",
                vertical=True,
                out_size=(_RENDER_WIDTH, _RENDER_HEIGHT),
                voiceover_path=voice.mp3_path,
                voiceover_text=script_text(script),
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
                width=_RENDER_WIDTH,
                height=_RENDER_HEIGHT,
                checks=checks,
            )
            board.save("render_report", report)
            ok = export_ready and all(c.ok for c in checks)
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
            frames = _grab_video_frames(Path(str(path)), times)
            notes = [
                {"at_s": t, "note": backend.describe([frame], _QA_PROMPT)}
                for t, frame in zip(times, frames, strict=False)
            ]
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

    funcs: list[Callable[..., dict[str, Any]]] = [
        board_status,
        get_scene_context,
        review_scene,
        get_reviews,
        save_storyline,
        get_storyline,
        save_script_chapter,
        get_script,
        synthesize_script_voice,
        build_cutlist,
        render_production,
        review_export,
        save_qa_report,
    ]
    return [ToolSpec(name=f.__name__, description=(f.__doc__ or "").strip(), func=f) for f in funcs]
