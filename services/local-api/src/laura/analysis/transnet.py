"""Shot/cut detection via TransNetV2 (optional extra: ``[scene-ml]``).

A learned deep cut detector (soCzech/transnetv2-pytorch) — usually more robust than
PySceneDetect on gradual transitions and stock footage. Heavy (pulls torch + weights), so
it is a fully optional second engine: callers select it explicitly and the orchestrator
falls back to the PySceneDetect ``adaptive`` detector when it is absent or fails.

The import is lazy (inside the functions) so this module imports fine without the package,
exactly like :mod:`laura.analysis.asr` / :mod:`laura.analysis.diarize`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

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
    return model


def detect_shots_transnet(video_path: Path | str) -> list[ShotResult]:
    """Detect shot boundaries with TransNetV2 (end-exclusive source-frame ranges).

    Runs ``predict_video`` to get per-frame transition predictions, then
    ``predictions_to_scenes`` to collapse them into ``[start, end]`` pairs whose ``end`` is
    **inclusive**; we convert to Laura's end-exclusive ``ShotResult``.

    Raises ImportError (via the lazy import) when the ``scene-ml`` extra is absent, and a
    clear RuntimeError when the package is present but the model/weights cannot be loaded or
    the API differs from what we expect.
    """
    import transnetv2_pytorch  # noqa: F401  # raise ImportError early if the extra is absent

    model = _load_model()

    predict = getattr(model, "predict_video", None)
    to_scenes = getattr(model, "predictions_to_scenes", None) or getattr(
        transnetv2_pytorch, "predictions_to_scenes", None
    )
    if not callable(predict) or not callable(to_scenes):
        raise RuntimeError(
            "TransNetV2 inference unavailable: model is missing predict_video / "
            "predictions_to_scenes"
        )

    try:
        predictions = predict(str(video_path))
        # predict_video may return (single_frame_pred, all_frame_pred); scenes derive from
        # the single-frame predictions.
        single_frame = predictions[0] if isinstance(predictions, tuple) else predictions
        scenes = to_scenes(single_frame)
    except Exception as exc:  # noqa: BLE001 - inference/IO failure -> clear, typed error
        msg = f"TransNetV2 inference unavailable: {type(exc).__name__}: {exc}"
        raise RuntimeError(msg) from exc

    return [
        ShotResult(
            src_in_frame=int(start),
            src_out_frame_exclusive=int(end) + 1,  # TransNetV2 end is inclusive
            method="transnetv2",
        )
        for start, end in scenes
    ]
