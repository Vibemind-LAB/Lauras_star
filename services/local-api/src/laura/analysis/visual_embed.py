"""Visual frame-embedder + RGB frame sampler + ``shorts.embed_frames`` job (VE1).

Produces per-frame visual embeddings and stores them in the VE2 vector store
(:mod:`laura.analysis.embeddings_store`). The default embedder uses ``fastembed``
(extra ``[semantic]``) and is **optional**: the module imports nothing heavy at
import time — ``fastembed``/``PIL`` are loaded *lazily*, only inside
:class:`FastEmbedImageEmbedder`. The job gates gracefully so a CPU-only backend
without the extra simply *skips* embedding rather than failing.

Invariants honoured (same as the rest of Laura's editorial layer):

* Integer source frames everywhere; the 1-fps sample grid is computed from the
  asset's integer frame rate and clamped to ``[0, total_frames)``.
* The frame IO is injected through ``frame_loader`` so tests feed in-memory RGB
  frames without ffmpeg, and the embedder is injected so tests use a deterministic
  fake — no model, no download.
"""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import numpy as np

from ..db import repos
from ..db.database import Database
from ..jobs.runner import JobContext, JobHandler
from .embeddings_store import FrameEmbedding, SqliteVectorStore

_log = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_DIMS",
    "DEFAULT_FPS_SAMPLE",
    "DEFAULT_VISUAL_MODEL",
    "FRAME_H",
    "FRAME_W",
    "Embedder",
    "FastEmbedImageEmbedder",
    "RgbFrameLoader",
    "handle_embed_frames",
    "load_rgb_frames_ffmpeg",
    "register_visual_handlers",
    "sample_frame_indices",
    "visual_available",
]

# Default CLIP image encoder shipped by fastembed. 512-dim, ViT-B/32.
DEFAULT_VISUAL_MODEL = "Qdrant/clip-ViT-B-32-vision"
DEFAULT_DIMS = 512

# Decode samples at the encoder's native input size; cheap + model-friendly.
FRAME_W = FRAME_H = 224

# One sample per second of source is plenty for shot-level visual context.
DEFAULT_FPS_SAMPLE = 1.0


# ---------------------------------------------------------------------------
# Optional-extra probe
# ---------------------------------------------------------------------------


def visual_available() -> bool:
    """``True`` when the visual embedder's extra (``fastembed`` + ``PIL``) is importable.

    Mirrors the ``*_available()`` probes elsewhere (``faster_whisper_available`` etc.):
    a soft check the job uses to skip gracefully. Never imports the heavy model.
    """
    try:
        import fastembed  # noqa: F401
        from PIL import Image  # noqa: F401
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# Embedder protocol + default fastembed implementation
# ---------------------------------------------------------------------------


@runtime_checkable
class Embedder(Protocol):
    """A visual embedder: turns RGB frames into a stacked float32 matrix.

    ``embed_frames`` takes a list of ``HxWx3`` uint8 RGB arrays and returns an
    ``(N, dims)`` float32 array, one row per input frame in the same order.
    """

    name: str
    dims: int

    def embed_frames(self, frames: list[np.ndarray]) -> np.ndarray: ...


class FastEmbedImageEmbedder:
    """Default :class:`Embedder` backed by ``fastembed``'s ``ImageEmbedding`` (CLIP).

    ``fastembed`` and ``PIL`` are imported **lazily** (first use), so importing this
    module never pulls the heavy extra. Construction is cheap; the model is built on
    the first :meth:`embed_frames` call. When the extra is missing, a clear
    :class:`RuntimeError` is raised — but the job avoids ever reaching here by gating
    on :func:`visual_available`.
    """

    def __init__(self, model: str = DEFAULT_VISUAL_MODEL) -> None:
        self.name = model
        self.dims = DEFAULT_DIMS
        self._model: Any | None = None

    def _ensure_model(self) -> Any:
        if self._model is None:
            try:
                from fastembed import ImageEmbedding
            except Exception as exc:  # pragma: no cover - exercised only without extra
                raise RuntimeError(
                    "visual extra not installed (uv sync --extra semantic)"
                ) from exc
            self._model = ImageEmbedding(model_name=self.name)
        return self._model

    def embed_frames(self, frames: list[np.ndarray]) -> np.ndarray:
        """Embed ``frames`` → ``(N, dims)`` float32. Empty input → ``(0, dims)``."""
        if not frames:
            return np.empty((0, self.dims), dtype=np.float32)
        try:
            from PIL import Image
        except Exception as exc:  # pragma: no cover - exercised only without extra
            raise RuntimeError(
                "visual extra not installed (uv sync --extra semantic)"
            ) from exc
        model = self._ensure_model()
        images = [Image.fromarray(np.asarray(f, dtype=np.uint8)) for f in frames]
        vectors = list(model.embed(images))
        out = np.asarray(vectors, dtype=np.float32)
        # Keep ``dims`` consistent with the real output if the model differs from 512.
        if out.ndim == 2 and out.shape[1] > 0:
            self.dims = int(out.shape[1])
        return out


