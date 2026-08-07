"""Analysis orchestrator job (`analysis.run`).

Runs each stage best-effort and records per-stage status in the run diagnostics, so a
missing ML extra degrades gracefully instead of failing the whole analysis. Shot
detection (light) typically runs; ASR/diarization run only when their extras are present.
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
from pathlib import Path
from typing import Any

from .. import PIPELINE_VERSION
from ..db import repos
from ..db.database import Database
from ..ingest.proxy import build_thumbnail
from ..jobs.runner import JobContext, JobHandler
from ..policy import (
    Policy,
    ResolvedPolicy,
    get_asset_policy,
    policy_to_str,
    resolve_policy,
    set_asset_policy,
)
from ..semantic import get_index
from ..util import utcnow_iso
from .align import align_words, whisperx_available
from .diarize import assign_speakers, diarize, pyannote_available
from .manifest import write_manifest
from .mapping import map_segment
from .quality import batch_shot_metrics, compute_shot_metrics, decide_keep, mark_duplicates
from .semantic_sync import segment_index_item
from .shots import detect_shots, detect_shots_hybrid, scenedetect_available
from .sidecar import asr_available, transcribe
from .transition_review import default_backend, run_transition_review
from .types import SegmentResult, ShotResult, WordResult

_log = logging.getLogger(__name__)


def _auto_rough_cut_enabled() -> bool:
    """Whether to auto-build a rough cut + scenes after a successful analysis run.

    Mirrors the ``LAURA_AUTO_ANALYZE`` gate in ``ingest/handlers.py``: enabled by default,
    opt out with ``LAURA_AUTO_ROUGH_CUT=0`` (also ``false``/``no``/``off``)."""
    return os.environ.get("LAURA_AUTO_ROUGH_CUT", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _resolve_and_persist_policy(db: Database, asset: dict[str, Any]) -> Policy:
    """Resolve the per-input policy for *asset* and persist it to ``asset_policies``.

    Precedence (P4-T1 seam, v1):
    - ``LAURA_DEFAULT_POLICY`` env var, if set and non-empty.
    - Otherwise falls back to the existing boolean gate:
      ``"auto"`` when :func:`_auto_rough_cut_enabled` is ``True``, else ``"human"``.

    On any ``resolve_policy``/persist error, logs a warning and returns
    ``Policy("auto")`` so a bad env value never fails the analysis run.
    """
    env_policy_str: str | None = os.environ.get("LAURA_DEFAULT_POLICY") or None
    if not env_policy_str:
        # Derive from the legacy boolean gate for backward-compat.
        env_policy_str = "auto" if _auto_rough_cut_enabled() else "human"

    try:
        rp: ResolvedPolicy = resolve_policy(
            row=None, pattern=None, env=env_policy_str, default="auto"
        )
        set_asset_policy(db, asset["id"], policy=policy_to_str(rp.policy), source=rp.source)
        return rp.policy
    except Exception as exc:  # noqa: BLE001
        _log.warning(
            "policy resolve/persist failed for asset %s (%s: %s); falling back to auto",
            asset.get("id"),
            type(exc).__name__,
            exc,
        )
        return Policy(mode="auto")


def _scene_detect_height() -> int:
    """Target height for the downscaled scene-detection proxy (env override).

    Default 720 is conservative: sources at/under 720p (the common case) are detected
    at native resolution — identical boundaries. Only larger sources (1080p/4K, which
    decode slowly enough to crawl or wedge OpenCV) are downscaled to 720p for detection;
    their boundaries then differ slightly from native, which is far better than a wedge.
    The full-res proxy/thumbnails/cuts are unaffected. Tune via LAURA_SCENE_DETECT_HEIGHT
    (set very high to force native-resolution detection)."""
    try:
        return max(120, int(os.environ.get("LAURA_SCENE_DETECT_HEIGHT", "720")))
    except ValueError:
        return 720


def _build_detection_proxy(src: str, dest: Path, *, height: int) -> bool:
    """Downscale ``src`` to ``height``px (even, keep aspect) at the SAME frame rate.

    Same frame count -> shot-boundary frame indices are unchanged (1:1); only pixels
    shrink, so detection decodes far faster. Returns True on success, False on any
    ffmpeg failure.
    """
    from ..ingest.ffmpeg import FFmpegError, run_ffmpeg

    try:
        run_ffmpeg([
            "-i", str(src),
            "-vf", f"scale=-2:{height}",
            "-fps_mode", "passthrough",  # never drop/dup frames -> exact frame parity
            "-an",  # detection needs no audio
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "28",
            "-pix_fmt", "yuv420p",
            str(dest),
        ])
    except FFmpegError:
        return False
    return dest.exists() and dest.stat().st_size > 0


def _resolve_detect_video(
    db: Database, asset: dict[str, Any], video: str
) -> tuple[str, Path | None]:
    """Return ``(path_for_detection, temp_to_clean_up_or_None)``.

    Builds a small same-fps detection proxy when the source is taller than the target;
    otherwise uses ``video`` unchanged. Any failure -> falls back to ``video``.
    """
    target_h = _scene_detect_height()
    src_h = int(asset.get("height") or 0)
    if not src_h or src_h <= target_h:
        return video, None
    project = repos.get_project(db, asset["project_id"])
    assert project is not None
    tmp = Path(project["workspace_root"]) / "analysis" / asset["id"] / "scene-detect.mp4"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    if _build_detection_proxy(video, tmp, height=target_h):
        return str(tmp), tmp
    return video, None


def _run_scene(
    db: Database,
    asset: dict[str, Any],
    run_id: str,
    files: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    if not scenedetect_available():
        return {"status": "skipped", "reason": "scene extra not installed"}
    video = files["proxy"]["path"] if "proxy" in files else asset["source_path"]
    # Detect on a small same-fps proxy (frame indices unchanged, ~9x faster decode);
    # thumbnails/metrics below still use the full ``video``.
    detect_video, detect_tmp = _resolve_detect_video(db, asset, video)

    desired = config.get("detector", "adaptive")
    detector = desired
    notes: dict[str, Any] = {}
    try:
        if desired == "hybrid":
            # Hybrid orchestrates both engines itself and degrades to adaptive internally,
            # so it never raises just because TransNetV2 is missing. Surface its diagnostics.
            try:
                shots, hybrid_diag = detect_shots_hybrid(detect_video)
            except Exception as exc:  # noqa: BLE001 - adaptive path failed -> stage fails
                return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            notes.update(hybrid_diag)
        else:
            try:
                shots = detect_shots(detect_video, detector=desired)
            except (ImportError, RuntimeError) as exc:
                # TransNetV2 (extra ``scene-ml``) absent or its inference failed: never fail
                # the run — fall back to the always-present PySceneDetect ``adaptive``.
                if desired != "adaptive":
                    notes[desired] = f"skipped: {type(exc).__name__}: {exc}"
                    detector = "adaptive"
                    try:
                        shots = detect_shots(detect_video, detector="adaptive")
                    except Exception as exc2:  # noqa: BLE001
                        return {"status": "failed", "error": f"{type(exc2).__name__}: {exc2}"}
                else:
                    return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            except Exception as exc:  # noqa: BLE001
                return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
        if not shots and asset["duration_frames"]:
            shots = [ShotResult(0, int(asset["duration_frames"]), method="whole")]
    finally:
        if detect_tmp is not None:
            detect_tmp.unlink(missing_ok=True)

    project = repos.get_project(db, asset["project_id"])
    assert project is not None
    thumb_dir = Path(project["workspace_root"]) / "analysis" / asset["id"] / "thumbnails"
    rate_num = asset["rate_num"] or 25
    rate_den = asset["rate_den"] or 1
    # Quality metrics for every shot in ONE decode pass (O(N) vs the per-shot O(N²) that made
    # long-video analysis crawl). Metric-identical to per-shot; falls back to per-shot if the
    # batch decode fails, so a hiccup never loses metrics.
    try:
        batch_metrics = batch_shot_metrics(
            video, [(s.src_in_frame, s.src_out_frame_exclusive) for s in shots]
        )
    except Exception:  # noqa: BLE001 - fall back to per-shot below
        batch_metrics = None
    rows: list[dict[str, Any]] = []
    for i, s in enumerate(shots):
        thumbnail: str | None = None
        dest = thumb_dir / f"shot-{i:04d}.jpg"
        try:
            build_thumbnail(video, dest, at_seconds=s.src_in_frame * rate_den / rate_num)
            thumbnail = str(dest)
        except Exception:  # noqa: BLE001 - thumbnails are best-effort
            thumbnail = None
        keep, reason, metrics = True, None, None
        try:
            metrics = (
                batch_metrics[i]
                if batch_metrics is not None
                else compute_shot_metrics(video, s.src_in_frame, s.src_out_frame_exclusive)
            )
            keep, reason = decide_keep(
                metrics, length_frames=s.src_out_frame_exclusive - s.src_in_frame
            )
        except Exception:  # noqa: BLE001 - quality metrics are best-effort
            metrics = None
        rows.append({
            "src_in_frame": s.src_in_frame,
            "src_out_frame_exclusive": s.src_out_frame_exclusive,
            "method": s.method,
            "confidence": s.confidence,
            "thumbnail_path": thumbnail,
            "black_ratio": metrics.black_ratio if metrics else None,
            "static_score": metrics.static if metrics else None,
            "phash": metrics.phash if metrics else None,
            "blur_score": metrics.blur if metrics else None,
            "keep": keep,
            "drop_reason": reason,
        })
    mark_duplicates(rows)

    # Guard: never drop 100 % of an asset's shots as "black" — that would produce an empty
    # rough cut. When every shot is flagged black (e.g. dark-stage poetry-slam footage with
    # a spotlight), keep them all and log a warning; the black-filter is being over-eager.
    if rows and all(
        not r.get("keep") and r.get("drop_reason") == "black" for r in rows
    ):
        _log.warning(
            "asset %s: all %d shots flagged black; keeping anyway — likely dark footage",
            asset["id"],
            len(rows),
        )
        for r in rows:
            r["keep"] = True
            r["drop_reason"] = None

    repos.insert_shots(db, asset_id=asset["id"], run_id=run_id, shots=rows)
    result: dict[str, Any] = {"status": "ok", "count": len(rows), "detector": detector}
    result.update(notes)  # e.g. {"transnet": "skipped: ImportError: ..."} on fallback
    return result


def _run_transcript(
    db: Database,
    asset: dict[str, Any],
    project: dict[str, Any],
    run_id: str,
    files: dict[str, dict[str, Any]],
    config: dict[str, Any],
) -> dict[str, Any]:
    if "audio_mono16k" not in files:
        return {"status": "skipped", "reason": "no audio extracted"}
    if not asr_available():
        return {"status": "skipped", "reason": "asr unavailable (no sidecar, no local extra)"}

    mono_path = files["audio_mono16k"]["path"]
    try:
        segments = transcribe(
            mono_path, model_size=config.get("model", "base"), language=config.get("language")
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

    align_status = "skipped"
    if config.get("stages", {}).get("align", False) and whisperx_available():
        try:
            segments = align_words(mono_path, segments, language=config.get("language") or "en")
            align_status = f"ok ({sum(len(s.words) for s in segments)} words)"
        except Exception as exc:  # noqa: BLE001
            align_status = f"failed: {type(exc).__name__}: {exc}"

    diar_status = "skipped"
    if config.get("stages", {}).get("diarize", False) and pyannote_available():
        try:
            turns = diarize(mono_path)
            assign_speakers(segments, turns)
            diar_status = f"ok ({len(turns)} turns)"
        except Exception as exc:  # noqa: BLE001
            diar_status = f"failed: {exc}"

    audio_rate = int(asset["audio_sample_rate"])
    rate_num = asset["rate_num"] or project["sequence_rate_num"]
    rate_den = asset["rate_den"] or project["sequence_rate_den"]

    label_to_id: dict[str, str] = {}
    index_items: list[dict[str, Any]] = []
    for seg in segments:
        speaker_id: str | None = None
        if seg.speaker_label:
            if seg.speaker_label not in label_to_id:
                label_to_id[seg.speaker_label] = repos.insert_speaker(
                    db, asset_id=asset["id"], run_id=run_id, label=seg.speaker_label
                )
            speaker_id = label_to_id[seg.speaker_label]
        seg_row, word_rows = map_segment(seg, audio_rate, rate_num, rate_den)
        seg_id = repos.insert_segment_with_words(
            db, asset_id=asset["id"], run_id=run_id, speaker_id=speaker_id,
            segment=seg_row, words=word_rows,
        )
        seg_row["id"] = seg_id
        index_items.append(segment_index_item(asset, seg_row, seg.speaker_label))

    embedded = 0
    embed_status: str | None = None
    if index_items:
        try:
            # get_index() itself raises when Qdrant is unreachable (the client/collection
            # construction talks to the server). Semantic indexing is best-effort -- it must
            # never take the analysis run with it. This is fd0914b's fix on the write side.
            index = get_index()
            if index is not None:
                index.delete_asset(asset["id"])
                embedded = index.index(index_items)
        except Exception as exc:  # noqa: BLE001 - semantic indexing is best-effort
            _log.warning("asset %s: semantic embed failed (best-effort): %s", asset["id"], exc)
            embed_status = f"failed: {type(exc).__name__}: {exc}"
    result: dict[str, Any] = {
        "status": "ok", "segments": len(segments), "diarization": diar_status,
        "alignment": align_status, "embedded": embedded,
    }
    if embed_status is not None:
        result["embed"] = embed_status
    return result


def handle_analysis_run(ctx: JobContext) -> dict[str, Any]:
    """Run the analysis stages, and leave the run in a TERMINAL state either way.

    The jobs table gets a reaper (jobs/runner.py); analysis_runs never had one, and
    finish_analysis_run was reachable only on the happy path. A handler that raised therefore
    left the row in 'running' forever -- with its segments already committed and
    diagnostics_json still '{}'. workspace-livetest holds three such rows. The exception is
    re-raised untouched so the job's own failure handling and retry budget are unchanged.
    """
    run_id = str(ctx.payload["analysis_run_id"])
    diagnostics: dict[str, Any] = {}
    try:
        return _analysis_run_stages(ctx, diagnostics)
    except Exception as exc:  # noqa: BLE001 - finalize the run, then re-raise untouched
        diagnostics["error"] = f"{type(exc).__name__}: {exc}"
        repos.finish_analysis_run(
            ctx.db, run_id, status="failed", diagnostics=diagnostics
        )
        raise


def _analysis_run_stages(ctx: JobContext, diagnostics: dict[str, Any]) -> dict[str, Any]:
    asset_id = ctx.payload["asset_id"]
    run_id = ctx.payload["analysis_run_id"]
    config: dict[str, Any] = ctx.payload.get("config", {})
    stages_cfg: dict[str, Any] = config.get("stages", {})

    asset = repos.get_asset(ctx.db, asset_id)
    if asset is None:
        raise ValueError(f"asset not found: {asset_id}")
    project = repos.get_project(ctx.db, asset["project_id"])
    assert project is not None
    files = {f["kind"]: f for f in repos.list_asset_files(ctx.db, asset_id)}

    started = utcnow_iso()
    repos.start_analysis_run(ctx.db, run_id)
    repos.clear_analysis_results(ctx.db, asset_id=asset_id, run_id=run_id)

    if stages_cfg.get("scene", True):
        diagnostics["scene"] = _run_scene(ctx.db, asset, run_id, files, config)
    if stages_cfg.get("asr", True):
        diagnostics["asr"] = _run_transcript(ctx.db, asset, project, run_id, files, config)

    # Smart handling: land the asset edit-ready (rough cut + scenes) with zero clicks.
    # Best-effort and gated — a failure here must NEVER fail the analysis run. Only fires
    # when the scene stage produced shots; otherwise there is nothing to build from.
    # Policy seam (P4-T2): resolve per-input policy, persist it, decide auto-build by mode.
    policy = _resolve_and_persist_policy(ctx.db, asset)
    if policy.mode in ("auto", "threshold") and diagnostics.get("scene", {}).get("status") == "ok":
        try:
            from ..scenes.build import autobuild_asset_edit_ready

            n = autobuild_asset_edit_ready(
                ctx.db, project_id=asset["project_id"], asset_id=asset_id, run_id=run_id
            )
            persisted = get_asset_policy(ctx.db, asset_id)
            diagnostics["auto_rough_cut"] = {
                "status": "ok",
                "scenes": n,
                "policy": policy.mode,
                "policy_source": persisted["policy_source"] if persisted else "default",
            }
        except Exception as exc:  # noqa: BLE001 - auto-build must never fail the run
            diagnostics["auto_rough_cut"] = {
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
    elif policy.mode == "human":
        diagnostics["auto_rough_cut"] = {"status": "skipped", "reason": "policy=human"}

    manifest_dest = Path(project["workspace_root"]) / "analysis" / asset_id / "manifest.json"
    write_manifest(
        manifest_dest, pipeline_version=PIPELINE_VERSION, asset_id=asset_id,
        stages=diagnostics, started_at=started, finished_at=utcnow_iso(),
    )
    repos.finish_analysis_run(ctx.db, run_id, status="succeeded", diagnostics=diagnostics)
    return diagnostics


def _module_available(name: str) -> bool:
    return importlib.util.find_spec(name) is not None


def handle_analysis_align(ctx: JobContext) -> dict[str, Any]:
    """Forced-alignment stage (WhisperX) — routed to the GPU queue. Optional extra;
    graceful-skip when the extra/GPU is absent so the pipeline degrades instead of
    failing. The real implementation is a GPU-only later portion."""
    if not _module_available("whisperx"):
        return {"status": "skipped", "reason": "align extra (whisperx) not installed"}
    return {"status": "skipped", "reason": f"{ctx.kind} not yet implemented (GPU stage)"}


def handle_analysis_embed(ctx: JobContext) -> dict[str, Any]:
    """Semantic-embedding stage feeding Qdrant — routed to the GPU queue. Optional
    extra; graceful-skip when absent."""
    if not _module_available("qdrant_client"):
        return {"status": "skipped", "reason": "embed extra (qdrant_client) not installed"}
    return {"status": "skipped", "reason": f"{ctx.kind} not yet implemented (GPU stage)"}


def _segment_result_from_row(
    segment: dict[str, Any], words: list[dict[str, Any]], audio_sample_rate: int
) -> SegmentResult:
    return SegmentResult(
        text=str(segment["text"]),
        start_sec=int(segment["start_sample"]) / audio_sample_rate,
        end_sec=int(segment["end_sample"]) / audio_sample_rate,
        confidence=segment.get("confidence"),
        words=[
            WordResult(
                text=str(word["text"]),
                start_sec=int(word["start_sample"]) / audio_sample_rate,
                end_sec=int(word["end_sample"]) / audio_sample_rate,
                confidence=word.get("confidence"),
                is_punctuation=bool(word.get("is_punctuation", False)),
            )
            for word in words
        ],
        speaker_label=segment.get("speaker_label"),
    )


def _realign_segments(
    db: Database, asset_id: str, requested_ids: Any
) -> list[dict[str, Any]]:
    if isinstance(requested_ids, list) and requested_ids:
        segments: list[dict[str, Any]] = []
        for segment_id in requested_ids:
            seg = repos.get_segment(db, str(segment_id))
            if seg is None:
                raise ValueError(f"segment not found: {segment_id}")
            if seg["asset_id"] != asset_id:
                raise ValueError(f"segment does not belong to asset: {segment_id}")
            segments.append(seg)
        return segments

    run = repos.get_latest_transcript_run(db, asset_id)
    if run is None:
        raise RuntimeError("no analysis run for asset")
    return repos.get_transcript(db, asset_id, run["id"])


def handle_transcript_realign(ctx: JobContext) -> dict[str, Any]:
    asset_id = str(ctx.payload["asset_id"])
    language = str(ctx.payload.get("language") or "en")
    requested_ids = ctx.payload.get("segment_ids")

    asset = repos.get_asset(ctx.db, asset_id)
    if asset is None:
        raise ValueError(f"asset not found: {asset_id}")

    segments = _realign_segments(ctx.db, asset_id, requested_ids)
    segment_ids = [str(seg["id"]) for seg in segments]

    try:
        audio_sample_rate = asset.get("audio_sample_rate")
        if audio_sample_rate is None:
            raise RuntimeError("asset has no audio sample rate; cannot realign transcript")
        files = {f["kind"]: f for f in repos.list_asset_files(ctx.db, asset_id)}
        if "audio_mono16k" not in files:
            raise RuntimeError("no audio_mono16k extracted; cannot realign transcript")
        if not whisperx_available():
            raise RuntimeError("align extra (whisperx) not installed")

        to_align = [
            _segment_result_from_row(
                seg,
                repos.get_segment_words(ctx.db, seg["id"]),
                int(audio_sample_rate),
            )
            for seg in segments
        ]
        aligned = align_words(Path(files["audio_mono16k"]["path"]), to_align, language=language)
        if len(aligned) != len(segments):
            raise RuntimeError("alignment changed segment count")

        rate_num = asset["rate_num"] or 25
        rate_den = asset["rate_den"] or 1
        for seg, aligned_seg in zip(segments, aligned, strict=True):
            seg_row, word_rows = map_segment(
                aligned_seg, int(audio_sample_rate), rate_num, rate_den
            )
            repos.replace_segment_words(ctx.db, seg["id"], segment=seg_row, words=word_rows)
    except Exception as exc:
        repos.mark_segments_alignment(
            ctx.db,
            segment_ids,
            status="failed",
            job_id=ctx.job_id,
            language=language,
            error=str(exc),
        )
        raise

    repos.mark_segments_alignment(
        ctx.db,
        segment_ids,
        status="aligned",
        job_id=ctx.job_id,
        language=language,
        error=None,
    )
    return {"status": "ok", "segments": len(segments)}


def handle_transition_review(ctx: JobContext) -> dict[str, Any]:
    """Review a timeline's cut transitions with the configured VLM backend (on-demand).

    No-op (status ``skipped``) when no backend is installed (the ``[vlm]`` extra is absent), so the
    job never fails just because the optional model is missing. Progress persisted per boundary."""
    timeline_id = str(ctx.payload["timeline_id"])
    backend = default_backend()
    if backend is None:
        return {"status": "skipped", "reason": "no vlm backend"}

    def _progress(done: int, total: int) -> None:
        repos.set_job_progress(ctx.db, ctx.job_id, json.dumps({"reviewed": done, "total": total}))
        ctx.heartbeat()

    result = run_transition_review(ctx.db, timeline_id, backend=backend, progress=_progress)
    return {"status": "ok", **result}


def register_analysis_handlers(registry: dict[str, JobHandler]) -> None:
    registry["analysis.run"] = handle_analysis_run
    registry["analysis.align"] = handle_analysis_align
    registry["analysis.embed"] = handle_analysis_embed
    registry["transcript.realign"] = handle_transcript_realign
    registry["transition.review"] = handle_transition_review
