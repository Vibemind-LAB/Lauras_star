"""Job handlers that wire the ingest pipeline together.

Chain (docs/05-workers-queue.md):
    ingest.probe ──> proxy.build
                 └─> audio.extract ──> waveform.build
Each handler is idempotent and re-enqueues its successors with stable idempotency
keys so retries never duplicate work.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from .. import PIPELINE_VERSION
from ..db import repos
from ..db.database import Database
from ..jobs.queues import queue_for
from ..jobs.runner import JobContext, JobHandler, enqueue
from .aria2 import Aria2Cancelled, aria2_available, aria2_download
from .audio import extract_mix48k, extract_mono16k
from .download import download_resumable
from .engine import select_engine
from .integrity import is_media_file, verify_decode
from .probe import probe_asset, sha256_file
from .proxy import build_poster, build_proxy
from .waveform import compute_waveform
from .ytdlp import download_via_ytdlp, needs_ytdlp, ytdlp_available


class ImportCancelled(RuntimeError):
    """Raised inside ``handle_fetch`` when the import's cancel flag is set.

    Caught by the handler itself: the partial download is removed and the job ends
    normally (no retry). ``import-status`` reports the cancelled phase via the
    persisted ``cancel_requested`` flag, not via the job's terminal status."""


def _cleanup_partial_download(base_dir: Path) -> None:
    """Remove any partially-downloaded files for a cancelled/aborted fetch.

    Best-effort: the whole per-asset download directory holds only the in-flight
    file plus its ``.part``/``.parts`` scratch, so dropping it leaves no orphan bytes
    behind. Never raises — cleanup failures must not mask the cancellation."""
    shutil.rmtree(base_dir, ignore_errors=True)


class _ProgressWriter:
    """Throttled persistence of download progress into the job's progress_json."""

    def __init__(self, db: Any, job_id: str, *, min_interval: float = 1.0) -> None:
        self._db = db
        self._job_id = job_id
        self._min_interval = min_interval
        self._last_t = 0.0
        self._last_bytes = 0
        self._started = False

    def __call__(self, downloaded: int, total: int | None) -> None:
        now = time.monotonic()
        if self._started and now - self._last_t < self._min_interval:
            return
        speed = 0.0
        if self._started and now > self._last_t:
            speed = (downloaded - self._last_bytes) / (now - self._last_t)
        self._started = True
        self._last_t = now
        self._last_bytes = downloaded
        repos.set_job_progress(
            self._db, self._job_id,
            json.dumps({"downloaded": downloaded, "total": total, "speed_bps": speed}),
        )


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
    if src.startswith("url:"):
        raise ValueError(
            f"asset {asset['id']} source is still a URL placeholder; fetch not complete"
        )
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


def _maybe_auto_analyze(ctx: JobContext, asset_id: str) -> str | None:
    """Smart handling: once import is complete, auto-start analysis (scene + transcript)
    so the user need not click "Analysieren". Opt out with LAURA_AUTO_ANALYZE=0. Idempotent:
    returns None (skips) when disabled or an analysis run already exists; else returns the
    enqueued job id."""
    if os.environ.get("LAURA_AUTO_ANALYZE", "1").strip().lower() in ("0", "false", "no", "off"):
        return None
    if repos.get_latest_analysis_run(ctx.db, asset_id) is not None:
        return None
    config: dict[str, Any] = {
        "stages": {"scene": True, "asr": True, "diarize": False, "align": False},
        # Must be a real model size, NOT None: handle_analysis_run does
        # config.get("model", "base"), which returns None (not "base") when the key is
        # present-but-None, and WhisperModel(None) then crashes (stat(None)). "base" is
        # the asr DEFAULT_MODEL. language None is fine (Whisper auto-detects).
        "model": "base",
        "language": None,
        "detector": "adaptive",
    }
    run = repos.create_analysis_run(
        ctx.db, asset_id=asset_id, pipeline_version=PIPELINE_VERSION, config=config
    )
    return enqueue(
        ctx.db,
        queue=queue_for("analysis.run"),
        kind="analysis.run",
        payload={"asset_id": asset_id, "analysis_run_id": run["id"], "config": config},
        idempotency_key=f"analysis:{run['id']}",
        pipeline_version=PIPELINE_VERSION,
        max_attempts=2,
        caused_by_job_id=ctx.job_id,
    )


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
    analysis_job = _maybe_auto_analyze(ctx, asset["id"])
    return {"waveform": str(dest), "length": payload["length"], "analysis_job": analysis_job}


