"""Pluggable reenact-backend interface for Laura.

Defines the ``ReenactBackend`` Protocol, a dependency-free ``StubReenactBackend``
(renders a visually-synthetic placeholder via ffmpeg), a ``LivePortraitBackend``
(HTTP sidecar adapter; no model imports), an ``UnavailableBackend`` (used for
unknown heavy-dep backends), and a resolver ``resolve_reenact_backend`` that
picks the correct implementation at runtime.

No GPU, no torch, no insightface, no liveportrait import — this module is always
safe to import regardless of the environment.
"""

from __future__ import annotations

import mimetypes
import os
import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from laura.ingest.ffmpeg import run_ffmpeg
from laura.render.reel import resolve_font

DEFAULT_LIVEPORTRAIT_URL = "http://127.0.0.1:8899"
DEFAULT_LIVEPORTRAIT_TIMEOUT_SECONDS = 300.0


@runtime_checkable
class ReenactBackend(Protocol):
    """Minimal contract that every reenact backend must satisfy."""

    name: str

    def available(self) -> bool:
        """Return True iff this backend can currently produce output."""
        ...

    def reenact(
        self,
        *,
        driving_path: Path,
        portrait_path: Path,
        out_path: Path,
        fps_num: int,
        fps_den: int,
    ) -> None:
        """Reenact a portrait with motion from *driving_path*.

        Args:
            driving_path:  Source clip whose motion/audio drives the output.
            portrait_path: Still or video portrait to animate.  The stub ignores
                           this — real backends use it.
            out_path:      Destination MP4 (will be overwritten if it exists).
            fps_num:       Output frame-rate numerator.
            fps_den:       Output frame-rate denominator.
        """
        ...


class StubReenactBackend:
    """Length-preserving synthetic placeholder — always available, never real.

    Produces an obviously-fake, desaturated MP4 with a centred yellow
    "REENACT (stub)" label so the output can never be mistaken for a genuine
    reenactment.  Useful for UI wiring and pipeline smoke-tests without any
    heavy optional dependency.

    The overlay text is written to a temporary ``*.reenact_stub.txt`` file next
    to *out_path* and passed to ffmpeg via ``textfile=<basename>`` with
    ``cwd=out_path.parent`` — the R0.8 mechanism that avoids Windows
    drive-colon escaping issues inside drawtext paths.
    """

    name: str = "stub"

    def available(self) -> bool:
        return True

    def reenact(
        self,
        *,
        driving_path: Path,
        portrait_path: Path,  # noqa: ARG002  (unused by stub by design)
        out_path: Path,
        fps_num: int,
        fps_den: int,
    ) -> None:
        label = out_path.parent / f"{out_path.stem}.reenact_stub.txt"
        label.write_text("REENACT (stub)", encoding="utf-8")
        try:
            font = resolve_font()
            vf = (
                f"eq=saturation=0.4,"
                f"drawtext=fontfile={font}:textfile={label.name}"
                f":x=(w-text_w)/2:y=h-text_h-40"
                f":fontsize=36:fontcolor=yellow:box=1:boxcolor=black@0.6"
            )
            run_ffmpeg(
                [
                    "-i", str(driving_path),
                    "-vf", vf,
                    "-r", f"{fps_num}/{fps_den}",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    str(out_path),
                ],
                cwd=out_path.parent,
            )
        finally:
            if label.exists():
                label.unlink()