# ---------------------------------------------------------------------------
# RGB frame sampler (mirrors eval_cut.load_gray_frames_ffmpeg, but rgb24 + index list)
# ---------------------------------------------------------------------------

# (video_path, frame_indices, w, h) -> one HxWx3 uint8 RGB array per index, same order.
RgbFrameLoader = Callable[["Path | str", "list[int]", int, int], "list[np.ndarray]"]


def load_rgb_frames_ffmpeg(
    video: Path | str,
    frames: list[int],
    w: int = FRAME_W,
    h: int = FRAME_H,
) -> list[np.ndarray]:
    """Decode the RGB frames at the explicit ``frames`` indices as ``HxWx3`` uint8 arrays.

    Mirrors :func:`laura.analysis.eval_cut.load_gray_frames_ffmpeg` but selects a list of
    (non-consecutive) frame indices and decodes ``rgb24`` (3 bytes/px). ffmpeg emits the
    selected frames in increasing source order; this returns them in that order. Empty
    index list → ``[]``.
    """
    if not frames:
        return []
    # select='eq(n\,F1)+eq(n\,F2)+…' — pass each requested frame through.
    terms = "+".join(f"eq(n\\,{int(f)})" for f in frames)
    cmd = [
        "ffmpeg", "-v", "error", "-i", str(video),
        "-vf", f"select='{terms}',scale={w}:{h},format=rgb24",
        "-vsync", "0", "-frames:v", str(len(frames)), "-f", "rawvideo", "-",
    ]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout  # noqa: S603
    frame_bytes = w * h * 3
    decoded: list[np.ndarray] = []
    for off in range(0, len(out) - frame_bytes + 1, frame_bytes):
        decoded.append(
            np.frombuffer(out[off : off + frame_bytes], dtype=np.uint8).reshape(h, w, 3)
        )
    return decoded


def sample_frame_indices(
    total_frames: int,
    rate_num: int,
    rate_den: int,
    shot_boundaries: Sequence[int],
    *,
    fps_sample: float = DEFAULT_FPS_SAMPLE,
) -> list[int]:
    """Pure: a 1-fps-style sample grid over ``[0, total_frames)`` ∪ shot-boundary frames.

    The grid steps by ``round(fps / fps_sample)`` frames (``fps = rate_num/rate_den``),
    minimum step 1. Shot boundaries are unioned in so every cut is represented. The result
    is deduplicated, sorted ascending and clamped to ``[0, total_frames)``. With
    ``total_frames <= 0`` the result is empty.
    """
    if total_frames <= 0:
        return []
    den = rate_den or 1
    fps = (rate_num or 25) / den
    step = 1 if fps <= 0 or fps_sample <= 0 else max(1, round(fps / fps_sample))

    indices: set[int] = set(range(0, total_frames, step))
    for b in shot_boundaries:
        bi = int(b)
        if 0 <= bi < total_frames:
            indices.add(bi)
    return sorted(indices)


# ---------------------------------------------------------------------------
# embed-frames job
# ---------------------------------------------------------------------------


def _resolve_video_path(db: Database, asset: dict[str, Any]) -> str:
    """The source video path for decoding: prefer the proxy file, else the source.

    Same selection the analysis scene stage uses
    (``files["proxy"]["path"] if "proxy" else asset["source_path"]``).
    """
    files = {f["kind"]: f for f in repos.list_asset_files(db, asset["id"])}
    if "proxy" in files and files["proxy"].get("path"):
        return str(files["proxy"]["path"])
    return str(asset["source_path"])


