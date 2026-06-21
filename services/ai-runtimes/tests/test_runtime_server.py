from __future__ import annotations

import http.client
import json
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from runtime_server import RuntimeConfig, build_handler  # noqa: E402


@contextmanager
def running_runtime(config: RuntimeConfig) -> Iterator[int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), build_handler(config))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield int(server.server_port)
    finally:
        server.shutdown()
        server.server_close()


def test_voice_smoke_health_capabilities_and_voiceover(tmp_path: Path) -> None:
    config = RuntimeConfig(kind="voice", mode="smoke", port=0, model_root=tmp_path)
    with running_runtime(config) as port:
        health = _json_request(port, "GET", "/healthz")
        caps = _json_request(port, "GET", "/capabilities")
        response = _raw_request(
            port,
            "POST",
            "/voiceover",
            body=json.dumps(
                {
                    "text": "Laura sidecar smoke",
                    "duration_frames": 24,
                    "fps_num": 24,
                    "fps_den": 1,
                    "sample_rate": 48000,
                    "language": "de",
                }
            ).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )

    assert health["ready"] is True
    assert health["mode"] == "smoke"
    assert caps["effects"] == ["voice"]
    assert caps["smoke"] is True
    assert response.status == 200
    assert response.body.startswith(b"RIFF")


def test_liveportrait_model_mode_not_ready_without_command(tmp_path: Path) -> None:
    config = RuntimeConfig(kind="liveportrait", mode="model", port=0, model_root=tmp_path)
    with running_runtime(config) as port:
        health = _json_request(port, "GET", "/healthz")
        caps = _json_request(port, "GET", "/capabilities")

    assert health["ready"] is False
    assert "command" in str(health["message"]).lower()
    assert caps["effects"] == ["reenact"]
    assert caps["runtime"] == "liveportrait"


