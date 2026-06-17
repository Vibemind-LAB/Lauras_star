"""Shot/cut detection via TransNetV2 (optional extra: ``[scene-ml]``).

A learned deep cut detector (soCzech/transnetv2-pytorch) — usually more robust than
PySceneDetect on gradual transitions and stock footage. Heavy (pulls torch + weights), so
it is a fully optional second engine: callers select it explicitly and the orchestrator
falls back to the PySceneDetect ``adaptive`` detector when it is absent or fails.

The import is lazy (inside the functions) so this module imports fine without the package,
exactly like :mod:`laura.analysis.asr` / :mod:`laura.analysis.diarize`.
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

from ..gpu import torch_cuda_available
from .types import ShotResult


def transnetv2_available() -> bool:
    try:
        import transnetv2_pytorch  # noqa: F401
    except Exception:
        return False
    return True


def _load_model() -> Any:
    """Construct + load a TransNetV2 model, tolerating minor API drift across releases.

    v1.0.x exposes a ``TransNetV2`` class that ships its own weights and loads them on
    construction. Some builds need an explicit ``.eval()`` or a weights path; we stay
    defensive and surface a clear RuntimeError instead of leaking import-internal errors.
    """
    import transnetv2_pytorch

    model_cls = getattr(transnetv2_pytorch, "TransNetV2", None)
    if model_cls is None:
        raise RuntimeError(
            "TransNetV2 inference unavailable: transnetv2_pytorch exposes no TransNetV2 class"
        )
    try:
        model = model_cls()
        eval_fn = getattr(model, "eval", None)
        if callable(eval_fn):
            model = eval_fn() or model
    except Exception as exc:  # noqa: BLE001 - weights download / construction may fail
        msg = f"TransNetV2 inference unavailable: {type(exc).__name__}: {exc}"
        raise RuntimeError(msg) from exc
    if torch_cuda_available():
        to_fn = getattr(model, "to", None)
        if callable(to_fn):
            with contextlib.suppress(Exception):  # stay on CPU if the move fails
                model = to_fn("cuda") or model
    return model


def _scene_pairs(model: Any, video_path: str) -> list[tuple[int, int]]:
    """Return ``[(start_frame, end_frame_inclusive), ...]`` for ``video_path``.

    Prefers the package's high-level ``detect_scenes`` (it extracts frames via ffmpeg,
    runs the net, and converts predictions to numpy internally — avoiding the
    torch-Tensor/``.astype`` mismatch of the low-level path). Falls back to
    ``predict_video`` + ``predictions_to_scenes`` for older builds, taking the
    single-frame predictions (tuple index 1) and moving them to numpy first.
    """
    detect = getattr(model, "detect_scenes", None)
    if callable(detect):
        scenes = detect(video_path, threshold=0.5)  # list of {start_frame, end_frame, ...}
        return [(int(s["start_frame"]), int(s["end_frame"])) for s in scenes]

    predict = getattr(model, "predict_video", None)
    to_scenes = getattr(model, "predictions_to_scenes", None)
    if not callable(predict) or not callable(to_scenes):
        raise RuntimeError(
            "TransNetV2 inference unavailable: model has no detect_scenes / predict_video"
        )
    preds = predict(video_path, quiet=True)
    # predict_video returns (frames, single_frame_pred, all_frame_pred).
    single = preds[1] if isinstance(preds, tuple) and len(preds) >= 2 else preds
    if hasattr(single, "detach"):  # torch.Tensor -> numpy (predictions_to_scenes uses .astype)
        single = single.detach().cpu().numpy()
    raw = to_scenes(single)
    return [(int(a), int(b)) for a, b in raw]


def detect_shots_transnet(video_path: Path | str) -> list[ShotResult]:
    """Detect shot boundaries with TransNetV2 (end-exclusive source-frame ranges).

    TransNetV2 reports per-scene ``[start_frame, end_frame]`` where ``end_frame`` is
    **inclusive**; Laura's ranges are end-exclusive, so we add 1.

    Raises ImportError (via the lazy import) when the ``scene-ml`` extra is absent, and a
    clear RuntimeError when the package is present but inference fails.
    """
    import transnetv2_pytorch  # noqa: F401  # raise ImportError early if the extra is absent

    model = _load_model()
    try:
        pairs = _scene_pairs(model, str(video_path))
    except Exception as exc:  # noqa: BLE001 - inference/IO failure -> clear, typed error
        msg = f"TransNetV2 inference unavailable: {type(exc).__name__}: {exc}"
        raise RuntimeError(msg) from exc

    return [
        ShotResult(
            src_in_frame=start,
            src_out_frame_exclusive=end + 1,  # TransNetV2 end_frame is inclusive
            method="transnetv2",
        )
        for start, end in pairs
    ]
