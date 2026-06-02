"""Job handlers that wire the ingest pipeline together.

Chain (docs/05-workers-queue.md):
    ingest.probe ──> proxy.build
                 └─> audio.extract ──> waveform.build
Each handler is idempotent and re-enqueues its successors with stable idempotency
keys so retries never duplicate work.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ..db import repos
from ..db.database import Database
from ..jobs.runner import JobContext, JobHandler, enqueue
from .audio import extract_mix48k, extract_mono16k
from .probe import probe_asset, sha256_file
from .proxy import build_poster, build_proxy
from .waveform import compute_waveform


def _project_root(db: Database, asset: dict[str, Any]) -> Path:
    project = repos.get_project(db, asset["project_id"])
    assert project is not None
    return Path(project["workspace_root"])


def _require_asset(ctx: JobContext) -> dict[str, Any]:
    asset_id = ctx.payload["asset_id"]
    asset = repos.get_asset(ctx.db, asset_id)
    if asset is None:
        raise ValueError(f"asset not found: {asset_id}")
    return asset


def handle_probe(ctx: JobContext) -> dict[str, Any]:
    asset = _require_asset(ctx)
    src = asset["source_path"]
    if not os.path.exists(src):
        raise FileNotFoundError(f"source not found: {src}")

    pr = probe_asset(src)
    sha = sha256_file(src)
    repos.update_asset_probe(
        ctx.db,
        asset["id"],
        type=pr.type,
        duration_frames=pr.duration_frames,
        rate_num=pr.rate_num,
        rate_den=pr.rate_den,
        audio_sample_rate=pr.audio_sample_rate,
        start_timecode=pr.start_timecode,
        width=pr.width,
        height=pr.height,
        codec_video=pr.codec_video,
        codec_audio=pr.codec_audio,
        is_vfr=pr.is_vfr,
        sha256=sha,
    )
    repos.add_asset_file(
        ctx.db, asset_id=asset["id"], kind="original", path=src,
        size_bytes=os.path.getsize(src), checksum=sha,
    )

    root = _project_root(ctx.db, asset)
    if pr.type == "video" and pr.height:
        poster = root / "analysis" / asset["id"] / "thumbnails" / "poster.jpg"
        build_poster(src, poster)
        repos.add_asset_file(ctx.db, asset_id=asset["id"], kind="poster", path=str(poster),
                             size_bytes=os.path.getsize(poster))
        enqueue(ctx.db, queue="proxy.cpu", kind="proxy.build",
                payload={"asset_id": asset["id"]}, idempotency_key=f"proxy:{asset['id']}",
                caused_by_job_id=ctx.job_id)
    if pr.audio_sample_rate:
        enqueue(ctx.db, queue="proxy.cpu", kind="audio.extract",
                payload={"asset_id": asset["id"]}, idempotency_key=f"audio:{asset['id']}",
                caused_by_job_id=ctx.job_id)

    return {"asset_id": asset["id"], "type": pr.type, "duration_frames": pr.duration_frames,
            "is_vfr": pr.is_vfr}


def handle_proxy(ctx: JobContext) -> dict[str, Any]:
    asset = _require_asset(ctx)
    if asset["type"] != "video" or not asset["height"]:
        return {"skipped": "no video stream"}
    root = _project_root(ctx.db, asset)
    dest = root / "proxies" / asset["id"] / "proxy.mp4"
    build_proxy(
        asset["source_path"], dest,
        src_height=int(asset["height"]),
        rate_num=asset["rate_num"], rate_den=asset["rate_den"],
    )
    repos.add_asset_file(ctx.db, asset_id=asset["id"], kind="proxy", path=str(dest),
                         size_bytes=os.path.getsize(dest), is_proxy=True)
    return {"proxy": str(dest)}


def handle_audio(ctx: JobContext) -> dict[str, Any]:
    asset = _require_asset(ctx)
    if not asset["audio_sample_rate"]:
        return {"skipped": "no audio stream"}
    root = _project_root(ctx.db, asset)
    mono = root / "audio" / asset["id"] / "mono-16k.wav"
    mix = root / "audio" / asset["id"] / "mix-48k.wav"
    extract_mono16k(asset["source_path"], mono)
    extract_mix48k(asset["source_path"], mix)
    repos.add_asset_file(ctx.db, asset_id=asset["id"], kind="audio_mono16k", path=str(mono),
                         size_bytes=os.path.getsize(mono), is_audio_extract=True)
    repos.add_asset_file(ctx.db, asset_id=asset["id"], kind="audio_mix48k", path=str(mix),
                         size_bytes=os.path.getsize(mix), is_audio_extract=True)
    enqueue(ctx.db, queue="proxy.cpu", kind="waveform.build",
            payload={"asset_id": asset["id"]}, idempotency_key=f"waveform:{asset['id']}",
            caused_by_job_id=ctx.job_id)
    return {"mono": str(mono), "mix": str(mix)}


def handle_waveform(ctx: JobContext) -> dict[str, Any]:
    asset = _require_asset(ctx)
    root = _project_root(ctx.db, asset)
    mono = root / "audio" / asset["id"] / "mono-16k.wav"
    if not mono.exists():
        raise FileNotFoundError(f"mono audio missing for waveform: {mono}")
    dest = root / "waveforms" / asset["id"] / "waveform.json"
    payload = compute_waveform(mono, dest)
    repos.add_asset_file(ctx.db, asset_id=asset["id"], kind="waveform", path=str(dest),
                         size_bytes=os.path.getsize(dest), is_waveform=True)
    return {"waveform": str(dest), "length": payload["length"]}


def register_ingest_handlers(registry: dict[str, JobHandler]) -> None:
    registry["ingest.probe"] = handle_probe
    registry["proxy.build"] = handle_proxy
    registry["audio.extract"] = handle_audio
    registry["waveform.build"] = handle_waveform