def test_model_health_exposes_command_and_model_root_diagnostics(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"
    config = RuntimeConfig(
        kind="voice",
        mode="model",
        port=0,
        model_root=missing_root,
        command="python -m providers.piper_voice_runner --request {request_json} --output {output}",
        provider="piper",
    )
    with running_runtime(config) as port:
        health = _json_request(port, "GET", "/healthz")
        caps = _json_request(port, "GET", "/capabilities")

    assert health["ready"] is False
    assert health["provider"] == "piper"
    assert health["command_configured"] is True
    assert health["model_root_exists"] is False
    assert caps["provider"] == "piper"
    command_env = caps["command_env"]
    assert isinstance(command_env, list)
    assert "LAURA_VOICE_COMMAND" in command_env


def test_model_health_requires_runtime_specific_model_artifacts(tmp_path: Path) -> None:
    config = RuntimeConfig(
        kind="vibevideo",
        mode="model",
        port=0,
        model_root=tmp_path,
        command=f"{sys.executable} -c \"from pathlib import Path\"",
    )
    with running_runtime(config) as port:
        missing_health = _json_request(port, "GET", "/healthz")
        missing_caps = _json_request(port, "GET", "/capabilities")

    assert missing_health["ready"] is False
    assert missing_health["state"] == "not_ready"
    assert "unet.pth" in str(missing_health["message"])
    assert missing_caps["required_model_paths"] == [
        str(tmp_path / "MuseTalk" / "models" / "musetalkV15" / "unet.pth")
    ]
    assert missing_caps["missing_model_paths"] == missing_caps["required_model_paths"]

    (tmp_path / "MuseTalk" / "models" / "musetalkV15").mkdir(parents=True)
    (tmp_path / "MuseTalk" / "models" / "musetalkV15" / "unet.pth").write_bytes(b"weights")
    with running_runtime(config) as port:
        ready_health = _json_request(port, "GET", "/healthz")

    assert ready_health["ready"] is True
    assert ready_health["missing_model_paths"] == []


def test_model_required_paths_can_be_overridden(tmp_path: Path) -> None:
    config = RuntimeConfig(
        kind="liveportrait",
        mode="model",
        port=0,
        model_root=tmp_path,
        command=f"{sys.executable} -c \"from pathlib import Path\"",
        required_model_paths=("custom-ready.marker",),
    )
    with running_runtime(config) as port:
        health = _json_request(port, "GET", "/healthz")

    assert health["ready"] is False
    assert health["missing_model_paths"] == [str(tmp_path / "custom-ready.marker")]


def test_lipsync_smoke_probe_and_lipsync_contract(tmp_path: Path) -> None:
    config = RuntimeConfig(kind="vibevideo", mode="smoke", port=0, model_root=tmp_path)
    multipart, content_type = _multipart(
        files={
            "video": ("driving.mp4", b"fake video bytes", "video/mp4"),
            "audio": ("voice.wav", b"RIFFfake audio bytes", "audio/wav"),
        }
    )
    with running_runtime(config) as port:
        probe = _json_request(
            port,
            "POST",
            "/probe",
            body=multipart,
            headers={"Content-Type": content_type},
        )
        response = _raw_request(
            port,
            "POST",
            "/lipsync",
            body=multipart,
            headers={"Content-Type": content_type},
        )

    quality = json.loads(response.headers["X-Laura-Quality"])
    assert probe == {"face_detected": True, "mouth_visible": True, "audio_present": True}
    assert response.status == 200
    assert response.body == b"fake video bytes"
    assert quality["passed"] is True
    assert quality["sync_score"] >= 0.8


def test_vibevideo_model_probe_uses_probe_command(tmp_path: Path) -> None:
    probe = tmp_path / "probe.py"
    probe.write_text(
        "import json\n"
        "import pathlib\n"
        "import sys\n"
        "pathlib.Path(sys.argv[-1]).write_text(json.dumps({"
        "'face_detected': True, 'mouth_visible': False, 'audio_present': True"
        "}), encoding='utf-8')\n",
        encoding="utf-8",
    )
    config = RuntimeConfig(
        kind="vibevideo",
        mode="model",
        port=0,
        model_root=tmp_path,
        command=f"{sys.executable} -c \"from pathlib import Path\"",
        probe_command=(
            f"{sys.executable} {probe} --video {{video}} "
            "--audio {audio} --output {probe_json}"
        ),
    )
    multipart, content_type = _multipart(
        files={
            "video": ("driving.mp4", b"fake video bytes", "video/mp4"),
            "audio": ("voice.wav", b"RIFFfake audio bytes", "audio/wav"),
        }
    )
    with running_runtime(config) as port:
        probe_result = _json_request(
            port,
            "POST",
            "/probe",
            body=multipart,
            headers={"Content-Type": content_type},
        )

    assert probe_result == {
        "face_detected": True,
        "mouth_visible": False,
        "audio_present": True,
    }


class RawResponse:
    def __init__(
        self,
        *,
        status: int,
        headers: dict[str, str],
        body: bytes,
    ) -> None:
        self.status = status
        self.headers = headers
        self.body = body


def _json_request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> dict[str, object]:
    response = _raw_request(port, method, path, body=body, headers=headers)
    assert response.status == 200
    data = json.loads(response.body.decode("utf-8"))
    assert isinstance(data, dict)
    return data


def _raw_request(
    port: int,
    method: str,
    path: str,
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> RawResponse:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        request_headers = dict(headers or {})
        if body is not None:
            request_headers["Content-Length"] = str(len(body))
        conn.request(method, path, body=body, headers=request_headers)
        response = conn.getresponse()
        return RawResponse(
            status=int(response.status),
            headers={key: value for key, value in response.getheaders()},
            body=response.read(),
        )
    finally:
        conn.close()


def _multipart(
    *,
    files: dict[str, tuple[str, bytes, str]],
    fields: dict[str, str] | None = None,
) -> tuple[bytes, str]:
    boundary = "laura-test-boundary"
    chunks: list[bytes] = []
    for name, value in (fields or {}).items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode())
        chunks.append(value.encode("utf-8"))
        chunks.append(b"\r\n")
    for name, (filename, content, mime) in files.items():
        chunks.append(f"--{boundary}\r\n".encode("ascii"))
        chunks.append(
            (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {mime}\r\n\r\n"
            ).encode()
        )
        chunks.append(content)
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode("ascii"))
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"
