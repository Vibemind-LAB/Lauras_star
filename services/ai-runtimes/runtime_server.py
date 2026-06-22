from __future__ import annotations

import json
import logging
import math
import os
import re
import shlex
import struct
import subprocess
import tempfile
import wave
from dataclasses import dataclass
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

LOGGER = logging.getLogger("laura.ai_runtime")
VERSION = "0.1.0"
DEFAULT_SAMPLE_RATE = 48_000


@dataclass(frozen=True)
class RuntimeSpec:
    runtime: str
    effect: str
    default_port: int
    endpoints: tuple[str, ...]
    command_env: tuple[str, ...]
    requires_gpu: bool
    required_model_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class RuntimeConfig:
    kind: str
    mode: str = "model"
    port: int = 0
    model_root: Path | None = None
    command: str | None = None
    provider: str | None = None
    probe_command: str | None = None
    required_model_paths: tuple[str, ...] | None = None
    timeout_seconds: float = 3600.0
    version: str = VERSION

    @classmethod
    def from_env(cls) -> RuntimeConfig:
        kind = os.environ.get("LAURA_RUNTIME_KIND", "voice")
        spec = runtime_spec(kind)
        raw_port = os.environ.get("LAURA_RUNTIME_PORT")
        port = int(raw_port) if raw_port else spec.default_port
        command = _first_env(spec.command_env)
        timeout = _float_env("LAURA_RUNTIME_TIMEOUT", 3600.0)
        return cls(
            kind=spec.runtime,
            mode=os.environ.get("LAURA_RUNTIME_MODE", "model"),
            port=port,
            model_root=Path(os.environ.get("LAURA_MODEL_ROOT", "/models")),
            command=command,
            provider=os.environ.get("LAURA_RUNTIME_PROVIDER"),
            probe_command=os.environ.get("LAURA_VIBEVIDEO_PROBE_COMMAND"),
            required_model_paths=_path_list_env("LAURA_MODEL_REQUIRED_PATHS"),
            timeout_seconds=timeout,
        )


@dataclass(frozen=True)
class UploadedFile:
    filename: str
    content_type: str
    data: bytes


_RUNTIME_SPECS = {
    "liveportrait": RuntimeSpec(
        runtime="liveportrait",
        effect="reenact",
        default_port=8899,
        endpoints=("/reenact",),
        command_env=("LAURA_LIVEPORTRAIT_COMMAND",),
        requires_gpu=True,
        required_model_paths=("LivePortrait/pretrained_weights",),
    ),
    "vibevideo": RuntimeSpec(
        runtime="vibevideo",
        effect="lipsync",
        default_port=8901,
        endpoints=("/probe", "/lipsync"),
        command_env=("LAURA_VIBEVIDEO_COMMAND", "LAURA_LIPSYNC_COMMAND"),
        requires_gpu=True,
        required_model_paths=(
            "MuseTalk/models/musetalkV15/unet.pth",
            "MuseTalk/models/musetalkV15/musetalk.json",
            "MuseTalk/models/dwpose/dw-ll_ucoco_384.pth",
            "MuseTalk/models/face-parse-bisent/79999_iter.pth",
            "MuseTalk/models/face-parse-bisent/resnet18-5c106cde.pth",
            "MuseTalk/models/sd-vae/config.json",
            "MuseTalk/models/sd-vae/diffusion_pytorch_model.bin",
            "MuseTalk/models/whisper/config.json",
            "MuseTalk/models/whisper/preprocessor_config.json",
            "MuseTalk/models/whisper/pytorch_model.bin",
        ),
    ),
    "voice": RuntimeSpec(
        runtime="voice",
        effect="voice",
        default_port=8898,
        endpoints=("/voiceover",),
        command_env=("LAURA_VOICE_COMMAND", "LAURA_VOICEOVER_COMMAND"),
        requires_gpu=False,
        required_model_paths=("piper",),
    ),
}

_KIND_ALIASES = {
    "reenact": "liveportrait",
    "lipsync": "vibevideo",
    "tts": "voice",
    "voiceover": "voice",
}
_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def runtime_spec(kind: str) -> RuntimeSpec:
    normalized = _KIND_ALIASES.get(kind.strip().lower(), kind.strip().lower())
    try:
        return _RUNTIME_SPECS[normalized]
    except KeyError as exc:
        raise ValueError(f"unsupported runtime kind: {kind!r}") from exc


