"""Pluggable lipsync/deepfake backend interface.

The real VibeVideo/MuseTalk/Wav2Lip stack stays outside Laura's process as an
optional HTTP sidecar. This module is dependency-free and always safe to import.
"""

from __future__ import annotations

import mimetypes
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from laura.ingest.ffmpeg import run_ffmpeg
from laura.render.reel import resolve_font

DEFAULT_LIPSYNC_URL = "http://127.0.0.1:8901"
DEFAULT_LIPSYNC_TIMEOUT_SECONDS = 600.0


@dataclass(frozen=True)
class LipsyncProbe:
    face_detected: bool
    mouth_visible: bool
    audio_present: bool
    reason: str | None = None


@dataclass(frozen=True)
class LipsyncQuality:
    sync_score: float
    mouth_score: float
    temporal_score: float
    passed: bool


@runtime_checkable
class LipsyncBackend(Protocol):
    name: str

    def available(self) -> bool:
        ...

    def probe(self, *, video_path: Path, audio_path: Path) -> LipsyncProbe:
        ...

    def lipsync(
        self,
        *,
        video_path: Path,
        audio_path: Path,
        out_path: Path,
        fps_num: int,
        fps_den: int,
    ) -> LipsyncQuality:
        ...


class StubLipsyncBackend:
    """Visible, length-preserving placeholder backend for safe pipeline wiring."""

    name = "stub"

    def available(self) -> bool:
        return True

    def probe(self, *, video_path: Path, audio_path: Path) -> LipsyncProbe:
        if not video_path.exists() or video_path.stat().st_size <= 0:
            return LipsyncProbe(False, False, False, "selected range has no readable video")
        if not audio_path.exists() or audio_path.stat().st_size <= 0:
            return LipsyncProbe(True, True, False, "selected audio asset is empty")
        return LipsyncProbe(face_detected=True, mouth_visible=True, audio_present=True)

    def lipsync(
        self,
        *,
        video_path: Path,
        audio_path: Path,
        out_path: Path,
        fps_num: int,
        fps_den: int,
    ) -> LipsyncQuality:
        label = out_path.parent / f"{out_path.stem}.lipsync_stub.txt"
        label.write_text("LIPSYNC (stub)", encoding="utf-8")
        try:
            font = resolve_font()
            vf = (
                f"eq=saturation=0.55,"
                f"drawtext=fontfile={font}:textfile={label.name}"
                f":x=(w-text_w)/2:y=h-text_h-40"
                f":fontsize=34:fontcolor=cyan:box=1:boxcolor=black@0.65"
            )
            run_ffmpeg(
                [
                    "-i", str(video_path),
                    "-i", str(audio_path),
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                    "-vf", vf,
                    "-r", f"{fps_num}/{fps_den}",
                    "-shortest",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    "-c:a", "aac",
                    str(out_path),
                ],
                cwd=out_path.parent,
            )
        finally:
            label.unlink(missing_ok=True)
        return LipsyncQuality(sync_score=0.92, mouth_score=0.88, temporal_score=0.9, passed=True)


class VibeVideoLipsyncBackend:
    """HTTP adapter for a local VibeVideo/MuseTalk/Wav2Lip-style sidecar."""

    name = "vibevideo"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = (
            base_url or os.environ.get("LAURA_LIPSYNC_URL") or DEFAULT_LIPSYNC_URL
        ).rstrip("/")
        self.timeout_seconds = timeout_seconds or _float_env(
            "LAURA_LIPSYNC_TIMEOUT",
            DEFAULT_LIPSYNC_TIMEOUT_SECONDS,
        )

    def available(self) -> bool:
        request = Request(f"{self.base_url}/healthz", method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return 200 <= int(response.status) < 300
        except (HTTPError, OSError, TimeoutError, URLError, ValueError):
            return False

    def probe(self, *, video_path: Path, audio_path: Path) -> LipsyncProbe:
        body, content_type = _multipart_body(files={"video": video_path, "audio": audio_path})
        request = Request(
            f"{self.base_url}/probe",
            data=body,
            headers={"Content-Type": content_type, "Content-Length": str(len(body))},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                data = response.read().decode("utf-8", errors="replace")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"lipsync sidecar probe failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise RuntimeError(f"lipsync sidecar unavailable: {exc}") from exc
        return _probe_from_json(data)

    def lipsync(
        self,
        *,
        video_path: Path,
        audio_path: Path,
        out_path: Path,
        fps_num: int,
        fps_den: int,
    ) -> LipsyncQuality:
        body, content_type = _multipart_body(
            fields={"fps_num": str(fps_num), "fps_den": str(fps_den)},
            files={"video": video_path, "audio": audio_path},
        )
        request = Request(
            f"{self.base_url}/lipsync",
            data=body,
            headers={"Content-Type": content_type, "Content-Length": str(len(body))},
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                raw_quality = response.headers.get("X-Laura-Quality")
                data = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"lipsync sidecar failed with HTTP {exc.code}: {detail}") from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise RuntimeError(f"lipsync sidecar unavailable: {exc}") from exc
        if not data:
            raise RuntimeError("lipsync sidecar returned an empty response")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)
        return _quality_from_json(raw_quality)


class UnavailableLipsyncBackend:
    def __init__(self, name: str) -> None:
        self.name = name

    def available(self) -> bool:
        return False

    def probe(self, *, video_path: Path, audio_path: Path) -> LipsyncProbe:  # noqa: ARG002
        raise RuntimeError(f"lipsync backend '{self.name}' is not installed")

    def lipsync(
        self,
        *,
        video_path: Path,
        audio_path: Path,
        out_path: Path,
        fps_num: int,
        fps_den: int,
    ) -> LipsyncQuality:
        raise RuntimeError(f"lipsync backend '{self.name}' is not installed")


def resolve_lipsync_backend(
    name: str | None = None,
    *,
    base_url: str | None = None,
) -> LipsyncBackend:
    chosen = (name or os.environ.get("LAURA_LIPSYNC_BACKEND") or "stub").strip().lower()
    if chosen == "stub":
        return StubLipsyncBackend()
    if chosen in {"vibevideo", "sidecar", "wav2lip", "musetalk"}:
        return VibeVideoLipsyncBackend(base_url=base_url)
    return UnavailableLipsyncBackend(chosen)


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        parsed = float(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


def _multipart_body(
    *,
    files: dict[str, Path],
    fields: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    boundary = f"laura-{uuid.uuid4().hex}"
    chunks: list[bytes] = []
    for name, value in (fields or {}).items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    for name, path in files.items():
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; '
                f'filename="{path.name}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode()
        )
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


def _probe_from_json(raw: str) -> LipsyncProbe:
    import json

    data = json.loads(raw or "{}")
    return LipsyncProbe(
        face_detected=bool(data.get("face_detected")),
        mouth_visible=bool(data.get("mouth_visible")),
        audio_present=bool(data.get("audio_present")),
        reason=data.get("reason") if isinstance(data.get("reason"), str) else None,
    )


def _quality_from_json(raw: str | None) -> LipsyncQuality:
    import json

    data = json.loads(raw or "{}")
    sync = float(data.get("sync_score", 0.75))
    mouth = float(data.get("mouth_score", 0.75))
    temporal = float(data.get("temporal_score", 0.75))
    passed = bool(data.get("passed", True))
    return LipsyncQuality(
        sync_score=sync,
        mouth_score=mouth,
        temporal_score=temporal,
        passed=passed,
    )