def _finalize_media_asset(
    ctx: JobContext, asset: dict[str, Any], media: Path, *, full_scan: bool
) -> bool:
    """Verify one downloaded media file and attach it to ``asset``. Returns True if the
    asset went online. On verify failure the asset stays offline with an integrity
    record (non-fatal — used by the multi-file torrent fan-out)."""
    report = verify_decode(media, full_scan=full_scan)
    if not report.ok:
        report_path = media.parent / f"{media.name}.integrity.json"
        report_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
        repos.add_asset_file(
            ctx.db, asset_id=asset["id"], kind="integrity",
            path=str(report_path), size_bytes=report_path.stat().st_size,
        )
        return False
    repos.set_asset_source(ctx.db, asset["id"], source_path=str(media), online=True)
    enqueue(
        ctx.db, queue="ingest.io", kind="ingest.probe",
        payload={"asset_id": asset["id"]}, idempotency_key=f"probe:{asset['id']}",
        caused_by_job_id=ctx.job_id,
    )
    return True


def handle_fetch(ctx: JobContext) -> dict[str, Any]:
    asset = _require_asset(ctx)
    url = ctx.payload.get("source_url")
    if not url:
        raise ValueError("ingest.fetch payload missing required field: source_url")
    full_scan = bool(ctx.payload.get("full_scan", True))
    # yt-dlp comfort options (set by the import endpoint; absent for direct/aria2 links).
    fmt = ctx.payload.get("format")
    cookies_from_browser = ctx.payload.get("cookies_from_browser")
    root = _project_root(ctx.db, asset)
    base_dir = root / "downloads" / asset["id"]

    # Honour a cancel requested before this run started: a queued/retried fetch of an
    # already-cancelled import must exit immediately without touching the network. With
    # the start-of-handler guard, a re-enqueued attempt (up to max_attempts) is a no-op.
    if repos.is_import_cancelled(ctx.db, asset["id"]):
        _cleanup_partial_download(base_dir)
        return {"asset_id": asset["id"], "cancelled": True}

    progress = _ProgressWriter(ctx.db, ctx.job_id)
    last_hb = [0.0]

    def _on_progress(downloaded: int, total: int | None) -> None:
        # Cooperative cancellation: an abort requested mid-download is observed at the
        # next progress tick (between chunks/segments) and unwinds the download loop.
        if repos.is_import_cancelled(ctx.db, asset["id"]):
            raise ImportCancelled(asset["id"])
        progress(downloaded, total)
        now = time.monotonic()
        if now - last_hb[0] > 10.0:
            ctx.heartbeat()
            last_hb[0] = now

    try:
        return _run_fetch(
            ctx, asset, url, base_dir, full_scan=full_scan, fmt=fmt,
            cookies_from_browser=cookies_from_browser, on_progress=_on_progress,
        )
    except ImportCancelled:
        # Terminal for the import: drop the partial file and end the job normally so the
        # runner does NOT retry. import-status surfaces "cancelled" via the persisted flag.
        _cleanup_partial_download(base_dir)
        return {"asset_id": asset["id"], "cancelled": True}