class LivePortraitBackend:
    """HTTP adapter for a local LivePortrait sidecar.

    The heavy LivePortrait environment remains outside Laura's process.  This
    adapter expects a loopback service with:

    * ``GET /healthz`` -> 2xx when ready
    * ``POST /reenact`` multipart form:
      ``driving`` file, ``portrait`` file, ``fps_num``, ``fps_den``
      -> response body is the rendered MP4 bytes.

    That contract wraps LivePortrait's documented ``inference.py -s <source>
    -d <driving> -o <dir>`` entry point without making Laura import GPU/model
    libraries.
    """

    name: str = "liveportrait"

    def __init__(
        self,
        base_url: str | None = None,
        *,
        timeout_seconds: float | None = None,
    ) -> None:
        self.base_url = _normalize_base_url(
            base_url or os.environ.get("LAURA_LIVEPORTRAIT_URL") or DEFAULT_LIVEPORTRAIT_URL
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else _float_env("LAURA_LIVEPORTRAIT_TIMEOUT", DEFAULT_LIVEPORTRAIT_TIMEOUT_SECONDS)
        )

    def available(self) -> bool:
        request = Request(f"{self.base_url}/healthz", method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                return 200 <= status < 300
        except (HTTPError, OSError, TimeoutError, URLError, ValueError):
            return False

    def reenact(
        self,
        *,
        driving_path: Path,
        portrait_path: Path,
        out_path: Path,
        fps_num: int,
        fps_den: int,
    ) -> None:
        body, content_type = _multipart_body(
            fields={
                "fps_num": str(fps_num),
                "fps_den": str(fps_den),
            },
            files={
                "driving": driving_path,
                "portrait": portrait_path,
            },
        )
        request = Request(
            f"{self.base_url}/reenact",
            data=body,
            headers={
                "Content-Type": content_type,
                "Content-Length": str(len(body)),
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                status = int(response.status)
                data = response.read()
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"liveportrait sidecar failed with HTTP {exc.code}: {detail}"
            ) from exc
        except (OSError, TimeoutError, URLError) as exc:
            raise RuntimeError(f"liveportrait sidecar unavailable: {exc}") from exc

        if not 200 <= status < 300:
            raise RuntimeError(f"liveportrait sidecar failed with HTTP {status}")
        if not data:
            raise RuntimeError("liveportrait sidecar returned an empty response")

        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(data)


class UnavailableBackend:
    """Sentinel for heavy-dep backends (liveportrait, etc.) that are not installed.

    Never imports GPU or model libraries.  ``available()`` always returns False;
    ``reenact()`` always raises ``RuntimeError`` with a clear message.

    Args:
        name: Canonical backend identifier (e.g. ``"liveportrait"``).
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def available(self) -> bool:
        return False

    def reenact(
        self,
        *,
        driving_path: Path,  # noqa: ARG002
        portrait_path: Path,  # noqa: ARG002
        out_path: Path,  # noqa: ARG002
        fps_num: int,  # noqa: ARG002
        fps_den: int,  # noqa: ARG002
    ) -> None:
        raise RuntimeError(f"reenact backend '{self.name}' is not installed")


def resolve_reenact_backend(
    name: str | None = None,
    *,
    base_url: str | None = None,
) -> ReenactBackend:
    """Return the best available ``ReenactBackend`` for *name*.

    Resolution order:
    1. The *name* argument.
    2. ``LAURA_REENACT_BACKEND`` environment variable.
    3. ``"stub"`` (safe fallback).

    Returns:
        ``StubReenactBackend()``  for ``"stub"``.
        ``LivePortraitBackend()`` for ``"liveportrait"``.
        ``UnavailableBackend(chosen)`` for any other identifier (real backends
        are registered here once their optional extra exists).
    """
    chosen: str = (name or os.environ.get("LAURA_REENACT_BACKEND") or "stub").strip().lower()
    if chosen == "stub":
        return StubReenactBackend()
    if chosen == "liveportrait":
        return LivePortraitBackend(base_url=base_url)
    return UnavailableBackend(chosen)


def _normalize_base_url(value: str) -> str:
    return value.rstrip("/")


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
    fields: dict[str, str],
    files: dict[str, Path],
) -> tuple[bytes, str]:
    boundary = f"laura-{uuid.uuid4().hex}"
    chunks: list[bytes] = []

    for name, value in fields.items():
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