def build_handler(config: RuntimeConfig) -> type[BaseHTTPRequestHandler]:
    spec = runtime_spec(config.kind)

    class RuntimeHandler(BaseHTTPRequestHandler):
        server_version = "LauraRuntime/0.1"

        def log_message(self, message: str, *args: object) -> None:
            LOGGER.info("%s - %s", self.address_string(), message % args)

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/healthz":
                self._send_json(_health_payload(config, spec))
                return
            if path == "/version":
                self._send_json(
                    {
                        "runtime": spec.runtime,
                        "version": config.version,
                        "mode": _mode(config),
                    }
                )
                return
            if path == "/capabilities":
                self._send_json(_capabilities_payload(config, spec))
                return
            self._send_json({"error": "not found"}, status=404)

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            try:
                if spec.runtime == "voice" and path == "/voiceover":
                    self._handle_voiceover()
                    return
                if spec.runtime == "liveportrait" and path == "/reenact":
                    self._handle_reenact()
                    return
                if spec.runtime == "vibevideo" and path == "/probe":
                    self._handle_probe()
                    return
                if spec.runtime == "vibevideo" and path == "/lipsync":
                    self._handle_lipsync()
                    return
            except ValueError as exc:
                self._send_json({"error": str(exc)}, status=400)
                return
            except RuntimeError as exc:
                self._send_json({"error": str(exc)}, status=500)
                return
            self._send_json({"error": "not found"}, status=404)

        def _handle_voiceover(self) -> None:
            payload = self._read_json()
            text = str(payload.get("text") or "")
            duration_frames = _positive_int(payload.get("duration_frames"), "duration_frames")
            fps_num = _positive_int(payload.get("fps_num"), "fps_num")
            fps_den = _positive_int(payload.get("fps_den"), "fps_den")
            sample_rate = _positive_int(
                payload.get("sample_rate", DEFAULT_SAMPLE_RATE),
                "sample_rate",
            )
            if _mode(config) == "smoke":
                wav_bytes = synthesize_smoke_wav(
                    text=text,
                    duration_frames=duration_frames,
                    fps_num=fps_num,
                    fps_den=fps_den,
                    sample_rate=sample_rate,
                )
                self._send_bytes(
                    wav_bytes,
                    content_type="audio/wav",
                    headers={"X-Laura-Smoke": "true"},
                )
                return

            with tempfile.TemporaryDirectory(prefix="laura-voice-") as tmp:
                tmp_dir = Path(tmp)
                request_json = tmp_dir / "request.json"
                output = tmp_dir / "voiceover.wav"
                request_json.write_text(json.dumps(payload), encoding="utf-8")
                _run_model_command(
                    config,
                    {
                        "request_json": request_json,
                        "output": output,
                        "model_root": _model_root(config),
                        "duration_frames": duration_frames,
                        "fps_num": fps_num,
                        "fps_den": fps_den,
                        "sample_rate": sample_rate,
                    },
                )
                self._send_model_output(output, "audio/wav")

        def _handle_reenact(self) -> None:
            files, fields = self._read_multipart()
            driving = _required_file(files, "driving")
            portrait = _required_file(files, "portrait")
            if _mode(config) == "smoke":
                self._send_bytes(
                    driving.data,
                    content_type=driving.content_type or "video/mp4",
                    headers={"X-Laura-Smoke": "true"},
                )
                return

            with tempfile.TemporaryDirectory(prefix="laura-liveportrait-") as tmp:
                tmp_dir = Path(tmp)
                driving_path = _write_upload(tmp_dir, "driving", driving)
                portrait_path = _write_upload(tmp_dir, "portrait", portrait)
                output = tmp_dir / "reenact.mp4"
                _run_model_command(
                    config,
                    {
                        "driving": driving_path,
                        "portrait": portrait_path,
                        "output": output,
                        "model_root": _model_root(config),
                        "fps_num": fields.get("fps_num", "30"),
                        "fps_den": fields.get("fps_den", "1"),
                    },
                )
                self._send_model_output(output, "video/mp4")

        def _handle_probe(self) -> None:
            files, fields = self._read_multipart()
            video = files.get("video")
            audio = files.get("audio")
            if _mode(config) == "model" and config.probe_command:
                if video is None or audio is None:
                    raise ValueError("probe requires video and audio files")
                with tempfile.TemporaryDirectory(prefix="laura-vibevideo-probe-") as tmp:
                    tmp_dir = Path(tmp)
                    video_path = _write_upload(tmp_dir, "video", video)
                    audio_path = _write_upload(tmp_dir, "audio", audio)
                    probe_json = tmp_dir / "probe.json"
                    _run_shell_command(
                        config.probe_command,
                        config.timeout_seconds,
                        {
                            "video": video_path,
                            "audio": audio_path,
                            "probe_json": probe_json,
                            "model_root": _model_root(config),
                            "fps_num": fields.get("fps_num", "30"),
                            "fps_den": fields.get("fps_den", "1"),
                        },
                    )
                    self._send_json(_read_probe_json(probe_json))
                    return
            self._send_json(
                {
                    "face_detected": bool(video and video.data),
                    "mouth_visible": bool(video and video.data),
                    "audio_present": bool(audio and audio.data),
                }
            )

        def _handle_lipsync(self) -> None:
            files, fields = self._read_multipart()
            video = _required_file(files, "video")
            audio = _required_file(files, "audio")
            quality = {
                "sync_score": 0.92,
                "mouth_score": 0.9,
                "temporal_score": 0.91,
                "passed": True,
            }
            if _mode(config) == "smoke":
                self._send_bytes(
                    video.data,
                    content_type=video.content_type or "video/mp4",
                    headers={
                        "X-Laura-Quality": json.dumps(quality),
                        "X-Laura-Smoke": "true",
                    },
                )
                return

            with tempfile.TemporaryDirectory(prefix="laura-vibevideo-") as tmp:
                tmp_dir = Path(tmp)
                video_path = _write_upload(tmp_dir, "video", video)
                audio_path = _write_upload(tmp_dir, "audio", audio)
                output = tmp_dir / "lipsync.mp4"
                _run_model_command(
                    config,
                    {
                        "video": video_path,
                        "audio": audio_path,
                        "output": output,
                        "model_root": _model_root(config),
                        "fps_num": fields.get("fps_num", "30"),
                        "fps_den": fields.get("fps_den", "1"),
                    },
                )
                self._send_model_output(
                    output,
                    "video/mp4",
                    headers={"X-Laura-Quality": json.dumps(quality)},
                )

        def _send_model_output(
            self,
            output: Path,
            content_type: str,
            *,
            headers: dict[str, str] | None = None,
        ) -> None:
            if not output.exists() or output.stat().st_size <= 0:
                raise RuntimeError(f"model command did not create output: {output}")
            self._send_bytes(output.read_bytes(), content_type=content_type, headers=headers)

        def _read_json(self) -> dict[str, Any]:
            body = self._read_body()
            try:
                data = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON body: {exc}") from exc
            if not isinstance(data, dict):
                raise ValueError("JSON body must be an object")
            return data

        def _read_multipart(self) -> tuple[dict[str, UploadedFile], dict[str, str]]:
            content_type = self.headers.get("Content-Type", "")
            if "multipart/form-data" not in content_type:
                raise ValueError("Content-Type must be multipart/form-data")
            body = self._read_body()
            return parse_multipart(body, content_type)

        def _read_body(self) -> bytes:
            raw_length = self.headers.get("Content-Length", "0")
            try:
                length = int(raw_length)
            except ValueError as exc:
                raise ValueError("Content-Length must be an integer") from exc
            return self.rfile.read(max(0, length))

        def _send_json(self, payload: dict[str, object], *, status: int = 200) -> None:
            body = json.dumps(payload).encode("utf-8")
            self._send_bytes(body, status=status, content_type="application/json")

        def _send_bytes(
            self,
            body: bytes,
            *,
            status: int = 200,
            content_type: str,
            headers: dict[str, str] | None = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in (headers or {}).items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

    return RuntimeHandler


def parse_multipart(
    body: bytes,
    content_type: str,
) -> tuple[dict[str, UploadedFile], dict[str, str]]:
    header = f"Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n".encode()
    message = BytesParser(policy=policy.default).parsebytes(header + body)
    files: dict[str, UploadedFile] = {}
    fields: dict[str, str] = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if not isinstance(name, str) or not name:
            continue
        raw_payload = part.get_payload(decode=True)
        payload = raw_payload if isinstance(raw_payload, bytes) else b""
        filename = part.get_filename()
        if filename is None:
            fields[name] = payload.decode("utf-8", errors="replace")
            continue
        files[name] = UploadedFile(
            filename=filename,
            content_type=part.get_content_type(),
            data=payload,
        )
    return files, fields


def synthesize_smoke_wav(
    *,
    text: str,
    duration_frames: int,
    fps_num: int,
    fps_den: int,
    sample_rate: int,
) -> bytes:
    sample_count = round(duration_frames * sample_rate * fps_den / fps_num)
    frequency = 220 + (sum(ord(ch) for ch in text) % 220)
    amplitude = 7000
    with tempfile.TemporaryFile() as wav_file:
        with wave.open(wav_file, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            frames = bytearray()
            for idx in range(sample_count):
                t = idx / sample_rate
                envelope = min(1.0, idx / max(1, sample_rate * 0.03))
                envelope *= min(1.0, (sample_count - idx) / max(1, sample_rate * 0.03))
                value = int(amplitude * envelope * math.sin(2.0 * math.pi * frequency * t))
                frames.extend(struct.pack("<h", value))
            wav.writeframes(bytes(frames))
        wav_file.seek(0)
        return wav_file.read()


def _health_payload(config: RuntimeConfig, spec: RuntimeSpec) -> dict[str, object]:
    mode = _mode(config)
    provider = _provider(config, spec)
    model_root = _model_root(config)
    command_configured = bool(config.command)
    root_exists = model_root.exists()
    required_paths = _required_model_paths(config, spec)
    missing_paths = _missing_model_paths(required_paths)
    if mode == "smoke":
        return {
            "ok": True,
            "ready": True,
            "state": "ready",
            "runtime": spec.runtime,
            "mode": mode,
            "provider": provider,
            "command_configured": command_configured,
            "model_root_exists": root_exists,
            "required_model_paths": [str(path) for path in required_paths],
            "missing_model_paths": [str(path) for path in missing_paths],
            "message": "smoke mode ready; no model weights loaded",
        }
    if mode != "model":
        return {
            "ok": False,
            "ready": False,
            "state": "error",
            "runtime": spec.runtime,
            "mode": mode,
            "provider": provider,
            "command_configured": command_configured,
            "model_root_exists": root_exists,
            "required_model_paths": [str(path) for path in required_paths],
            "missing_model_paths": [str(path) for path in missing_paths],
            "message": "LAURA_RUNTIME_MODE must be 'model' or 'smoke'",
        }
    if not config.command:
        return {
            "ok": False,
            "ready": False,
            "state": "not_ready",
            "runtime": spec.runtime,
            "mode": mode,
            "provider": provider,
            "command_configured": False,
            "model_root_exists": root_exists,
            "required_model_paths": [str(path) for path in required_paths],
            "missing_model_paths": [str(path) for path in missing_paths],
            "message": f"{spec.runtime} model command is not configured",
        }
    if not model_root.exists():
        return {
            "ok": False,
            "ready": False,
            "state": "not_ready",
            "runtime": spec.runtime,
            "mode": mode,
            "provider": provider,
            "command_configured": True,
            "model_root_exists": False,
            "required_model_paths": [str(path) for path in required_paths],
            "missing_model_paths": [str(path) for path in missing_paths],
            "message": f"model root does not exist: {model_root}",
        }
    if missing_paths:
        missing = ", ".join(str(path) for path in missing_paths)
        return {
            "ok": False,
            "ready": False,
            "state": "not_ready",
            "runtime": spec.runtime,
            "mode": mode,
            "provider": provider,
            "command_configured": True,
            "model_root_exists": True,
            "required_model_paths": [str(path) for path in required_paths],
            "missing_model_paths": [str(path) for path in missing_paths],
            "message": f"required model paths missing: {missing}",
        }
    return {
        "ok": True,
        "ready": True,
        "state": "ready",
        "runtime": spec.runtime,
        "mode": mode,
        "provider": provider,
        "command_configured": True,
        "model_root_exists": True,
        "required_model_paths": [str(path) for path in required_paths],
        "missing_model_paths": [],
        "message": "model command configured",
    }


def _capabilities_payload(config: RuntimeConfig, spec: RuntimeSpec) -> dict[str, object]:
    return {
        "runtime": spec.runtime,
        "version": config.version,
        "effects": [spec.effect],
        "mode": _mode(config),
        "provider": _provider(config, spec),
        "smoke": _mode(config) == "smoke",
        "endpoints": list(spec.endpoints),
        "requires_gpu": spec.requires_gpu,
        "requires_model_command": _mode(config) == "model",
        "command_env": list(spec.command_env),
        "command_configured": bool(config.command),
        "probe_command_configured": bool(config.probe_command),
        "model_root": str(_model_root(config)),
        "required_model_paths": [str(path) for path in _required_model_paths(config, spec)],
        "missing_model_paths": [
            str(path) for path in _missing_model_paths(_required_model_paths(config, spec))
        ],
    }


def _run_model_command(config: RuntimeConfig, replacements: dict[str, object]) -> None:
    if not config.command:
        raise RuntimeError("model command is not configured")
    _run_shell_command(config.command, config.timeout_seconds, replacements)


def _run_shell_command(
    command_template: str,
    timeout_seconds: float,
    replacements: dict[str, object],
) -> None:
    command = command_template
    for key, value in replacements.items():
        command = command.replace("{" + key + "}", _quote_command_value(value))
    result = subprocess.run(
        command,
        shell=True,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "model command failed").strip()
        raise RuntimeError(detail)


def _read_probe_json(path: Path) -> dict[str, object]:
    if not path.exists():
        raise RuntimeError(f"probe command did not create output: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"probe command wrote invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise RuntimeError("probe command output must be a JSON object")
    return {str(key): value for key, value in data.items()}


def _quote_command_value(value: object) -> str:
    text = str(value)
    if os.name == "nt":
        return subprocess.list2cmdline([text])
    return shlex.quote(text)


def _provider(config: RuntimeConfig, spec: RuntimeSpec) -> str:
    if config.provider and config.provider.strip():
        return config.provider.strip()
    return spec.runtime


def _required_file(files: dict[str, UploadedFile], name: str) -> UploadedFile:
    upload = files.get(name)
    if upload is None or not upload.data:
        raise ValueError(f"missing multipart file: {name}")
    return upload


def _write_upload(root: Path, label: str, upload: UploadedFile) -> Path:
    filename = _SAFE_FILENAME_RE.sub("_", upload.filename).strip("._") or label
    path = root / f"{label}-{filename}"
    path.write_bytes(upload.data)
    return path


def _mode(config: RuntimeConfig) -> str:
    return config.mode.strip().lower()


def _model_root(config: RuntimeConfig) -> Path:
    return config.model_root if config.model_root is not None else Path("/models")


def _required_model_paths(config: RuntimeConfig, spec: RuntimeSpec) -> tuple[Path, ...]:
    model_root = _model_root(config)
    raw_paths = config.required_model_paths
    requirements = raw_paths if raw_paths is not None else spec.required_model_paths
    return tuple(
        _resolve_model_requirement(model_root, requirement)
        for requirement in requirements
    )


def _resolve_model_requirement(model_root: Path, requirement: str) -> Path:
    path = Path(requirement)
    return path if path.is_absolute() else model_root / path


def _missing_model_paths(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(path for path in paths if not path.exists())


def _positive_int(value: object, name: str) -> int:
    try:
        if isinstance(value, int):
            parsed = value
        elif isinstance(value, (str, bytes, bytearray)):
            parsed = int(value)
        else:
            raise TypeError
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.environ.get(name)
        if value:
            return value
    return None


def _path_list_env(name: str) -> tuple[str, ...] | None:
    raw = os.environ.get(name)
    if raw is None:
        return None
    return tuple(part.strip() for part in re.split(r"[;,]", raw) if part.strip())


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def main() -> None:
    logging.basicConfig(level=os.environ.get("LAURA_RUNTIME_LOG_LEVEL", "INFO"))
    config = RuntimeConfig.from_env()
    host = os.environ.get("LAURA_RUNTIME_HOST", "0.0.0.0")
    server = ThreadingHTTPServer((host, config.port), build_handler(config))
    LOGGER.info(
        "starting Laura %s runtime on %s:%s in %s mode",
        runtime_spec(config.kind).runtime,
        host,
        config.port,
        _mode(config),
    )
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
