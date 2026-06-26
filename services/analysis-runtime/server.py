"""Laura analysis GPU worker — containerised model inference.

A small, stateless HTTP service that runs Laura's heavy analysis models so the local-first backend
can offload them to a GPU container instead of the host CPU. Phase 1 ships ASR (faster-whisper);
``/scenes`` and ``/embed`` follow. Models load lazily on first use and are cached.

Contract (formgleich to the in-process result so the backend shares its DB-mapping code):

    GET  /healthz                          -> {"status":"ok","device":...,"models_loaded":[...]}
    POST /transcribe?model_size=&language= -> {"segments":[{text,start_sec,end_sec,confidence,
         body: WAV bytes (audio/wav)            words:[{text,start_sec,end_sec,confidence}]}]}

This subtree is intentionally separate from ``services/ai-runtimes/`` (voice/reenact/lipsync),
which is owned by another workstream.
"""

from __future__ import annotations

import contextlib
import logging
import os
import tempfile
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("analysis-runtime")

app = FastAPI(title="Laura Analysis Runtime")

# Lazy model cache: model_size -> faster_whisper.WhisperModel. One model resident at a time is
# enough because the backend calls the analysis stages sequentially per run.
_MODELS: dict[str, Any] = {}


def _cuda_available() -> bool:
    try:
        import ctranslate2  # faster-whisper's backend; ships its own CUDA support

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:  # noqa: BLE001
        return False


def _device_and_compute() -> tuple[str, str]:
    """Resolve device + compute type. ``LAURA_ASR_DEVICE`` overrides CUDA auto-detection."""
    forced = os.environ.get("LAURA_ASR_DEVICE")
    if forced:
        return forced, ("float16" if forced == "cuda" else "int8")
    if _cuda_available():
        return "cuda", "float16"
    return "cpu", "int8"


def _get_model(model_size: str) -> Any:
    if model_size not in _MODELS:
        from faster_whisper import WhisperModel

        device, compute = _device_and_compute()
        logger.info("loading whisper model=%s device=%s compute=%s", model_size, device, compute)
        try:
            _MODELS[model_size] = WhisperModel(model_size, device=device, compute_type=compute)
        except Exception:  # noqa: BLE001 - CUDA libs may be half-configured; retry on CPU
            if device == "cpu":
                raise
            logger.warning("CUDA load failed for %s; retrying on CPU", model_size)
            _MODELS[model_size] = WhisperModel(model_size, device="cpu", compute_type="int8")
    return _MODELS[model_size]


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    device, compute = _device_and_compute()
    return {
        "status": "ok",
        "device": device,
        "compute_type": compute,
        "models_loaded": sorted(_MODELS),
    }


@app.post("/transcribe")
async def transcribe(request: Request) -> JSONResponse:
    model_size = request.query_params.get("model_size", "base")
    language = request.query_params.get("language") or None
    vad = os.environ.get("LAURA_ASR_VAD", "1") not in {"0", "false", "False"}

    audio = await request.body()
    if not audio:
        return JSONResponse({"error": "empty audio body"}, status_code=400)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp.write(audio)
        tmp_path = tmp.name
    try:
        model = _get_model(model_size)
        segments, _info = model.transcribe(
            tmp_path, word_timestamps=True, language=language, vad_filter=vad
        )
        out: list[dict[str, Any]] = []
        for seg in segments:  # lazy generator — GPU work happens while iterating
            words = [
                {
                    "text": w.word,
                    "start_sec": float(w.start),
                    "end_sec": float(w.end),
                    "confidence": getattr(w, "probability", None),
                }
                for w in (seg.words or [])
            ]
            out.append(
                {
                    "text": seg.text.strip(),
                    "start_sec": float(seg.start),
                    "end_sec": float(seg.end),
                    "confidence": getattr(seg, "avg_logprob", None),
                    "words": words,
                }
            )
        return JSONResponse({"segments": out})
    except Exception as exc:  # noqa: BLE001 - report failure so the backend can fall back
        logger.exception("transcribe failed")
        return JSONResponse(
            {"error": f"{type(exc).__name__}: {exc}"}, status_code=500
        )
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
