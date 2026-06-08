"""yt-dlp URL ingest: routing heuristic, option building, and handler dispatch.

No network and no real yt-dlp invocation — ``yt_dlp.YoutubeDL`` is monkeypatched so we
assert the options Laura builds (format/merge/ffmpeg_location/outtmpl) and the returned
path, plus that the fetch handler routes a site URL to yt-dlp and a ``.mp4`` link to the
direct downloader.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from laura.ingest import ytdlp
from laura.ingest.ytdlp import (
    download_via_ytdlp,
    needs_ytdlp,
    ytdlp_available,
)


# --- routing heuristic ----------------------------------------------------
@pytest.mark.parametrize("url", [
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
    "https://youtu.be/dQw4w9WgXcQ",
    "https://m.youtube.com/watch?v=abc",
    "https://drive.google.com/file/d/abc/view",
    "https://vimeo.com/123456",
    "https://www.dailymotion.com/video/x123",
    "https://x.com/user/status/123",
    "https://www.tiktok.com/@u/video/123",
    "https://example.com/watch?v=novideoextension",  # no media extension -> page
])
def test_needs_ytdlp_true_for_sites_and_pages(url: str) -> None:
    assert needs_ytdlp(url) is True


@pytest.mark.parametrize("url", [
    "https://x/y.mp4",
    "https://example.com/clip.mov",
    "http://cdn.example.com/path/to/file.mkv",
    "https://example.com/a.webm",
    "https://example.com/audio.m4a",
])
def test_needs_ytdlp_false_for_direct_media(url: str) -> None:
    assert needs_ytdlp(url) is False


def test_needs_ytdlp_false_for_non_http() -> None:
    # magnet/ftp are the aria2 engine's job, not yt-dlp's.
    assert needs_ytdlp("magnet:?xt=urn:btih:deadbeef") is False
    assert needs_ytdlp("ftp://example.com/a.mp4") is False


def test_ytdlp_available_true() -> None:
    # yt-dlp is installed in this environment (the [fetch] extra).
    assert ytdlp_available() is True


# --- option building + return path (mocked yt_dlp.YoutubeDL) --------------
class _FakeYDL:
    """Stand-in for yt_dlp.YoutubeDL that records the options it was built with and
    fakes a download by touching the file prepare_filename() would produce."""

    last_opts: dict[str, Any] = {}

    def __init__(self, opts: dict[str, Any]) -> None:
        type(self).last_opts = opts
        self._opts = opts

    def __enter__(self) -> _FakeYDL:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def extract_info(self, url: str, download: bool = True) -> dict[str, Any]:
        self._info = {"id": "vid123", "title": "Clip", "ext": "mp4"}
        return self._info

    def prepare_filename(self, info: dict[str, Any]) -> str:
        outtmpl = self._opts["outtmpl"]
        dest_dir = Path(outtmpl).parent
        path = dest_dir / "Clip [vid123].mp4"
        path.write_bytes(b"fake media bytes")
        return str(path)


def test_download_via_ytdlp_builds_options_and_returns_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)

    progress: list[tuple[int, int | None]] = []
    out = download_via_ytdlp(
        "https://www.youtube.com/watch?v=vid123",
        tmp_path,
        on_progress=lambda d, t: progress.append((d, t)),
        ffmpeg_dir=r"C:\ff\bin",
    )

    assert out == tmp_path / "Clip [vid123].mp4"
    assert out.exists()

    opts = _FakeYDL.last_opts
    assert opts["format"] == "bv*+ba/b"
    assert opts["merge_output_format"] == "mp4"
    assert opts["noplaylist"] is True
    assert opts["ffmpeg_location"] == r"C:\ff\bin"
    assert str(tmp_path) in opts["outtmpl"]
    assert callable(opts["progress_hooks"][0])


def test_download_via_ytdlp_progress_hook_maps_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    seen: list[tuple[int, int | None]] = []
    download_via_ytdlp(
        "https://youtu.be/x", tmp_path,
        on_progress=lambda d, t: seen.append((d, t)),
    )
    # Drive the recorded hook the way yt-dlp would.
    hook = _FakeYDL.last_opts["progress_hooks"][0]
    hook({"status": "downloading", "downloaded_bytes": 10, "total_bytes": 100})
    hook({"status": "downloading", "downloaded_bytes": 50,
          "total_bytes_estimate": 200})  # no exact total -> estimate
    hook({"status": "finished"})  # ignored
    assert seen == [(10, 100), (50, 200)]


def test_download_via_ytdlp_missing_file_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _NoFileYDL(_FakeYDL):
        def prepare_filename(self, info: dict[str, Any]) -> str:
            # Report a path but never create it.
            return str(Path(self._opts["outtmpl"]).parent / "ghost.mp4")

    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _NoFileYDL)
    with pytest.raises(RuntimeError, match="produced no file"):
        download_via_ytdlp("https://youtu.be/x", tmp_path)


def test_ffmpeg_dir_derives_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAURA_FFMPEG", str(Path("/opt/ff/bin/ffmpeg")))
    assert ytdlp._ffmpeg_dir() == str(Path("/opt/ff/bin"))
    monkeypatch.delenv("LAURA_FFMPEG", raising=False)
    assert ytdlp._ffmpeg_dir() is None


# --- handler-level routing (mock both downloaders, no ffmpeg/network) ------
def _make_asset_env(tmp_path: Path) -> tuple[Any, dict[str, Any]]:
    from laura.config import Settings
    from laura.db import repos
    from laura.db.database import SqliteDatabase

    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    project_root = settings.workspace_root / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(project_root),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="clip.mp4", source_path="url:pending", online=False,
    )
    return db, asset


def _run_fetch(db: Any, asset: dict[str, Any], url: str) -> str:
    from laura.ingest.handlers import register_ingest_handlers
    from laura.jobs import JobRunner, default_registry, enqueue

    registry = default_registry()
    register_ingest_handlers(registry)
    runner = JobRunner(db, registry)
    enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": asset["id"], "source_url": url},
        idempotency_key=f"fetch:{asset['id']}", max_attempts=1,
    )
    while runner.run_once():
        pass
    from laura.db import repos

    a = repos.get_asset(db, asset["id"])
    assert a is not None
    source_path: str = a["source_path"]
    return source_path


def test_handler_routes_youtube_to_ytdlp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, asset = _make_asset_env(tmp_path)
    import laura.ingest.handlers as h

    calls: dict[str, int] = {"ytdlp": 0, "resumable": 0}
    media = tmp_path / "downloaded.mp4"
    media.write_bytes(b"fake")

    def _fake_ytdlp(url: str, dest_dir: Any, **kw: Any) -> Path:
        calls["ytdlp"] += 1
        return media

    def _fake_resumable(url: str, dest: Any, **kw: Any) -> None:
        calls["resumable"] += 1

    monkeypatch.setattr(h, "ytdlp_available", lambda: True)
    monkeypatch.setattr(h, "download_via_ytdlp", _fake_ytdlp)
    monkeypatch.setattr(h, "download_resumable", _fake_resumable)
    # Skip real ffmpeg verification + probe enqueue side effects.
    monkeypatch.setattr(h, "_finalize_media_asset", lambda ctx, a, m, **kw: True)

    _run_fetch(db, asset, "https://www.youtube.com/watch?v=abc")
    assert calls == {"ytdlp": 1, "resumable": 0}


def test_handler_routes_direct_mp4_to_resumable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, asset = _make_asset_env(tmp_path)
    import laura.ingest.handlers as h
    from laura.ingest.integrity import IntegrityReport

    calls: dict[str, int] = {"ytdlp": 0, "resumable": 0}

    def _fake_ytdlp(url: str, dest_dir: Any, **kw: Any) -> Path:
        calls["ytdlp"] += 1
        return tmp_path / "x.mp4"

    def _fake_resumable(url: str, dest: Any, **kw: Any) -> None:
        calls["resumable"] += 1
        Path(dest).parent.mkdir(parents=True, exist_ok=True)
        Path(dest).write_bytes(b"fake")

    monkeypatch.setattr(h, "ytdlp_available", lambda: True)
    monkeypatch.setattr(h, "download_via_ytdlp", _fake_ytdlp)
    monkeypatch.setattr(h, "download_resumable", _fake_resumable)
    # A direct .mp4 takes the httpx path; stub the integrity check to pass.
    monkeypatch.setattr(
        h, "verify_decode",
        lambda p, **kw: IntegrityReport(
            ok=True, container_ok=True, decode_errors=0, detail="ok"
        ),
    )

    src = _run_fetch(db, asset, "https://cdn.example.com/clip.mp4")
    assert calls == {"ytdlp": 0, "resumable": 1}
    assert src.endswith("clip.mp4")
