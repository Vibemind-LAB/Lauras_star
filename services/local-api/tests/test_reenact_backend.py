"""Integration tests for laura.ai.reenact_backend.

The ffmpeg-dependent tests are skipped when ffmpeg/ffprobe are not on PATH.
The UnavailableBackend / resolver-default tests never need ffmpeg.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from laura.ai import reenact_backend
from laura.ai.reenact_backend import (
    ReenactBackend,
    StubReenactBackend,
    UnavailableBackend,
    resolve_reenact_backend,
)

# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _probe_frame_count(path: Path) -> int:
    """Return the number of frames in the first video stream via ffprobe."""
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=nb_frames,r_frame_rate,duration",
            "-of", "json",
            str(path),
        ],
        text=True,
    )
    data: dict[str, object] = json.loads(raw)
    streams: list[dict[str, object]] = data.get("streams", [])  # type: ignore[assignment]
    assert streams, f"no video streams in {path}"
    stream = streams[0]

    # nb_frames is sometimes absent for short clips — fall back to duration * fps
    nb = stream.get("nb_frames")
    if nb and str(nb) != "N/A":
        if isinstance(nb, int):
            return nb
        if isinstance(nb, str):
            return int(nb)

    dur_str = str(stream.get("duration", "0"))
    rate_str = str(stream.get("r_frame_rate", "25/1"))
    num_s, den_s = rate_str.split("/")
    fps = int(num_s) / int(den_s)
    return round(float(dur_str) * fps)


def _probe_has_video(path: Path) -> bool:
    raw = subprocess.check_output(
        [
            "ffprobe",
            "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "stream=codec_type",
            "-of", "json",
            str(path),
        ],
        text=True,
    )
    data: dict[str, object] = json.loads(raw)
    streams: list[dict[str, object]] = data.get("streams", [])  # type: ignore[assignment]
    return bool(streams)


class _LivePortraitHandler(BaseHTTPRequestHandler):
    """Tiny test sidecar: health-checks and echoes a deterministic MP4 payload."""

    output_bytes: bytes = b""
    last_post_body: bytes = b""
    last_content_type: str = ""

    def do_GET(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path == "/healthz":
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        if self.path != "/reenact":
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", "0"))
        type(self).last_content_type = self.headers.get("Content-Type", "")
        type(self).last_post_body = self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Type", "video/mp4")
        self.end_headers()
        self.wfile.write(type(self).output_bytes)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        return


@pytest.fixture()
def liveportrait_sidecar(driving_clip: Path) -> Generator[str, None, None]:
    _LivePortraitHandler.output_bytes = driving_clip.read_bytes()
    _LivePortraitHandler.last_post_body = b""
    _LivePortraitHandler.last_content_type = ""
    server = HTTPServer(("127.0.0.1", 0), _LivePortraitHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        raw_host = server.server_address[0]
        host = raw_host.decode("ascii") if isinstance(raw_host, bytes) else str(raw_host)
        port = int(server.server_address[1])
        yield f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


# ---------------------------------------------------------------------------
# fixture: ~1-second 320×240 driving clip (25 fps, libx264/yuv420p)
# ---------------------------------------------------------------------------

@pytest.fixture()
def driving_clip(tmp_path: Path) -> Path:
    fix = tmp_path / "driving.mp4"
    subprocess.check_call(
        [
            "ffmpeg", "-y",
            "-f", "lavfi",
            "-i", "testsrc=d=1:s=320x240:r=25",
            "-c:v", "libx264",
            "-pix_fmt", "yuv420p",
            str(fix),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return fix


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_stub_backend_name_and_available() -> None:
    b = resolve_reenact_backend("stub")
    assert b.name == "stub"
    assert b.available() is True


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_stub_reenact_produces_valid_output(
    driving_clip: Path, tmp_path: Path
) -> None:
    b = resolve_reenact_backend("stub")
    out = tmp_path / "out.mp4"

    b.reenact(
        driving_path=driving_clip,
        portrait_path=driving_clip,  # stub ignores portrait
        out_path=out,
        fps_num=25,
        fps_den=1,
    )

    # output file must exist
    assert out.exists(), "stub did not produce an output file"

    # must have at least one video stream
    assert _probe_has_video(out), "stub output has no video stream"

    # length-preserving: frame count within ±2 of the driving clip
    driving_frames = _probe_frame_count(driving_clip)
    out_frames = _probe_frame_count(out)
    assert abs(out_frames - driving_frames) <= 2, (
        f"frame count drifted too far: driving={driving_frames} out={out_frames}"
    )

    # cleanup: no *.reenact_stub.txt must remain next to the output
    leftover = list(tmp_path.glob("*.reenact_stub.txt"))
    assert leftover == [], f"stub label files leaked: {leftover}"


def test_unavailable_backend_available_is_false() -> None:
    b = resolve_reenact_backend("missing-heavy-backend")
    assert isinstance(b, UnavailableBackend)
    assert b.available() is False


def test_unavailable_backend_reenact_raises() -> None:
    b = resolve_reenact_backend("missing-heavy-backend")
    with pytest.raises(RuntimeError, match="missing-heavy-backend"):
        b.reenact(
            driving_path=Path("x.mp4"),
            portrait_path=Path("p.mp4"),
            out_path=Path("o.mp4"),
            fps_num=25,
            fps_den=1,
        )


def test_resolve_none_defaults_to_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_REENACT_BACKEND", raising=False)
    b = resolve_reenact_backend(None)
    assert isinstance(b, StubReenactBackend)
    assert b.name == "stub"


def test_protocol_structural_subtype() -> None:
    """Both concrete classes satisfy the ReenactBackend structural protocol."""
    assert isinstance(StubReenactBackend(), ReenactBackend)
    assert isinstance(UnavailableBackend("x"), ReenactBackend)


def test_resolve_liveportrait_returns_sidecar_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LAURA_REENACT_BACKEND", raising=False)
    monkeypatch.setenv("LAURA_LIVEPORTRAIT_URL", "http://127.0.0.1:9")

    backend = resolve_reenact_backend("liveportrait")

    assert hasattr(reenact_backend, "LivePortraitBackend")
    assert isinstance(backend, reenact_backend.LivePortraitBackend)
    assert backend.name == "liveportrait"


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_liveportrait_backend_health_checks_sidecar(
    liveportrait_sidecar: str,
) -> None:
    liveportrait_cls = reenact_backend.LivePortraitBackend
    backend = liveportrait_cls(liveportrait_sidecar, timeout_seconds=2.0)

    assert backend.available() is True


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
def test_liveportrait_backend_posts_media_and_writes_mp4(
    driving_clip: Path,
    liveportrait_sidecar: str,
    tmp_path: Path,
) -> None:
    liveportrait_cls = reenact_backend.LivePortraitBackend
    backend = liveportrait_cls(liveportrait_sidecar, timeout_seconds=2.0)
    out = tmp_path / "liveportrait.mp4"

    backend.reenact(
        driving_path=driving_clip,
        portrait_path=driving_clip,
        out_path=out,
        fps_num=25,
        fps_den=1,
    )

    assert out.read_bytes() == driving_clip.read_bytes()
    assert "multipart/form-data" in _LivePortraitHandler.last_content_type
    body = _LivePortraitHandler.last_post_body
    assert b'name="driving"; filename="driving.mp4"' in body
    assert b'name="portrait"; filename="driving.mp4"' in body
    assert b'name="fps_num"' in body
    assert b"\r\n25\r\n" in body
    assert b'name="fps_den"' in body
