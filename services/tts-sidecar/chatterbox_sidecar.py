"""Standalone Chatterbox TTS voiceover sidecar for Laura.

Fulfils the ``SidecarVoiceoverBackend`` contract from
``services/local-api/src/laura/ai/voiceover_backend.py``:

* ``GET /healthz``  -> 200 ``"ok"`` without touching the model (fast readiness probe).
* ``POST /voiceover`` JSON body -> ``audio/wav`` bytes.

This script runs in Chatterbox's OWN virtualenv (e.g. ``E:\\chatterbox``), never in
Laura's ``services/local-api`` venv. It intentionally imports ``torch``/``torchaudio``/
``chatterbox`` only inside functions (lazily, on first use) so that:

* ``python -m py_compile`` succeeds even where those packages are not installed.
* nothing explodes merely by importing this module (e.g. from a test file) in an
  environment that lacks the Chatterbox stack.

Stdlib only at module scope. Start with::

    E:\\chatterbox\\.venv\\Scripts\\python.exe chatterbox_sidecar.py --port 8898

See ``README.md`` next to this file for the full start recipe and env vars.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import torch

logger = logging.getLogger("chatterbox_sidecar")

DEFAULT_PORT = 8898
DEFAULT_HOST = "127.0.0.1"
DEFAULT_DEVICE = "cuda"
DEFAULT_VOICE_REF_FILENAME = "felix_ref.wav"
DEFAULT_EXAGGERATION = 0.5
DEFAULT_CFG_WEIGHT = 0.5


class VoiceoverError(RuntimeError):
    """A request-specific failure. Its message goes verbatim into the 500 body."""


# ---------------------------------------------------------------------------
# Model loading (lazy, thread-lock guarded -- loaded once on first request)
# ---------------------------------------------------------------------------

_model_lock = threading.Lock()
_model_state: dict[str, Any] = {"model": None, "device": None}


def _get_model(device: str) -> Any:
    """Return the cached ChatterboxTTS model, loading it on first use.

    Guarded by a lock so concurrent requests during the (slow) first load don't
    each try to load their own copy. Reloads only if a later request asks for a
    different device than the one currently loaded.
    """
    with _model_lock:
        cached = _model_state.get("model")
        if cached is not None and _model_state.get("device") == device:
            return cached

        # Lazy import: torch/chatterbox live only in this script's own venv.
        from chatterbox.tts import ChatterboxTTS

        logger.info(
            "loading ChatterboxTTS on device=%s (first request may download "
            "~2GB of weights; respects HF_HOME=%s)",
            device,
            os.environ.get("HF_HOME", "<unset>"),
        )
        t0 = time.time()
        try:
            model = ChatterboxTTS.from_pretrained(device=device)
        except Exception as exc:  # noqa: BLE001 - surfaced verbatim as the 500 body
            raise VoiceoverError(f"failed to load ChatterboxTTS model: {exc}") from exc
        logger.info("model loaded in %.1fs (sr=%d)", time.time() - t0, model.sr)

        _model_state["model"] = model
        _model_state["device"] = device
        return model


def _synthesize(model: Any, text: str, voice_ref: Path) -> tuple[torch.Tensor, int]:
    """Generate speech with the cloned reference voice. Returns (wav, sample_rate)."""
    try:
        wav = model.generate(
            text,
            audio_prompt_path=str(voice_ref),
            exaggeration=DEFAULT_EXAGGERATION,
            cfg_weight=DEFAULT_CFG_WEIGHT,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim as the 500 body
        raise VoiceoverError(f"chatterbox generation failed: {exc}") from exc
    return wav, int(model.sr)


def _save_wav(path: Path, wav: torch.Tensor, sample_rate: int) -> None:
    import torchaudio  # lazy import: same reason as ChatterboxTTS above

    try:
        torchaudio.save(str(path), wav, sample_rate)
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim as the 500 body
        raise VoiceoverError(f"failed to write synthesized wav: {exc}") from exc


# ---------------------------------------------------------------------------
# Reference-voice resolution
# ---------------------------------------------------------------------------


def _resolve_voice_ref(voice_id: str | None) -> Path:
    """Pick the reference wav: ``voice_id`` (if it is an existing file) overrides
    ``CHATTERBOX_VOICE_REF``, which overrides ``felix_ref.wav`` next to this script."""
    candidates: list[tuple[str, str]] = []
    if voice_id:
        candidates.append(("voice_id", voice_id))
    env_ref = os.environ.get("CHATTERBOX_VOICE_REF")
    if env_ref:
        candidates.append(("CHATTERBOX_VOICE_REF", env_ref))
    default_ref = Path(__file__).resolve().with_name(DEFAULT_VOICE_REF_FILENAME)
    candidates.append(("default", str(default_ref)))

    for _source, raw_path in candidates:
        path = Path(raw_path)
        if path.is_file():
            return path

    tried = ", ".join(f"{source}={raw_path}" for source, raw_path in candidates)
    raise VoiceoverError(f"no reference voice wav found (tried: {tried})")


# ---------------------------------------------------------------------------
# Resampling / slot-fitting via ffmpeg subprocess
# ---------------------------------------------------------------------------


def _ffmpeg_bin() -> str:
    return os.environ.get("LAURA_FFMPEG") or os.environ.get("FFMPEG_BIN") or "ffmpeg"


def _resample_and_fit(
    src: Path,
    dst: Path,
    *,
    sample_rate: int,
    fit_to_slot: bool,
    duration_frames: int,
    fps_num: int,
    fps_den: int,
) -> None:
    """Resample ``src`` to mono ``sample_rate`` pcm_s16le; optionally pad/trim to the
    exact slot duration (``apad`` pads short speech, ``-t`` trims long speech), the
    same way Laura's WindowsSapiVoiceoverBackend/ElevenLabsVoiceoverBackend do."""
    binary = _ffmpeg_bin()
    args = [
        binary,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(src),
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
    ]
    if fit_to_slot:
        seconds = duration_frames * fps_den / fps_num
        args += ["-af", "apad", "-t", f"{seconds:.6f}"]
    args += ["-c:a", "pcm_s16le", str(dst)]

    try:
        proc = subprocess.run(args, capture_output=True, text=True)  # noqa: S603
    except OSError as exc:
        raise VoiceoverError(f"failed to launch ffmpeg ({binary}): {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "ffmpeg failed").strip()[-2000:]
        raise VoiceoverError(f"ffmpeg resample failed: {detail}")
    if not dst.is_file() or dst.stat().st_size == 0:
        raise VoiceoverError("ffmpeg produced no output file")


# ---------------------------------------------------------------------------
# Payload validation
# ---------------------------------------------------------------------------


def _require_positive_int(payload: dict[str, Any], key: str) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise VoiceoverError(f"payload.{key} must be a positive number")
    result = int(value)
    if result <= 0:
        raise VoiceoverError(f"payload.{key} must be positive")
    return result


def handle_voiceover(payload: dict[str, Any]) -> bytes:
    """Validate the request payload, synthesize, and return WAV bytes.

    Payload fields (per the SidecarVoiceoverBackend contract): text, duration_frames,
    fps_num, fps_den, sample_rate, language (optional), voice_id (optional),
    fit_to_slot (optional, default False).
    """
    raw_text = payload.get("text")
    text = str(raw_text).strip() if raw_text is not None else ""
    if not text:
        raise VoiceoverError("payload.text must be a non-empty string")

    duration_frames = _require_positive_int(payload, "duration_frames")
    fps_num = _require_positive_int(payload, "fps_num")
    fps_den = _require_positive_int(payload, "fps_den")
    sample_rate = _require_positive_int(payload, "sample_rate")

    voice_id_raw = payload.get("voice_id")
    voice_id = voice_id_raw if isinstance(voice_id_raw, str) and voice_id_raw.strip() else None
    fit_to_slot = bool(payload.get("fit_to_slot", False))
    language = payload.get("language")

    voice_ref = _resolve_voice_ref(voice_id)
    device = os.environ.get("CHATTERBOX_DEVICE") or DEFAULT_DEVICE
    model = _get_model(device)

    logger.info(
        "synthesizing %d chars (language=%s, voice_ref=%s, device=%s, fit_to_slot=%s)",
        len(text),
        language,
        voice_ref,
        device,
        fit_to_slot,
    )
    wav, model_sr = _synthesize(model, text, voice_ref)

    with tempfile.TemporaryDirectory(prefix="chatterbox_sidecar_") as tmp:
        raw_path = Path(tmp) / "voiceover_raw.wav"
        _save_wav(raw_path, wav, model_sr)

        out_path = Path(tmp) / "voiceover_out.wav"
        _resample_and_fit(
            raw_path,
            out_path,
            sample_rate=sample_rate,
            fit_to_slot=fit_to_slot,
            duration_frames=duration_frames,
            fps_num=fps_num,
            fps_den=fps_den,
        )
        return out_path.read_bytes()


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------


class SidecarHandler(BaseHTTPRequestHandler):
    server_version = "ChatterboxSidecar/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
        logger.info("%s - %s", self.address_string(), format % args)

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler method name
        if self.path.split("?", 1)[0] == "/healthz":
            self._send_plain(200, "ok")
            return
        self._send_plain(404, f"not found: {self.path}")

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler method name
        if self.path.split("?", 1)[0] != "/voiceover":
            self._send_plain(404, f"not found: {self.path}")
            return
        try:
            payload = self._read_json_body()
            wav_bytes = handle_voiceover(payload)
        except VoiceoverError as exc:
            logger.error("voiceover request failed: %s", exc)
            self._send_plain(500, str(exc))
            return
        except Exception as exc:  # noqa: BLE001 - any failure must still yield a clear 500
            logger.exception("voiceover request failed unexpectedly")
            self._send_plain(500, f"unexpected error: {exc}")
            return

        self.send_response(200)
        self.send_header("Content-Type", "audio/wav")
        self.send_header("Content-Length", str(len(wav_bytes)))
        self.end_headers()
        self.wfile.write(wav_bytes)

    def _read_json_body(self) -> dict[str, Any]:
        length_header = self.headers.get("Content-Length")
        if length_header is None:
            raise VoiceoverError("missing Content-Length header")
        try:
            length = int(length_header)
        except ValueError as exc:
            raise VoiceoverError(f"invalid Content-Length: {length_header}") from exc
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise VoiceoverError(f"invalid JSON body: {exc}") from exc
        if not isinstance(data, dict):
            raise VoiceoverError("JSON body must be an object")
        return data

    def _send_plain(self, status: int, message: str) -> None:
        body = message.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Chatterbox TTS voiceover sidecar for Laura")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="Listen port (default 8898)")
    parser.add_argument(
        "--host", type=str, default=DEFAULT_HOST, help="Listen host (default 127.0.0.1)"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    logger.info("starting chatterbox sidecar on %s:%d", args.host, args.port)
    server = ThreadingHTTPServer((args.host, args.port), SidecarHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("shutting down (KeyboardInterrupt)")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
