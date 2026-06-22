"""Job handler: ai.reenact — consent-gated portrait reenactment.

SAFETY-CRITICAL: the consent gate is checked FIRST, before any DB writes or
file I/O.  If the consent record is missing or the payload is malformed the
handler raises immediately and creates nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..db import repos
from ..db.database import Database
from ..jobs.keys import idempotency_key_for
from ..jobs.queues import queue_for
from ..jobs.runner import JobContext, JobHandler, enqueue
from ..render.mp4 import render_clips_mp4
from ..render.sync import assert_or_fix_media_sync
from ..sequences.flatten import flatten_sequence
from ..util import new_id
from .auto_pipeline import plan_lipsync_after_voiceover
from .lipsync_backend import resolve_lipsync_backend
from .provenance import write_ai_provenance_manifest
from .reenact_backend import resolve_reenact_backend
from .voiceover_backend import DEFAULT_VOICEOVER_SAMPLE_RATE, resolve_voiceover_backend

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RuntimeBackendConfig:
    name: str | None
    base_url: str | None
    runtime_id: str | None = None


_EFFECT_BACKENDS = {
    "voice": "sidecar",
    "reenact": "liveportrait",
    "lipsync": "vibevideo",
}


def _runtime_base_url(runtime: dict[str, Any]) -> str | None:
    raw = runtime.get("base_url")
    if isinstance(raw, str) and raw.strip():
        return raw.rstrip("/")
    port = runtime.get("port")
    if port is not None:
        return f"http://127.0.0.1:{int(port)}"
    return None


def _backend_config_from_runtime(
    db: Database,
    runtime_id: str | None,
    fallback: str | None,
    effect: str,
) -> RuntimeBackendConfig:
    if runtime_id is None:
        return RuntimeBackendConfig(fallback, None, None)

    runtime = repos.get_ai_runtime(db, runtime_id)
    if runtime is None:
        raise ValueError(f"runtime not found: {runtime_id!r}")
    if not bool(runtime["enabled"]):
        raise ValueError("runtime is disabled")
    if str(runtime["effect"]) != effect:
        raise ValueError(f"runtime effect must be {effect}")

    kind = str(runtime["kind"])
    if kind == "stub":
        return RuntimeBackendConfig("stub", None, runtime_id)
    if kind in {"external_http", "container"}:
        base_url = _runtime_base_url(runtime)
        if base_url is None:
            raise ValueError("runtime has no base_url or port")
        return RuntimeBackendConfig(_EFFECT_BACKENDS[effect], base_url, runtime_id)
    raise ValueError(f"unsupported runtime kind: {kind!r}")


def _backend_from_runtime(
    db: Database,
    runtime_id: str | None,
    fallback: str | None,
    effect: str | None = None,
) -> str | None:
    if effect is None:
        if runtime_id is None:
            return fallback
        runtime = repos.get_ai_runtime(db, runtime_id)
        if runtime is None:
            raise ValueError(f"runtime not found: {runtime_id!r}")
        effect = str(runtime["effect"])
    return _backend_config_from_runtime(db, runtime_id, fallback, effect).name


def handle_reenact(ctx: JobContext) -> dict[str, Any]:
    """Consent-gated reenact job.

    Steps (in order):
    1. Consent gate — raises immediately if consent_id is missing or unknown.
    2. Resolve timeline + project (rate_num / rate_den).
    3. Extract the driving range from base clip rows that overlap [seq_in, seq_out).
    4. Render the driving clip to a temporary MP4.
    5. Resolve and check the reenact backend.
    6. Animate the portrait asset, write to the project workspace.
    7. Register the output as a synthetic asset.
    8. Place a replace-overlay clip on the timeline.
    """
    payload = ctx.payload

    # ── 1. CONSENT GATE (must be first — creates nothing on failure) ──────────
    consent_id: str | None = payload.get("consent_id")
    if not consent_id:
        raise ValueError("ai.reenact: payload missing required key 'consent_id'")

    consent = repos.get_consent_record(ctx.db, consent_id)
    if consent is None:
        raise ValueError(
            f"ai.reenact: consent record not found: {consent_id!r} — "
            "refusing to create any asset or clip"
        )
    if consent.get("revoked_at"):
        raise ValueError(
            f"ai.reenact: consent {consent_id!r} has been revoked — "
            "refusing to create any asset or clip"
        )

    # ── 2. Resolve timeline + project ────────────────────────────────────────
    timeline_id: str = payload["timeline_id"]
    tl = repos.get_timeline(ctx.db, timeline_id)
    if tl is None:
        raise ValueError(f"ai.reenact: timeline not found: {timeline_id!r}")

    project = repos.get_project(ctx.db, tl["project_id"])
    if project is None:
        raise ValueError(f"ai.reenact: project not found: {tl['project_id']!r}")

    rate_num: int = int(project["sequence_rate_num"])
    rate_den: int = int(project["sequence_rate_den"])

    seq_in: int = int(payload["seq_in_frame"])
    seq_out: int = int(payload["seq_out_frame_exclusive"])

    # ── 3. Build the driving clip list from overlapping ORIGINAL base rows ────
    # Drive from original base footage ONLY — never from a prior synthetic
    # replace-overlay (provenance: a reenact must not be driven by another
    # reenact's output). So we resolve base rows directly, WITHOUT precedence.
    if tl.get("kind") == "sequence":
        base_rows = flatten_sequence(ctx.db, tl["id"])
    else:
        base_rows = [
            r
            for r in repos.list_timeline_clips(ctx.db, tl["id"])
            if r.get("role", "base") != "replace"
        ]

    driving_clips: list[tuple[Path, int, int]] = []
    for row in base_rows:
        row_seq_in = int(row["seq_in_frame"])
        row_seq_out = int(row["seq_out_frame_exclusive"])
        row_src_in = int(row["src_in_frame"])

        o_in = max(row_seq_in, seq_in)
        o_out = min(row_seq_out, seq_out)
        if o_in >= o_out:
            continue  # no overlap

        s_in = row_src_in + (o_in - row_seq_in)
        s_out = row_src_in + (o_out - row_seq_in)

        asset = repos.get_asset(ctx.db, row["asset_id"])
        if asset is None:
            raise ValueError(f"ai.reenact: asset not found: {row['asset_id']!r}")

        driving_clips.append((Path(asset["source_path"]), s_in, s_out))

    if not driving_clips:
        raise ValueError(
            f"ai.reenact: no base clips overlap driving range [{seq_in}, {seq_out})"
        )

    # ── 4. Render the driving range to a temp file ────────────────────────────
    workspace = Path(project["workspace_root"])
    tmp_dir = workspace / "tmp"
    synthetic_dir = workspace / "synthetic"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    synthetic_dir.mkdir(parents=True, exist_ok=True)

    # Bind both paths up front so out_path can be cleaned on ANY failure below
    # (no orphaned, unlabelled partial synthetic file may ever survive a crash).
    driving_tmp = tmp_dir / f"{new_id()}.driving.mp4"
    out_path = synthetic_dir / f"{new_id()}.mp4"
    try:
        render_clips_mp4(
            driving_clips,
            driving_tmp,
            rate_num=rate_num,
            rate_den=rate_den,
        )

        # ── 5. Resolve and validate backend ──────────────────────────────────
        backend_config = _backend_config_from_runtime(
            ctx.db,
            payload.get("runtime_id"),
            payload.get("backend"),
            "reenact",
        )
        backend = resolve_reenact_backend(
            backend_config.name,
            base_url=backend_config.base_url,
        )
        if not backend.available():
            raise RuntimeError(
                f"ai.reenact: reenact backend '{backend.name}' is not installed"
            )

        # ── 6. Portrait asset → animate ──────────────────────────────────────
        portrait_asset_id: str = payload["portrait_asset_id"]
        portrait = repos.get_asset(ctx.db, portrait_asset_id)
        if portrait is None:
            raise ValueError(
                f"ai.reenact: portrait asset not found: {portrait_asset_id!r}"
            )

        backend.reenact(
            driving_path=driving_tmp,
            portrait_path=Path(portrait["source_path"]),
            out_path=out_path,
            fps_num=rate_num,
            fps_den=rate_den,
        )
        assert_or_fix_media_sync(
            out_path,
            expected_frames=seq_out - seq_in,
            rate_num=rate_num,
            rate_den=rate_den,
            require_video=True,
            fix=True,
        )
    except Exception:
        # Never leave an unlabelled partial synthetic file behind on failure.
        out_path.unlink(missing_ok=True)
        raise
    finally:
        # The temporary driving file is never needed after this block.
        driving_tmp.unlink(missing_ok=True)

    # ── 7. Register the output as a synthetic asset ───────────────────────────
    asset = repos.create_asset(
        ctx.db,
        project_id=tl["project_id"],
        type="video",
        display_name=f"reenact {seq_in}-{seq_out}",
        source_path=str(out_path),
        synthetic=True,
        ai_effect="reenact",
    )
    write_ai_provenance_manifest(
        media_path=out_path,
        asset_id=asset["id"],
        project_id=tl["project_id"],
        ai_effect="reenact",
        source={
            "timeline_id": tl["id"],
            "portrait_asset_id": portrait_asset_id,
            "consent_id": consent_id,
            "backend": payload.get("backend") or "stub",
            "seq_in_frame": seq_in,
            "seq_out_frame_exclusive": seq_out,
        },
    )

    # ── 8. Place a replace-overlay clip on the timeline ───────────────────────
    repos.add_timeline_clip(
        ctx.db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=seq_out - seq_in,
        seq_in_frame=seq_in,
        seq_out_frame_exclusive=seq_out,
        lane=1,
        role="replace",
    )

    return {
        "asset_id": asset["id"],
        "out_path": str(out_path),
        "seq_in_frame": seq_in,
        "seq_out_frame_exclusive": seq_out,
    }


def register_ai_handlers(registry: dict[str, JobHandler]) -> None:
    """Register all AI-stage job handlers into ``registry``."""
    registry["ai.reenact"] = handle_reenact
    registry["ai.voiceover"] = handle_voiceover
    registry["ai.lipsync"] = handle_lipsync


def _maybe_enqueue_lipsync_after_vo(
    ctx: JobContext,
    *,
    timeline: dict[str, Any],
    project: dict[str, Any],
    vo_asset_id: str,
    seq_in: int,
    seq_out: int,
) -> tuple[str | None, str | None]:
    """Probe the VO span for a face and, if consent exists, enqueue ai.lipsync (spec §5).

    No face / sidecar absent / no consent -> (None, reason); never raises into the VO
    success path (a lipsync hiccup must not fail the VO that already rendered).
    """
    try:
        backend = resolve_lipsync_backend(None)
        if not backend.available():
            return None, "no_backend"
        rate_num = int(project["sequence_rate_num"])
        rate_den = int(project["sequence_rate_den"])
        if timeline.get("kind") == "sequence":
            base_rows = flatten_sequence(ctx.db, timeline["id"])
        else:
            base_rows = [
                r for r in repos.list_timeline_clips(ctx.db, timeline["id"])
                if r.get("role", "base") != "replace" and int(r.get("lane") or 0) == 0
            ]
        clips: list[tuple[Path, int, int]] = []
        for row in base_rows:
            r_in, r_out = int(row["seq_in_frame"]), int(row["seq_out_frame_exclusive"])
            o_in, o_out = max(r_in, seq_in), min(r_out, seq_out)
            if o_in >= o_out:
                continue
            asset = repos.get_asset(ctx.db, row["asset_id"])
            if asset is None:
                continue
            base = int(row["src_in_frame"])
            clips.append((Path(asset["source_path"]), base + (o_in - r_in), base + (o_out - r_in)))
        if not clips:
            return None, "no_face"
        tmp_dir = Path(project["workspace_root"]) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        driving = tmp_dir / f"{new_id()}.vo-probe.mp4"
        vo_asset = repos.get_asset(ctx.db, vo_asset_id)
        audio_path = Path(vo_asset["source_path"]) if vo_asset else None
        try:
            render_clips_mp4(clips, driving, rate_num=rate_num, rate_den=rate_den)
            probe = backend.probe(video_path=driving, audio_path=audio_path or driving)
        finally:
            driving.unlink(missing_ok=True)
        consent_id = repos.get_active_consent_id(ctx.db, project["id"])
        plan = plan_lipsync_after_voiceover(
            probe_face_detected=bool(probe.face_detected),
            probe_mouth_visible=bool(probe.mouth_visible),
            consent_id=consent_id,
            audio_asset_id=vo_asset_id,
            seq_in_frame=seq_in,
            seq_out_frame_exclusive=seq_out,
        )
        if not plan.should_enqueue:
            return None, plan.reason
        payload: dict[str, Any] = {
            "timeline_id": timeline["id"],
            "seq_in_frame": plan.seq_in_frame,
            "seq_out_frame_exclusive": plan.seq_out_frame_exclusive,
            "audio_asset_id": plan.audio_asset_id,
            "consent_id": plan.consent_id,
            "license_accepted": True,
        }
        job_id = enqueue(
            ctx.db,
            queue=queue_for("ai.lipsync", default="ai"),
            kind="ai.lipsync",
            payload=payload,
            max_attempts=2,
            idempotency_key=idempotency_key_for("ai.lipsync", payload),
            caused_by_job_id=ctx.job_id,
        )
        return job_id, "ok"
    except Exception:  # noqa: BLE001 - VO already succeeded; lipsync is best-effort
        _log.warning(
            "lipsync auto-enqueue failed after voiceover; VO result preserved",
            exc_info=True,
        )
        return None, "probe_error"


def handle_voiceover(ctx: JobContext) -> dict[str, Any]:
    """Generate a synthetic voiceover WAV and place it on the timeline's A2 lane."""
    payload = ctx.payload
    timeline_id = str(payload["timeline_id"])
    timeline = repos.get_timeline(ctx.db, timeline_id)
    if timeline is None:
        raise ValueError(f"ai.voiceover: timeline not found: {timeline_id!r}")

    project = repos.get_project(ctx.db, timeline["project_id"])
    if project is None:
        raise ValueError(f"ai.voiceover: project not found: {timeline['project_id']!r}")

    seq_in = int(payload["seq_in_frame"])
    seq_out = int(payload["seq_out_frame_exclusive"])
    if seq_out <= seq_in:
        raise ValueError("ai.voiceover: seq_out_frame_exclusive must be greater than seq_in_frame")

    text = str(payload.get("text") or "").strip()
    segment_id = payload.get("segment_id")
    if segment_id:
        segment = repos.get_segment(ctx.db, str(segment_id))
        if segment is None:
            raise ValueError(f"ai.voiceover: segment not found: {segment_id!r}")
        source_asset = repos.get_asset(ctx.db, segment["asset_id"])
        if source_asset is None:
            raise ValueError(f"ai.voiceover: segment asset not found: {segment['asset_id']!r}")
        if source_asset["project_id"] != timeline["project_id"]:
            raise ValueError("ai.voiceover: segment does not belong to this timeline project")
        if not text:
            text = str(segment["text"]).strip()
    if not text:
        raise ValueError("ai.voiceover: text is required")

    duration_frames = seq_out - seq_in
    rate_num = int(project["sequence_rate_num"])
    rate_den = int(project["sequence_rate_den"])
    sample_rate = DEFAULT_VOICEOVER_SAMPLE_RATE

    backend_config = _backend_config_from_runtime(
        ctx.db,
        payload.get("runtime_id"),
        payload.get("backend"),
        "voice",
    )
    backend = resolve_voiceover_backend(
        backend_config.name,
        base_url=backend_config.base_url,
    )
    if not backend.available():
        raise RuntimeError(f"ai.voiceover: voiceover backend '{backend.name}' is not installed")

    workspace = Path(project["workspace_root"])
    synthetic_dir = workspace / "synthetic"
    synthetic_dir.mkdir(parents=True, exist_ok=True)
    out_path = synthetic_dir / f"{new_id()}.voiceover.wav"
    try:
        backend.synthesize(
            text=text,
            out_path=out_path,
            duration_frames=duration_frames,
            fps_num=rate_num,
            fps_den=rate_den,
            sample_rate=sample_rate,
            language=payload.get("language"),
            voice_id=payload.get("voice_id"),
        )
        assert_or_fix_media_sync(
            out_path,
            expected_frames=duration_frames,
            rate_num=rate_num,
            rate_den=rate_den,
            require_audio=True,
            fix=True,
        )
    except Exception:
        out_path.unlink(missing_ok=True)
        raise

    asset = repos.create_asset(
        ctx.db,
        project_id=timeline["project_id"],
        type="audio",
        display_name="Voiceover",
        source_path=str(out_path),
        synthetic=True,
        ai_effect="voiceover",
    )
    repos.add_asset_file(
        ctx.db,
        asset_id=asset["id"],
        kind="original",
        path=str(out_path),
        size_bytes=out_path.stat().st_size,
        is_proxy=False,
    )
    write_ai_provenance_manifest(
        media_path=out_path,
        asset_id=asset["id"],
        project_id=timeline["project_id"],
        ai_effect="voiceover",
        source={
            "timeline_id": timeline["id"],
            "segment_id": segment_id,
            "backend": backend.name,
            "voice_id": payload.get("voice_id"),
            "language": payload.get("language"),
            "seq_in_frame": seq_in,
            "seq_out_frame_exclusive": seq_out,
        },
    )
    repos.update_asset_probe(
        ctx.db,
        asset["id"],
        type="audio",
        duration_frames=duration_frames,
        rate_num=rate_num,
        rate_den=rate_den,
        audio_sample_rate=sample_rate,
        start_timecode=None,
        width=None,
        height=None,
        codec_video=None,
        codec_audio="pcm_s16le",
        is_vfr=False,
        sha256=None,
    )

    clip = repos.add_timeline_audio_clip(
        ctx.db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        seq_in_frame=seq_in,
        seq_out_frame_exclusive=seq_out,
        asset_in_frame=0,
        gain_percent=int(payload.get("gain_percent") or 100),
        fade_in_frames=int(payload.get("fade_in_frames") or 0),
        fade_out_frames=int(payload.get("fade_out_frames") or 0),
        mix_mode=str(payload.get("mix_mode") or "mix"),
        ducking_percent=(
            int(payload["ducking_percent"]) if payload.get("ducking_percent") is not None else 100
        ),
        label="Voiceover",
    )

    lipsync_job_id, lipsync_skip_reason = _maybe_enqueue_lipsync_after_vo(
        ctx,
        timeline=timeline,
        project=project,
        vo_asset_id=asset["id"],
        seq_in=seq_in,
        seq_out=seq_out,
    )
    return {
        "asset_id": asset["id"],
        "audio_clip_id": clip["id"],
        "out_path": str(out_path),
        "seq_in_frame": seq_in,
        "seq_out_frame_exclusive": seq_out,
        "lipsync_job_id": lipsync_job_id,
        "lipsync_skip_reason": lipsync_skip_reason,
    }