def handle_embed_frames(
    ctx: JobContext,
    *,
    embedder: Embedder | None = None,
    frame_loader: RgbFrameLoader = load_rgb_frames_ffmpeg,
) -> dict[str, Any]:
    """Sample frames, embed them and persist the vectors for one asset (VE1).

    Payload: ``{"asset_id": str}``. ``embedder`` and ``frame_loader`` are injectable so
    tests run with a deterministic fake and no ffmpeg/model.

    Graceful gating: if no embedder is injected *and* the visual extra is absent, the job
    returns ``{"ok": False, "skipped": …}`` without error — a CPU-only backend simply does
    not embed. Without a *succeeded* latest analysis run it returns
    ``{"ok": False, "error": …}``.

    On success persists ``(asset_id, run_id)``'s frame embeddings (replace-wholesale, so the
    job is idempotent) and returns a small summary dict.
    """
    db = ctx.db
    asset_id: str = ctx.payload["asset_id"]

    # --- graceful gate: no embedder + no extra → skip (never raise) --------
    if embedder is None and not visual_available():
        _log.info("shorts.embed_frames asset=%s skipped: visual extra not installed", asset_id)
        return {"ok": False, "skipped": "visual extra not installed", "asset_id": asset_id}

    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise ValueError(f"asset not found: {asset_id}")
    project = repos.get_project(db, asset["project_id"])
    if project is None:
        raise ValueError(f"project not found for asset {asset_id}: {asset['project_id']}")

    rate_num: int = int(asset["rate_num"] or 25)
    rate_den: int = int(asset["rate_den"] or 1)
    total_frames: int = int(asset["duration_frames"] or 0)

    run = repos.get_latest_analysis_run(db, asset_id)
    if run is None or run["status"] != "succeeded":
        return {"ok": False, "error": "no succeeded analysis run", "asset_id": asset_id}
    run_id: str = run["id"]

    shots = repos.list_shots(db, asset_id, run_id)
    boundaries = [int(s["src_in_frame"]) for s in shots]

    idx = sample_frame_indices(total_frames, rate_num, rate_den, boundaries)

    emb: Embedder = embedder or FastEmbedImageEmbedder()
    store = SqliteVectorStore(db)

    if not idx:
        store.replace_frame_embeddings(asset_id, run_id, [])
        _log.info("shorts.embed_frames asset=%s run=%s frames=0 (no samples)", asset_id, run_id)
        return {
            "ok": True,
            "asset_id": asset_id,
            "analysis_run_id": run_id,
            "frames": 0,
            "model": emb.name,
            "dims": emb.dims,
        }

    video_path = _resolve_video_path(db, asset)
    decoded = frame_loader(video_path, idx, FRAME_W, FRAME_H)

    # Decoder may yield fewer frames than requested (e.g. indices past EOF) — truncate
    # both sides to the common prefix so indices stay aligned with their vectors.
    n = min(len(idx), len(decoded))
    used_idx = idx[:n]
    frames = list(decoded[:n])

    vectors = emb.embed_frames(frames) if frames else np.empty((0, emb.dims), dtype=np.float32)

    items = [
        FrameEmbedding(
            asset_id=asset_id,
            analysis_run_id=run_id,
            frame=int(used_idx[i]),
            model=emb.name,
            vector=np.asarray(vectors[i], dtype=np.float32),
        )
        for i in range(len(used_idx))
    ]
    store.replace_frame_embeddings(asset_id, run_id, items)

    _log.info(
        "shorts.embed_frames asset=%s run=%s frames=%d model=%s dims=%d",
        asset_id, run_id, len(items), emb.name, emb.dims,
    )
    return {
        "ok": True,
        "asset_id": asset_id,
        "analysis_run_id": run_id,
        "frames": len(items),
        "model": emb.name,
        "dims": emb.dims,
    }


def register_visual_handlers(registry: dict[str, JobHandler]) -> None:
    """Register the ``shorts.embed_frames`` handler on the job registry."""
    registry["shorts.embed_frames"] = handle_embed_frames
