"""Transition-smoothness review — pure core (Plan B).

Datatypes + boundary identity + frame-strip planning for the optional VLM review that judges
how fluid each cut is. The model itself is optional (Plan C / ``[vlm]`` extra); everything here
is deterministic and fully testable with the :class:`StubVlmBackend` — no model, no ffmpeg.

Frames are integer source-frame indices, ranges end-exclusive (invariants #1/#2). The cache
identity of a boundary is its **semantic** source-frame pair, never the sequence position (which
drifts when upstream clips are edited) — see :func:`boundary_signature` and spec §3.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from typing import Any, Literal, Protocol

from ..db import repos
from ..db.database import Database
from ..editing.operations import EditClip, roll_boundary
from ..ingest.ffmpeg import ffmpeg_bin

# Editorial window for a resnap nudge (matches editorial.DEFAULT_WINDOW).
RESNAP_WINDOW = 12

TimelineKind = Literal["rough_cut", "scene", "sequence"]
FixKind = Literal["none", "resnap", "transition"]
TransitionStyle = Literal["crossfade", "fade"]
SmoothnessLabel = Literal["smooth", "jump_cut", "hard_jolt", "motion_break"]


@dataclass(frozen=True)
class Boundary:
    """One cut between two adjacent lane-0 clips, in source-frame space (end-exclusive)."""

    timeline_id: str
    kind: TimelineKind
    asset_a: str
    asset_b: str
    src_in_a: int
    src_out_a: int
    src_in_b: int
    src_out_b: int
    seq_in_a: int
    seq_out_a: int  # == boundary_seq_frame (denormalised; NOT part of identity)
    removed_gap_frames: int  # max(0, src_in_b - src_out_a) when same asset, else 0
    same_source: bool  # asset_a == asset_b AND src_in_b == src_out_a (contiguous source)


@dataclass(frozen=True)
class SuggestedFix:
    kind: FixKind
    resnap_delta_frames: int = 0
    transition_style: TransitionStyle = "crossfade"
    transition_frames: int = 0


@dataclass(frozen=True)
class TransitionVerdict:
    smoothness: float
    label: SmoothnessLabel
    reason: str
    suggested_fix: SuggestedFix


def boundary_signature(boundary: Boundary, k: int, proxy_version: str) -> str:
    """Stable hash of a boundary's *semantic* identity + the inputs that change what the model sees.

    Excludes the sequence position (drifts on upstream edits) so a re-review after an unrelated
    edit is a cache hit; includes ``k`` and ``proxy_version`` because they change the extracted
    frames. Spec §3."""
    raw = "|".join(
        str(x)
        for x in (
            boundary.timeline_id,
            boundary.asset_a,
            boundary.asset_b,
            boundary.src_out_a,
            boundary.src_in_b,
            boundary.removed_gap_frames,
            int(boundary.same_source),
            k,
            proxy_version,
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def frame_strip_plan(boundary: Boundary, k: int) -> list[tuple[str, int]]:
    """Ordered ``(asset_id, src_frame)`` refs across the boundary: ≤k frames each side.

    A-side ends at ``src_out_a - 1`` inclusive (end-exclusive range ``[src_out_a-k, src_out_a)``);
    B-side starts at ``src_in_b``. Shorter clips yield fewer frames (no padding)."""
    a_start = max(boundary.src_in_a, boundary.src_out_a - k)
    a_refs = [(boundary.asset_a, f) for f in range(a_start, boundary.src_out_a)]
    b_end = min(boundary.src_in_b + k, boundary.src_out_b)
    b_refs = [(boundary.asset_b, f) for f in range(boundary.src_in_b, b_end)]
    return a_refs + b_refs


class VlmBackend(Protocol):
    """A model (or stub) that judges a single transition from frames + metadata.

    ``review`` receives the boundary frame strip (JPEG bytes, A-side then B-side) and a ``meta``
    dict (``same_source``, ``removed_gap_frames``, ``k``, ``a_count``, ``b_count``) and returns a
    structured verdict. ``model_digest`` pins the verdict's identity for the cache (spec §3)."""

    def available(self) -> bool: ...
    def model_id(self) -> str: ...
    def model_digest(self) -> str: ...
    def review(self, frames: list[bytes], meta: dict[str, object]) -> TransitionVerdict: ...


