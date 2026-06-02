"""Analysis orchestrator job (`analysis.run`).

Runs each stage best-effort and records per-stage status in the run diagnostics, so a
missing ML extra degrades gracefully instead of failing the whole analysis. Shot
detection (light) typically runs; ASR/diarization run only when their extras are present.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .. import PIPELINE_VERSION
from ..db import repos
from ..db.database import Database
from ..ingest.proxy import build_thumbnail
from ..jobs.runner import JobContext, JobHandler
from ..util import utcnow_iso
from .asr import faster_whisper_available, transcribe
from .diarize import assign_speakers, diarize, pyannote_available
from .manifest import write_manifest
from .mapping import map_segment
from .shots import detect_shots, scenedetect_available
from .types import ShotResult


def _run_scene(
    db: Database, asset: dict[str, Any], run_id: str, files: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    if not scenedetect_available():
        return {"status": "skipped", "reason": "scene extra not installed"}
    video = files["proxy"]["path"] if "proxy" in files else asset["source_path"]
    try:
        shots = detect_shots(video)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
    if not shots and asset["duration_frames"]:
        shots = [ShotResult(0, int(asset["duration_frames"]), method="whole")]

    project = repos.get_project(db, asset["project_id"])
    assert project is not None
    thumb_dir = Path(project["workspace_root"]) / "analysis" / asset["id"] / "thumbnails"
    rate_num = asset["rate_num"] or 25
    rate_den = asset["rate_den"] or 1
    rows: list[dict[str, Any]] = []
    for i, s in enumerate(shots):
        thumbnail: str | None = None
        dest = thumb_dir / f"shot-{i:04d}.jpg"
        try:
            build_thumbnail(video, dest, at_seconds=s.src_in_frame * rate_den / rate_num)
            thumbnail = str(dest)
        except Exception:  # noqa: BLE001 - thumbnails are best-effort
            thumbnail = None
        rows.append({
            "src_in_frame": s.src_in_frame,
            "src_out_frame_exclusive": s.src_out_frame_exclusive,
            "method": s.method,
            "confidence": s.confidence,
            "thumbnail_path": thumbnail,
        })
    repos.insert_shots(db, asset_id=asset["id"], run_id=run_id, shots=rows)
    return {"status": "ok", "count": len(rows), "method": "pyscenedetect"}


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
    if not faster_whisper_available():
        return {"status": "skipped", "reason": "asr extra not installed"}

    mono_path = files["audio_mono16k"]["path"]
    try:
        segments = transcribe(
            mono_path, model_size=config.get("model", "base"), language=config.get("language")
        )
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}

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
    for seg in segments:
        speaker_id: str | None = None
        if seg.speaker_label:
            if seg.speaker_label not in label_to_id:
                label_to_id[seg.speaker_label] = repos.insert_speaker(
                    db, asset_id=asset["id"], run_id=run_id, label=seg.speaker_label
                )
            speaker_id = label_to_id[seg.speaker_label]
        seg_row, word_rows = map_segment(seg, audio_rate, rate_num, rate_den)
        repos.insert_segment_with_words(
            db, asset_id=asset["id"], run_id=run_id, speaker_id=speaker_id,
            segment=seg_row, words=word_rows,
        )
    return {"status": "ok", "segments": len(segments), "diarization": diar_status}


def handle_analysis_run(ctx: JobContext) -> dict[str, Any]:
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

    diagnostics: dict[str, Any] = {}
    if stages_cfg.get("scene", True):
        diagnostics["scene"] = _run_scene(ctx.db, asset, run_id, files)
    if stages_cfg.get("asr", True):
        diagnostics["asr"] = _run_transcript(ctx.db, asset, project, run_id, files, config)

    manifest_dest = Path(project["workspace_root"]) / "analysis" / asset_id / "manifest.json"
    write_manifest(
        manifest_dest, pipeline_version=PIPELINE_VERSION, asset_id=asset_id,
        stages=diagnostics, started_at=started, finished_at=utcnow_iso(),
    )
    repos.finish_analysis_run(ctx.db, run_id, status="succeeded", diagnostics=diagnostics)
    return diagnostics


def register_analysis_handlers(registry: dict[str, JobHandler]) -> None:
    registry["analysis.run"] = handle_analysis_run