def _run_fetch(
    ctx: JobContext,
    asset: dict[str, Any],
    url: str,
    base_dir: Path,
    *,
    full_scan: bool,
    fmt: Any,
    cookies_from_browser: Any,
    on_progress: Callable[[int, int | None], None],
) -> dict[str, Any]:
    """Engine dispatch for :func:`handle_fetch`. Raises :class:`ImportCancelled` when the
    import's cancel flag is observed mid-download; the caller turns that into a no-retry
    terminal result."""
    _on_progress = on_progress
    if select_engine(url) == "aria2":
        if not aria2_available():
            raise ValueError("aria2c required for this source but is not installed")
        stop_hb = threading.Event()
        cancel_event = threading.Event()

        def _hb_loop() -> None:
            # Doubles as the cancel poller for the aria2 engine (which has no per-chunk
            # callback): when the flag flips, signal aria2_download to kill the subprocess.
            while not stop_hb.wait(5.0):
                ctx.heartbeat()
                if repos.is_import_cancelled(ctx.db, asset["id"]):
                    cancel_event.set()
                    return

        hb_thread = threading.Thread(target=_hb_loop, daemon=True)
        hb_thread.start()
        try:
            files = aria2_download(
                url, base_dir,
                on_progress=lambda d, t, _s: _on_progress(d, t),
                cancel_event=cancel_event,
            )
        except Aria2Cancelled as exc:
            raise ImportCancelled(asset["id"]) from exc
        finally:
            stop_hb.set()
            hb_thread.join(timeout=1.0)
        media = [f for f in files if is_media_file(f)]
        if not media:
            base_dir.mkdir(parents=True, exist_ok=True)
            report_path = base_dir / "integrity.json"
            report_path.write_text(
                json.dumps({"ok": False, "detail": "no media file in download"}, indent=2),
                encoding="utf-8",
            )
            repos.add_asset_file(
                ctx.db, asset_id=asset["id"], kind="integrity",
                path=str(report_path), size_bytes=report_path.stat().st_size,
            )
            raise ValueError("no media file found in downloaded source")
        _finalize_media_asset(ctx, asset, media[0], full_scan=full_scan)
        for i, extra in enumerate(media[1:], start=1):
            child_id = f"{asset['id']}-{i}"
            child = repos.get_asset(ctx.db, child_id) or repos.create_asset(
                ctx.db, project_id=asset["project_id"], type="video",
                display_name=extra.name, source_path=f"url:{url}", online=False,
                asset_id=child_id,
            )
            _finalize_media_asset(ctx, child, extra, full_scan=full_scan)
        return {"asset_id": asset["id"], "engine": "aria2", "media_files": len(media)}

    # --- yt-dlp engine: "site" URLs (YouTube/Drive/Vimeo/...) need extraction ---
    # Use it when available AND the URL is not a plain media link. Direct links keep
    # the fast segmented downloader below. If yt-dlp is absent we fall through and let
    # download_resumable try (and fail clearly) — import/analysis still work without it.
    use_ytdlp = ytdlp_available() and needs_ytdlp(url)
    if use_ytdlp:
        ff_dir = str(Path(os.environ["LAURA_FFMPEG"]).parent) if os.environ.get(
            "LAURA_FFMPEG"
        ) else None
        try:
            media_path = download_via_ytdlp(
                url, base_dir, on_progress=_on_progress, ffmpeg_dir=ff_dir,
                fmt=fmt, cookies_from_browser=cookies_from_browser,
            )
        except ImportCancelled:
            # A cancel raised from inside the progress hook must NOT be reported as a
            # yt-dlp failure (ImportCancelled is a RuntimeError) — let it unwind.
            raise
        except RuntimeError as exc:
            # Surface a clear job error (e.g. a locked/missing browser cookie store)
            # rather than letting yt-dlp's exception crash the worker.
            raise ValueError(f"yt-dlp download failed: {exc}") from exc
        ok = _finalize_media_asset(ctx, asset, media_path, full_scan=full_scan)
        if not ok:
            raise ValueError("integrity check failed on yt-dlp download")
        return {"asset_id": asset["id"], "engine": "ytdlp", "downloaded": str(media_path),
                "size_bytes": os.path.getsize(media_path)}

    # --- httpx engine (HTTP/S): keep the existing strict single-asset policy ---
    raw_name = Path(asset["display_name"]).name or "download.bin"
    filename = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", raw_name) or "download.bin"
    dest = base_dir / filename
    download_resumable(url, dest, on_progress=_on_progress)
    report = verify_decode(dest, full_scan=full_scan)
    if not report.ok:
        # A "direct" link that turns out to be a page/redirect downloads non-media
        # bytes. If yt-dlp is available, retry through it before giving up.
        if ytdlp_available():
            dest.unlink(missing_ok=True)
            (dest.with_name(dest.name + ".part")).unlink(missing_ok=True)
            shutil.rmtree(dest.with_name(dest.name + ".parts"), ignore_errors=True)
            ff_dir = str(Path(os.environ["LAURA_FFMPEG"]).parent) if os.environ.get(
                "LAURA_FFMPEG"
            ) else None
            try:
                media_path = download_via_ytdlp(
                    url, base_dir, on_progress=_on_progress, ffmpeg_dir=ff_dir,
                    fmt=fmt, cookies_from_browser=cookies_from_browser,
                )
            except ImportCancelled:
                raise
            except RuntimeError as exc:
                raise ValueError(f"yt-dlp fallback failed: {exc}") from exc
            ok = _finalize_media_asset(ctx, asset, media_path, full_scan=full_scan)
            if not ok:
                raise ValueError("integrity check failed on yt-dlp fallback download")
            return {"asset_id": asset["id"], "engine": "ytdlp-fallback",
                    "downloaded": str(media_path),
                    "size_bytes": os.path.getsize(media_path)}
        report_path = dest.parent / "integrity.json"
        report_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
        repos.add_asset_file(
            ctx.db, asset_id=asset["id"], kind="integrity",
            path=str(report_path), size_bytes=report_path.stat().st_size,
        )
        dest.unlink(missing_ok=True)
        (dest.with_name(dest.name + ".part")).unlink(missing_ok=True)
        shutil.rmtree(dest.with_name(dest.name + ".parts"), ignore_errors=True)
        raise ValueError(f"integrity check failed: {report.detail}")
    repos.set_asset_source(ctx.db, asset["id"], source_path=str(dest), online=True)
    enqueue(
        ctx.db, queue="ingest.io", kind="ingest.probe",
        payload={"asset_id": asset["id"]}, idempotency_key=f"probe:{asset['id']}",
        caused_by_job_id=ctx.job_id,
    )
    return {"asset_id": asset["id"], "engine": "httpx", "downloaded": str(dest),
            "size_bytes": os.path.getsize(dest)}


def register_ingest_handlers(registry: dict[str, JobHandler]) -> None:
    registry["ingest.fetch"] = handle_fetch
    registry["ingest.probe"] = handle_probe
    registry["proxy.build"] = handle_proxy
    registry["audio.extract"] = handle_audio
    registry["waveform.build"] = handle_waveform