class StubVlmBackend:
    """Deterministic, model-free backend — the default in tests (no model, no ffmpeg).

    Heuristic: a **contiguous same-source** cut (``same_source`` and ``removed_gap_frames == 0``)
    is the canonical dead-air jump → propose a crossfade. Everything else reads as a clean cut
    between distinct material → no fix. (Note: ``same_source`` already implies a zero gap; the gap
    guard is defensive.)"""

    def available(self) -> bool:
        return True

    def model_id(self) -> str:
        return "stub"

    def model_digest(self) -> str:
        return "stub-v1"

    def review(self, frames: list[bytes], meta: dict[str, object]) -> TransitionVerdict:
        same_source = bool(meta.get("same_source"))
        gap_raw = meta.get("removed_gap_frames", 0)
        gap = gap_raw if isinstance(gap_raw, int) else 0
        if same_source and gap == 0:
            k_raw = meta.get("k", 6)
            k = k_raw if isinstance(k_raw, int) else 6
            return TransitionVerdict(
                smoothness=0.2,
                label="jump_cut",
                reason="contiguous same-source cut (dead-air jump)",
                suggested_fix=SuggestedFix(
                    kind="transition", transition_style="crossfade", transition_frames=k
                ),
            )
        return TransitionVerdict(
            smoothness=0.9,
            label="smooth",
            reason="distinct material",
            suggested_fix=SuggestedFix(kind="none"),
        )


def _make_boundary(
    timeline_id: str, kind: TimelineKind, a: dict[str, Any], b: dict[str, Any]
) -> Boundary:
    """One boundary from two adjacent clip rows (A then B), in source-frame space.

    ``same_source`` is the strict contiguous-same-asset test (a dead-air jump);
    ``removed_gap_frames`` is the source gap when both clips share an asset (0 across assets)."""
    asset_a, asset_b = str(a["asset_id"]), str(b["asset_id"])
    src_out_a, src_in_b = int(a["src_out_frame_exclusive"]), int(b["src_in_frame"])
    removed_gap = max(0, src_in_b - src_out_a) if asset_a == asset_b else 0
    return Boundary(
        timeline_id=timeline_id,
        kind=kind,
        asset_a=asset_a,
        asset_b=asset_b,
        src_in_a=int(a["src_in_frame"]),
        src_out_a=src_out_a,
        src_in_b=src_in_b,
        src_out_b=int(b["src_out_frame_exclusive"]),
        seq_in_a=int(a["seq_in_frame"]),
        seq_out_a=int(a["seq_out_frame_exclusive"]),
        removed_gap_frames=removed_gap,
        same_source=(asset_a == asset_b and src_in_b == src_out_a),
    )


def _lane0_sorted(db: Database, timeline_id: str) -> list[dict[str, Any]]:
    clips = [c for c in repos.list_timeline_clips(db, timeline_id) if int(c.get("lane") or 0) == 0]
    clips.sort(key=lambda c: int(c["seq_in_frame"]))
    return clips


def _sequence_boundaries(db: Database, timeline_id: str) -> list[Boundary]:
    """Boundaries between consecutive scenes of a sequence, resolved to their clip rows.

    Each scene's lane-0 clips are read from its ``scene_timeline_id``; the boundary uses scene A's
    last clip and scene B's first clip. The denormalised ``seq_in_a/seq_out_a`` are sequence-level
    positions (cumulative scene lengths) — identity/frame-strip use only the source fields."""
    resolved: list[tuple[dict[str, Any], dict[str, Any], int]] = []  # (last, first, scene_len)
    for item in repos.list_sequence_items(db, timeline_id):
        scene = repos.get_scene(db, item["scene_id"])
        if scene is None or not scene.get("scene_timeline_id"):
            continue
        clips = _lane0_sorted(db, str(scene["scene_timeline_id"]))
        if not clips:
            continue
        scene_len = max(int(c["seq_out_frame_exclusive"]) for c in clips)
        resolved.append((clips[-1], clips[0], scene_len))
    out: list[Boundary] = []
    cum = 0
    for i in range(len(resolved) - 1):
        last_a, _first_a, len_a = resolved[i]
        _last_b, first_b, _len_b = resolved[i + 1]
        boundary = _make_boundary(timeline_id, "sequence", last_a, first_b)
        out.append(replace(boundary, seq_in_a=cum, seq_out_a=cum + len_a))
        cum += len_a
    return out


def enumerate_boundaries(db: Database, timeline_id: str) -> list[Boundary]:
    """All lane-0 cut boundaries of a timeline, kind-aware (spec §4.2).

    rough_cut / scene: between adjacent lane-0 clips. sequence: between adjacent scenes, resolved
    to their materialised clip rows. Unknown kinds / missing timeline → ``[]``."""
    tl = repos.get_timeline(db, timeline_id)
    if tl is None:
        return []
    kind = str(tl["kind"])
    if kind in ("rough_cut", "scene"):
        clips = _lane0_sorted(db, timeline_id)
        return [
            _make_boundary(timeline_id, kind, a, b)  # type: ignore[arg-type]
            for a, b in zip(clips, clips[1:], strict=False)
        ]
    if kind == "sequence":
        return _sequence_boundaries(db, timeline_id)
    return []