def _object_bool(obj: object, name: str) -> bool:
    return bool(getattr(obj, name))


def _object_float(obj: object, name: str) -> float:
    return float(getattr(obj, name))


def _probe_dict(probe: object) -> dict[str, object]:
    reason = getattr(probe, "reason", None)
    return {
        "face_detected": _object_bool(probe, "face_detected"),
        "mouth_visible": _object_bool(probe, "mouth_visible"),
        "audio_present": _object_bool(probe, "audio_present"),
        "reason": reason if isinstance(reason, str) else None,
    }


def _quality_dict(quality: object) -> dict[str, object]:
    return {
        "sync_score": _object_float(quality, "sync_score"),
        "mouth_score": _object_float(quality, "mouth_score"),
        "temporal_score": _object_float(quality, "temporal_score"),
        "passed": _object_bool(quality, "passed"),
    }


def _quality_passes(quality: object, threshold: float) -> bool:
    scores = [
        _object_float(quality, "sync_score"),
        _object_float(quality, "mouth_score"),
        _object_float(quality, "temporal_score"),
    ]
    return _object_bool(quality, "passed") and min(scores) >= threshold


def handle_lipsync(ctx: JobContext) -> dict[str, Any]:
    """Consent- and license-gated lipsync job.

    Gate order is safety-critical: license and consent are checked before any
    temp media render or sidecar call.
    """
    payload = ctx.payload
    if payload.get("license_accepted") is not True:
        raise ValueError("ai.lipsync: license_accepted must be true")

    consent_id = str(payload.get("consent_id") or "")
    if not consent_id:
        raise ValueError("ai.lipsync: payload missing required key 'consent_id'")
    consent = repos.get_consent_record(ctx.db, consent_id)
    if consent is None:
        raise ValueError(f"ai.lipsync: consent record not found: {consent_id!r}")
    if consent.get("revoked_at"):
        raise ValueError(f"ai.lipsync: consent {consent_id!r} has been revoked")

    timeline_id = str(payload["timeline_id"])
    timeline = repos.get_timeline(ctx.db, timeline_id)
    if timeline is None:
        raise ValueError(f"ai.lipsync: timeline not found: {timeline_id!r}")
    project = repos.get_project(ctx.db, timeline["project_id"])
    if project is None:
        raise ValueError(f"ai.lipsync: project not found: {timeline['project_id']!r}")
    if consent["project_id"] != timeline["project_id"]:
        raise ValueError("ai.lipsync: consent does not belong to this timeline project")

    audio_asset_id = str(payload["audio_asset_id"])
    audio_asset = repos.get_asset(ctx.db, audio_asset_id)
    if audio_asset is None:
        raise ValueError(f"ai.lipsync: audio asset not found: {audio_asset_id!r}")
    if audio_asset["project_id"] != timeline["project_id"]:
        raise ValueError("ai.lipsync: audio asset does not belong to this timeline project")
    if audio_asset.get("type") != "audio" and not audio_asset.get("codec_audio"):
        raise ValueError("ai.lipsync: selected asset has no audio stream")

    rate_num = int(project["sequence_rate_num"])
    rate_den = int(project["sequence_rate_den"])
    seq_in = int(payload["seq_in_frame"])
    seq_out = int(payload["seq_out_frame_exclusive"])
    if seq_out <= seq_in:
        raise ValueError("ai.lipsync: seq_out_frame_exclusive must be greater than seq_in_frame")
    duration_frames = seq_out - seq_in

    if timeline.get("kind") == "sequence":
        base_rows = flatten_sequence(ctx.db, timeline["id"])
    else:
        base_rows = [
            row
            for row in repos.list_timeline_clips(ctx.db, timeline["id"])
            if row.get("role", "base") != "replace"
        ]
    driving_clips: list[tuple[Path, int, int]] = []
    for row in base_rows:
        row_seq_in = int(row["seq_in_frame"])
        row_seq_out = int(row["seq_out_frame_exclusive"])
        overlap_in = max(row_seq_in, seq_in)
        overlap_out = min(row_seq_out, seq_out)
        if overlap_in >= overlap_out:
            continue
        asset = repos.get_asset(ctx.db, row["asset_id"])
        if asset is None:
            raise ValueError(f"ai.lipsync: asset not found: {row['asset_id']!r}")
        src_in = int(row["src_in_frame"]) + (overlap_in - row_seq_in)
        src_out = int(row["src_in_frame"]) + (overlap_out - row_seq_in)
        driving_clips.append((Path(asset["source_path"]), src_in, src_out))
    if not driving_clips:
        raise ValueError(f"ai.lipsync: no base clips overlap range [{seq_in}, {seq_out})")

    workspace = Path(project["workspace_root"])
    tmp_dir = workspace / "tmp"
    synthetic_dir = workspace / "synthetic"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    synthetic_dir.mkdir(parents=True, exist_ok=True)
    driving_tmp = tmp_dir / f"{new_id()}.lipsync-driving.mp4"
    out_path = synthetic_dir / f"{new_id()}.lipsync.mp4"
    probe_payload: dict[str, object]
    quality_payload: dict[str, object]

    try:
        render_clips_mp4(driving_clips, driving_tmp, rate_num=rate_num, rate_den=rate_den)
        backend_config = _backend_config_from_runtime(
            ctx.db,
            payload.get("runtime_id"),
            payload.get("backend"),
            "lipsync",
        )
        backend = resolve_lipsync_backend(
            backend_config.name,
            base_url=backend_config.base_url,
        )
        if not backend.available():
            raise RuntimeError(f"ai.lipsync: lipsync backend '{backend.name}' is not installed")

        probe = backend.probe(video_path=driving_tmp, audio_path=Path(audio_asset["source_path"]))
        probe_payload = _probe_dict(probe)
        if not (
            probe_payload["face_detected"]
            and probe_payload["mouth_visible"]
            and probe_payload["audio_present"]
        ):
            reason = probe_payload["reason"] or "face/mouth/audio probe failed"
            raise ValueError(f"ai.lipsync: {reason}")

        quality = backend.lipsync(
            video_path=driving_tmp,
            audio_path=Path(audio_asset["source_path"]),
            out_path=out_path,
            fps_num=rate_num,
            fps_den=rate_den,
        )
        assert_or_fix_media_sync(
            out_path,
            expected_frames=duration_frames,
            rate_num=rate_num,
            rate_den=rate_den,
            require_video=True,
            require_audio=True,
            fix=True,
        )
        quality_payload = _quality_dict(quality)
        threshold = float(payload.get("quality_threshold", 0.6))
        if not _quality_passes(quality, threshold):
            raise ValueError(
                f"ai.lipsync: quality gate failed below threshold {threshold:.2f}"
            )
    except Exception:
        out_path.unlink(missing_ok=True)
        raise
    finally:
        driving_tmp.unlink(missing_ok=True)

    asset = repos.create_asset(
        ctx.db,
        project_id=timeline["project_id"],
        type="video",
        display_name=f"lipsync {seq_in}-{seq_out}",
        source_path=str(out_path),
        synthetic=True,
        ai_effect="lipsync",
    )
    write_ai_provenance_manifest(
        media_path=out_path,
        asset_id=asset["id"],
        project_id=timeline["project_id"],
        ai_effect="lipsync",
        source={
            "timeline_id": timeline["id"],
            "audio_asset_id": audio_asset_id,
            "consent_id": consent_id,
            "backend": payload.get("backend") or "stub",
            "quality_threshold": payload.get("quality_threshold", 0.6),
            "seq_in_frame": seq_in,
            "seq_out_frame_exclusive": seq_out,
            "probe": probe_payload,
            "quality": quality_payload,
        },
    )
    repos.add_timeline_clip(
        ctx.db,
        timeline_id=timeline["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=duration_frames,
        seq_in_frame=seq_in,
        seq_out_frame_exclusive=seq_out,
        lane=1,
        role="replace",
    )
    return {
        "asset_id": asset["id"],
        "audio_asset_id": audio_asset_id,
        "consent_id": consent_id,
        "out_path": str(out_path),
        "seq_in_frame": seq_in,
        "seq_out_frame_exclusive": seq_out,
        "probe": probe_payload,
        "quality": quality_payload,
    }