def extract_frames(
    proxy_paths: dict[str, str],
    frame_refs: list[tuple[str, int]],
    *,
    rate_num: int,
    rate_den: int,
) -> list[bytes]:
    """Extract one JPEG (bytes) per ``(asset_id, src_frame)`` ref from that asset's proxy.

    ``proxy_paths`` maps each asset id to its resolved on-disk proxy path (A-side and B-side may be
    different assets). Frame→time uses the project rate; the proxy is CFR so the index→time mapping
    is exact. A ref whose asset has no proxy path (or whose extraction fails) is skipped, so the
    returned list may be shorter than ``frame_refs``. Deterministic: exact ``-ss``, mjpeg q=2."""
    out: list[bytes] = []
    for asset_id, frame in frame_refs:
        path = proxy_paths.get(asset_id)
        if not path:
            continue
        t = max(0, frame) * rate_den / rate_num
        cmd = [
            ffmpeg_bin(),
            "-v",
            "error",
            "-ss",
            f"{t:.6f}",
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
        proc = subprocess.run(cmd, capture_output=True)  # noqa: S603
        if proc.returncode == 0 and proc.stdout:
            out.append(proc.stdout)
    return out


def _find_boundary_pair(
    lane0_rows: list[dict[str, Any]], identity: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """The adjacent lane-0 (A, B) row pair matching a boundary's semantic identity, or None."""
    for ar, br in zip(lane0_rows, lane0_rows[1:], strict=False):
        if (
            str(ar["asset_id"]) == str(identity["asset_a"])
            and str(br["asset_id"]) == str(identity["asset_b"])
            and int(ar["src_out_frame_exclusive"]) == int(identity["src_out_a"])
            and int(br["src_in_frame"]) == int(identity["src_in_b"])
            and int(ar["seq_out_frame_exclusive"]) == int(br["seq_in_frame"])
        ):
            return ar, br
    return None


def apply_fix(
    db: Database, *, timeline_id: str, identity: dict[str, Any], fix: SuggestedFix
) -> dict[str, Any]:
    """Apply a fix at the boundary identified by ``identity`` (asset_a/asset_b/src_out_a/src_in_b).

    ``resnap`` rolls the cut (works on rough_cut/scene/sequence lane-0 clips; transitions kept);
    ``transition`` sets clip A's ``transition_after`` (crossfade/fade — rendered by Plan A);
    ``none`` is a no-op. Returns ``{"status": "ok"|"error", ...}``. The delta is clamped to the
    editorial window and the in-clip bounds; a clamp to 0 is an error (no effective change)."""
    if fix.kind == "none":
        return {"status": "ok", "applied": "none"}
    rows = repos.list_timeline_clips(db, timeline_id)
    lane0 = sorted(
        (r for r in rows if int(r.get("lane") or 0) == 0), key=lambda r: int(r["seq_in_frame"])
    )
    pair = _find_boundary_pair(lane0, identity)
    if pair is None:
        return {"status": "error", "reason": "boundary not found"}
    a_row, b_row = pair

    if fix.kind == "transition":
        repos.set_clip_transition(
            db,
            clip_id=str(a_row["id"]),
            kind=fix.transition_style,
            frames=int(fix.transition_frames),
        )
        return {"status": "ok", "applied": "transition", "style": fix.transition_style}

    # resnap — clamp to the editorial window and the in-clip length bounds.
    len_a = int(a_row["src_out_frame_exclusive"]) - int(a_row["src_in_frame"])
    len_b = int(b_row["src_out_frame_exclusive"]) - int(b_row["src_in_frame"])
    lo, hi = -(len_a - 1), (len_b - 1)
    delta = max(lo, min(hi, max(-RESNAP_WINDOW, min(RESNAP_WINDOW, int(fix.resnap_delta_frames)))))
    if delta == 0:
        return {"status": "error", "reason": "no effective resnap delta"}
    boundary_seq = int(a_row["seq_out_frame_exclusive"])
    # Validate the roll against the pure op (speed-1 guard, range), then persist the two clips'
    # frame columns directly so the transition_after_* fields survive (replace would drop them).
    try:
        roll_boundary([EditClip.from_row(r) for r in rows], boundary_seq, delta)
    except ValueError as exc:
        return {"status": "error", "reason": str(exc)}
    repos.update_clip_frames(
        db,
        str(a_row["id"]),
        src_in_frame=int(a_row["src_in_frame"]),
        src_out_frame_exclusive=int(a_row["src_out_frame_exclusive"]) + delta,
        seq_in_frame=int(a_row["seq_in_frame"]),
        seq_out_frame_exclusive=int(a_row["seq_out_frame_exclusive"]) + delta,
    )
    repos.update_clip_frames(
        db,
        str(b_row["id"]),
        src_in_frame=int(b_row["src_in_frame"]) + delta,
        src_out_frame_exclusive=int(b_row["src_out_frame_exclusive"]),
        seq_in_frame=int(b_row["seq_in_frame"]) + delta,
        seq_out_frame_exclusive=int(b_row["seq_out_frame_exclusive"]),
    )
    return {"status": "ok", "applied": "resnap", "delta": delta}


def default_backend() -> VlmBackend | None:
    """The configured real backend, or ``None`` when the ``[vlm]`` model isn't set up.

    Opt-in & local-first: returns an :class:`OllamaVlmBackend` only when ``LAURA_VLM_MODEL`` (or
    ``LAURA_VLM=1``) is set AND the model is locally available; otherwise ``None`` (so the backend
    starts/serves cache + apply-fix without a model, and tests inject :class:`StubVlmBackend`)."""
    if not (os.environ.get("LAURA_VLM_MODEL") or os.environ.get("LAURA_VLM")):
        return None
    from .vlm_ollama import OllamaVlmBackend  # lazy — avoids an import cycle

    backend = OllamaVlmBackend()
    return backend if backend.available() else None


def vlm_available() -> bool:
    return default_backend() is not None


FrameExtractor = Callable[..., list[bytes]]


def _proxy_paths_for(db: Database, asset_ids: set[str]) -> dict[str, str]:
    """Resolve each asset's on-disk proxy path (first ``is_proxy`` file), for frame extraction."""
    out: dict[str, str] = {}
    for asset_id in asset_ids:
        for f in repos.list_asset_files(db, asset_id):
            if f.get("is_proxy"):
                out[asset_id] = str(f["path"])
                break
    return out


def run_transition_review(
    db: Database,
    timeline_id: str,
    *,
    backend: VlmBackend,
    k: int = 6,
    frame_extractor: FrameExtractor = extract_frames,
    progress: Callable[[int, int], None] | None = None,
) -> dict[str, int]:
    """Review every boundary of ``timeline_id`` with ``backend``, caching by identity + digest.

    A boundary already cached for this ``model_digest`` is skipped (no inference), so a re-run after
    no change does zero model calls (idempotency, spec §6). ``frame_extractor`` is injectable for
    tests (the stub backend ignores the frames). Returns ``{total, reviewed, inferences}``."""
    boundaries = enumerate_boundaries(db, timeline_id)
    total = len(boundaries)
    if total == 0:
        return {"total": 0, "reviewed": 0, "inferences": 0}
    tl = repos.get_timeline(db, timeline_id)
    project = repos.get_project(db, str(tl["project_id"])) if tl else None
    rate_num = int(project["sequence_rate_num"]) if project else 30
    rate_den = int(project["sequence_rate_den"]) if project else 1
    proxy_paths = _proxy_paths_for(
        db, {b.asset_a for b in boundaries} | {b.asset_b for b in boundaries}
    )
    digest, model_id = backend.model_digest(), backend.model_id()
    reviewed = inferences = 0
    for b in boundaries:
        cached = repos.get_cached_review(
            db,
            timeline_id=timeline_id,
            asset_a=b.asset_a,
            asset_b=b.asset_b,
            src_out_a=b.src_out_a,
            src_in_b=b.src_in_b,
            model_digest=digest,
        )
        if cached is None:
            refs = frame_strip_plan(b, k)
            frames = frame_extractor(proxy_paths, refs, rate_num=rate_num, rate_den=rate_den)
            a_count = b.src_out_a - max(b.src_in_a, b.src_out_a - k)
            b_count = min(b.src_in_b + k, b.src_out_b) - b.src_in_b
            verdict = backend.review(
                frames,
                {
                    "same_source": b.same_source,
                    "removed_gap_frames": b.removed_gap_frames,
                    "k": k,
                    "a_count": a_count,
                    "b_count": b_count,
                },
            )
            repos.upsert_transition_review(
                db,
                timeline_id=timeline_id,
                asset_a=b.asset_a,
                asset_b=b.asset_b,
                src_out_a=b.src_out_a,
                src_in_b=b.src_in_b,
                boundary_seq_frame=b.seq_out_a,
                boundary_signature=boundary_signature(b, k, proxy_paths.get(b.asset_a, "")),
                smoothness=verdict.smoothness,
                label=verdict.label,
                reason=verdict.reason,
                suggested_fix_json=json.dumps(asdict(verdict.suggested_fix)),
                model_id=model_id,
                model_digest=digest,
            )
            inferences += 1
        reviewed += 1
        if progress is not None:
            progress(reviewed, total)
    return {"total": total, "reviewed": reviewed, "inferences": inferences}
