# Resilient URL Ingest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "import from URL" ingest path that downloads large media resiliently (resumable HTTP) and verifies it decodes cleanly before handing it to the existing probe pipeline.

**Architecture:** A single `ingest.fetch` job downloads to `<dest>.part` (resuming via HTTP `Range`), verifies the result (container probe + ffmpeg decode scan), then chains to the existing `ingest.probe`. The job runs with `max_attempts=5`: a download interruption keeps `.part` for resume; a verify failure discards the file so the next attempt re-downloads in full ("auto-retry, then mark corrupt").

**Tech Stack:** Python 3.11, `httpx` (already a runtime dep), `ffmpeg`/`ffprobe` wrappers, the existing job runner, FastAPI, `pytest`.

**Working directory for all commands:** `services/local-api`. Tests run with `uv run pytest`.

**Spec:** [`docs/superpowers/specs/2026-06-03-resilient-url-ingest-design.md`](../specs/2026-06-03-resilient-url-ingest-design.md)

---

## File Structure

- Create: `services/local-api/src/laura/ingest/download.py` — resumable HTTP(S) download + size/hash verify.
- Create: `services/local-api/src/laura/ingest/integrity.py` — `verify_decode()` (container check + decode scan).
- Modify: `services/local-api/src/laura/ingest/ffmpeg.py` — add `decode_scan()`.
- Modify: `services/local-api/src/laura/db/repos.py` — add `set_asset_source()`.
- Modify: `services/local-api/src/laura/ingest/handlers.py` — add `handle_fetch`, register `ingest.fetch`.
- Modify: `services/local-api/src/laura/jobs/queues.py` — route `ingest.fetch` to `ingest.io`.
- Modify: `services/local-api/src/laura/api/models.py` — `AssetImport.source_url` + validator.
- Modify: `services/local-api/src/laura/api/assets.py` — URL branch in `import_asset`.
- Create: `services/local-api/tests/_flaky_http.py` — shared flaky HTTP server for tests.
- Create: `services/local-api/tests/test_download.py`
- Create: `services/local-api/tests/test_integrity.py`
- Create: `services/local-api/tests/test_fetch.py`
- Modify: `services/local-api/tests/test_api_assets.py` — URL-import + validator cases.
- Modify: `docs/06-storage.md` — `workspace/downloads/` layout + Drive/manual-test note.

---

## Task 1: Shared flaky HTTP server for tests

**Files:**
- Create: `services/local-api/tests/_flaky_http.py`

- [ ] **Step 1: Write the helper module**

```python
"""A tiny threaded HTTP server for download tests.

Supports HTTP ``Range`` requests and can cut the connection after a fixed number of
bytes on the *first* full-file response, so a test can prove that a resumed download
completes. Not a fixture — a context manager that yields the URL.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


@contextmanager
def serve(content: bytes, *, cut_after: int | None = None) -> Iterator[str]:
    """Serve ``content`` at the yielded URL.

    If ``cut_after`` is set, the first request that starts at offset 0 writes only
    ``cut_after`` bytes and then drops the connection (simulating a flaky link). Any
    later request — including the Range request a resume sends — is served in full.
    """
    state = {"cut_used": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # silence test noise
            pass

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            rng = self.headers.get("Range")
            start = 0
            if rng and rng.startswith("bytes="):
                start = int(rng.split("=", 1)[1].split("-", 1)[0])
            body = content[start:]
            do_cut = cut_after is not None and start == 0 and not state["cut_used"]

            if start > 0:
                self.send_response(206)
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}"
                )
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

            if do_cut:
                self.wfile.write(body[: cut_after or 0])
                state["cut_used"] = True
                self.close_connection = True  # short read -> client errors out
                return
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/file"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
```

- [ ] **Step 2: Commit**

```bash
git add services/local-api/tests/_flaky_http.py
git commit -m "$(printf 'test: flaky HTTP server helper for download tests\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 2: Resumable download (`download.py`)

**Files:**
- Create: `services/local-api/src/laura/ingest/download.py`
- Test: `services/local-api/tests/test_download.py`

- [ ] **Step 1: Write the failing tests**

```python
"""Unit tests for the resumable downloader against a local flaky HTTP server."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from laura.ingest.download import DownloadError, download_resumable

from ._flaky_http import serve

CONTENT = b"laura-resilient-ingest-" * 4096  # ~94 KiB, deterministic


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def test_simple_download_succeeds(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    with serve(CONTENT) as url:
        result = download_resumable(url, dest)
    assert dest.read_bytes() == CONTENT
    assert result.size_bytes == len(CONTENT)
    assert result.sha256 == _sha(CONTENT)
    assert not dest.with_name(dest.name + ".part").exists()


def test_cut_connection_then_resume(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    part = dest.with_name(dest.name + ".part")
    with serve(CONTENT, cut_after=10_000) as url:
        # first attempt: server drops the connection partway through
        with pytest.raises(DownloadError):
            download_resumable(url, dest)
        assert part.exists() and 0 < part.stat().st_size < len(CONTENT)
        assert not dest.exists()  # not promoted until verified-complete

        # second attempt: resumes from .part and completes
        result = download_resumable(url, dest)
    assert dest.read_bytes() == CONTENT
    assert result.sha256 == _sha(CONTENT)


def test_sha256_mismatch_raises(tmp_path: Path) -> None:
    dest = tmp_path / "out.bin"
    with serve(CONTENT) as url:
        with pytest.raises(DownloadError, match="sha256"):
            download_resumable(url, dest, expected_sha256="00" * 32)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_download.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'laura.ingest.download'`

- [ ] **Step 3: Implement `download.py`**

```python
"""Resumable HTTP(S) download with size/hash verification.

Streams to ``<dest>.part`` and resumes from an existing partial file via a Range
request, so an interrupted download is retried by simply re-running. Only after the
final size (and optional SHA-256) check passes is the file atomically promoted to
``dest``. Generic HTTP(S) only — for Google Drive pass a direct ``googleusercontent``
link (no confirm-token handling here).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from .probe import sha256_file


class DownloadError(RuntimeError):
    """Raised when the download cannot complete or fails verification."""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size_bytes: int
    sha256: str


def _expected_total(resp: httpx.Response, resume_from: int) -> int | None:
    content_range = resp.headers.get("Content-Range")
    if content_range and "/" in content_range:
        tail = content_range.rsplit("/", 1)[1].strip()
        if tail.isdigit():
            return int(tail)
    length = resp.headers.get("Content-Length")
    if length and length.isdigit():
        # 206: Content-Length is the remaining range; 200: it is the whole file.
        return resume_from + int(length) if resp.status_code == 206 else int(length)
    return None


def download_resumable(
    url: str,
    dest: Path | str,
    *,
    expected_sha256: str | None = None,
    chunk_bytes: int = 1 << 20,
    timeout: float = 30.0,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> DownloadResult:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")

    resume_from = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={resume_from}-"} if resume_from else {}
    total: int | None = None

    try:
        with httpx.Client(follow_redirects=True, timeout=timeout) as client:
            with client.stream("GET", url, headers=headers) as resp:
                if resume_from and resp.status_code == 200:
                    resume_from = 0  # server ignored Range -> restart cleanly
                elif resp.status_code not in (200, 206):
                    raise DownloadError(f"unexpected status {resp.status_code} for {url}")
                total = _expected_total(resp, resume_from)
                downloaded = resume_from
                with open(part, "ab" if resume_from else "wb") as fh:
                    for chunk in resp.iter_bytes(chunk_bytes):
                        fh.write(chunk)
                        downloaded += len(chunk)
                        if on_progress is not None:
                            on_progress(downloaded, total)
    except httpx.HTTPError as exc:
        raise DownloadError(f"transport error for {url}: {exc}") from exc

    size = part.stat().st_size
    if total is not None and size != total:
        raise DownloadError(f"size mismatch: got {size}, expected {total}")

    sha = sha256_file(part)
    if expected_sha256 is not None and sha.lower() != expected_sha256.lower():
        raise DownloadError(f"sha256 mismatch: got {sha}, expected {expected_sha256}")

    os.replace(part, dest)
    return DownloadResult(path=dest, size_bytes=size, sha256=sha)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_download.py -v`
Expected: PASS (3 passed)

- [ ] **Step 5: Typecheck + lint**

Run: `uv run mypy src/laura/ingest/download.py && uv run ruff check src/laura/ingest/download.py`
Expected: no errors

- [ ] **Step 6: Commit**

```bash
git add src/laura/ingest/download.py tests/test_download.py
git commit -m "$(printf 'feat(ingest): resumable HTTP download with size/hash verify\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 3: Decode scan + integrity report (`integrity.py`)

**Files:**
- Modify: `services/local-api/src/laura/ingest/ffmpeg.py` (add `decode_scan`)
- Create: `services/local-api/src/laura/ingest/integrity.py`
- Test: `services/local-api/tests/test_integrity.py`

- [ ] **Step 1: Write the failing test**

```python
"""Integrity verification with REAL ffmpeg. Skipped if ffmpeg is unavailable."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from laura.ingest.ffmpeg import run_ffmpeg
from laura.ingest.integrity import verify_decode

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="ffmpeg not available on PATH",
)


@pytest.fixture
def sample(tmp_path: Path) -> Path:
    out = tmp_path / "sample.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out),
    ])
    return out


def test_clean_file_passes(sample: Path) -> None:
    report = verify_decode(sample)
    assert report.ok is True
    assert report.container_ok is True
    assert report.decode_errors == 0


def test_truncated_file_is_flagged(sample: Path, tmp_path: Path) -> None:
    truncated = tmp_path / "broken.mp4"
    data = sample.read_bytes()
    truncated.write_bytes(data[: len(data) // 2])  # chop the file in half
    report = verify_decode(truncated)
    assert report.ok is False


def test_skip_decode_scan_only_checks_container(sample: Path) -> None:
    report = verify_decode(sample, full_scan=False)
    assert report.ok is True
    assert "skipped" in report.detail
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_integrity.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'laura.ingest.integrity'`

- [ ] **Step 3: Add `decode_scan` to `ffmpeg.py`**

Append to `services/local-api/src/laura/ingest/ffmpeg.py`:

```python
def decode_scan(path: Path | str) -> int:
    """Full decode pass; returns the count of decode-error lines ffmpeg emitted.

    ``ffmpeg -v error -xerror -i <path> -f null -`` decodes every frame and prints one
    line per decode error. ``-xerror`` makes it bail on the first error, so a clean file
    decodes fully while a corrupt one returns quickly. Zero => clean.
    """
    cmd = [
        ffmpeg_bin(), "-hide_banner", "-nostdin", "-v", "error", "-xerror",
        "-i", str(path), "-f", "null", "-",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)  # noqa: S603
    except FileNotFoundError as exc:
        raise FFmpegError(f"ffmpeg not found: {ffmpeg_bin()}") from exc
    stderr = (proc.stderr or "").strip()
    return len([line for line in stderr.splitlines() if line.strip()])
```

- [ ] **Step 4: Implement `integrity.py`**

```python
"""Detect corrupt / incomplete media before it enters the editorial pipeline.

A cheap container probe catches truncated/garbled containers; an optional full
decode scan catches broken frames mid-stream. Returns a structured report rather
than raising, so the caller decides what to do.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .ffmpeg import FFmpegError, decode_scan
from .ffmpeg import probe as ffprobe


@dataclass(frozen=True)
class IntegrityReport:
    ok: bool
    container_ok: bool
    decode_errors: int
    detail: str


def verify_decode(path: Path | str, *, full_scan: bool = True) -> IntegrityReport:
    path = Path(path)
    try:
        ffprobe(path)
    except FFmpegError as exc:
        return IntegrityReport(
            ok=False, container_ok=False, decode_errors=0,
            detail=f"container unreadable: {exc}",
        )
    if not full_scan:
        return IntegrityReport(
            ok=True, container_ok=True, decode_errors=0,
            detail="container ok (decode scan skipped)",
        )
    errors = decode_scan(path)
    if errors:
        return IntegrityReport(
            ok=False, container_ok=True, decode_errors=errors,
            detail=f"{errors} decode error(s)",
        )
    return IntegrityReport(ok=True, container_ok=True, decode_errors=0, detail="ok")
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_integrity.py -v`
Expected: PASS (3 passed)

- [ ] **Step 6: Typecheck + lint**

Run: `uv run mypy src/laura/ingest/integrity.py src/laura/ingest/ffmpeg.py && uv run ruff check src/laura/ingest/integrity.py src/laura/ingest/ffmpeg.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/laura/ingest/ffmpeg.py src/laura/ingest/integrity.py tests/test_integrity.py
git commit -m "$(printf 'feat(ingest): decode-scan integrity check for downloaded media\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 4: Repo helper `set_asset_source`

**Files:**
- Modify: `services/local-api/src/laura/db/repos.py`

- [ ] **Step 1: Add the function after `update_asset_probe` (around line 187)**

```python
def set_asset_source(
    db: Database, asset_id: str, *, source_path: str, online: bool
) -> None:
    """Point an asset at its (now local) source file and flip its online flag.

    Used by the URL-ingest fetch stage once the download is verified complete.
    """
    with db.transaction() as conn:
        conn.execute(
            "UPDATE media_assets SET source_path=?, online=? WHERE id=?",
            (source_path, int(online), asset_id),
        )
```

- [ ] **Step 2: Typecheck**

Run: `uv run mypy src/laura/db/repos.py`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
git add src/laura/db/repos.py
git commit -m "$(printf 'feat(db): set_asset_source to relink an asset after download\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 5: Fetch handler + queue wiring

**Files:**
- Modify: `services/local-api/src/laura/ingest/handlers.py`
- Modify: `services/local-api/src/laura/jobs/queues.py`
- Test: `services/local-api/tests/test_fetch.py`

- [ ] **Step 1: Write the failing integration test**

```python
"""End-to-end: ingest.fetch over a flaky server -> resume -> verify -> probe.

Uses REAL ffmpeg/ffprobe. Skipped if ffmpeg is unavailable.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.ingest.handlers import register_ingest_handlers
from laura.jobs import JobRunner, default_registry, enqueue

from ._flaky_http import serve

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None,
    reason="ffmpeg not available on PATH",
)


def _drain(runner: JobRunner, limit: int = 60) -> int:
    ran = 0
    while runner.run_once():
        ran += 1
        if ran >= limit:
            break
    return ran


def test_fetch_resumes_then_probes(tmp_path: Path) -> None:
    media = tmp_path / "sample.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=30",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media),
    ])
    content = media.read_bytes()

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
        display_name="sample.mp4", source_path="url:pending", online=False,
    )

    registry = default_registry()
    register_ingest_handlers(registry)
    runner = JobRunner(db, registry)

    with serve(content, cut_after=len(content) // 2) as url:
        enqueue(
            db, queue="ingest.io", kind="ingest.fetch",
            payload={"asset_id": asset["id"], "source_url": url},
            idempotency_key=f"fetch:{asset['id']}", max_attempts=5,
        )
        _drain(runner)

    a = repos.get_asset(db, asset["id"])
    assert a is not None
    assert a["online"] == 1                      # fetch promoted it
    assert Path(a["source_path"]).exists()       # local file now
    assert (a["width"], a["height"]) == (320, 240)  # probe ran afterwards
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_fetch.py -v`
Expected: FAIL — `ingest.fetch` has no handler (`KeyError`/job failure), asset stays `online=0`.

- [ ] **Step 3: Add `handle_fetch` to `handlers.py`**

Add these imports near the top of `services/local-api/src/laura/ingest/handlers.py` (it already imports `os`, `Path`, `Any`):

```python
import json
from dataclasses import asdict

from .download import download_resumable
from .integrity import verify_decode
```

Add the handler (place it before `register_ingest_handlers`):

```python
def handle_fetch(ctx: JobContext) -> dict[str, Any]:
    asset = _require_asset(ctx)
    url = ctx.payload["source_url"]
    full_scan = bool(ctx.payload.get("full_scan", True))
    root = _project_root(ctx.db, asset)
    filename = Path(asset["display_name"]).name or "download.bin"
    dest = root / "downloads" / asset["id"] / filename

    # Download stage: on failure the .part file remains so a retry resumes.
    download_resumable(url, dest, on_progress=lambda _d, _t: ctx.heartbeat())

    # Verify stage: on failure discard the file so the retry re-downloads in full.
    report = verify_decode(dest, full_scan=full_scan)
    if not report.ok:
        report_path = dest.parent / "integrity.json"
        report_path.write_text(json.dumps(asdict(report), indent=2), encoding="utf-8")
        repos.add_asset_file(
            ctx.db, asset_id=asset["id"], kind="integrity",
            path=str(report_path), size_bytes=report_path.stat().st_size,
        )
        dest.unlink(missing_ok=True)
        (dest.with_name(dest.name + ".part")).unlink(missing_ok=True)
        raise ValueError(f"integrity check failed: {report.detail}")

    repos.set_asset_source(ctx.db, asset["id"], source_path=str(dest), online=True)
    enqueue(
        ctx.db, queue="ingest.io", kind="ingest.probe",
        payload={"asset_id": asset["id"]}, idempotency_key=f"probe:{asset['id']}",
        caused_by_job_id=ctx.job_id,
    )
    return {"asset_id": asset["id"], "downloaded": str(dest), "size_bytes": os.path.getsize(dest)}
```

Then register it in `register_ingest_handlers`:

```python
    registry["ingest.fetch"] = handle_fetch
```

- [ ] **Step 4: Route the queue in `queues.py`**

In `services/local-api/src/laura/jobs/queues.py`, add to the `_STAGE_QUEUE` dict (next to `"ingest.probe": QUEUE_INGEST`):

```python
    "ingest.fetch": QUEUE_INGEST,
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `uv run pytest tests/test_fetch.py -v`
Expected: PASS (1 passed) — first fetch attempt fails on the cut, the retry resumes, verify passes, probe runs, asset is online with dimensions.

- [ ] **Step 6: Typecheck + lint**

Run: `uv run mypy src/laura/ingest/handlers.py src/laura/jobs/queues.py && uv run ruff check src/laura/ingest/handlers.py src/laura/jobs/queues.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/laura/ingest/handlers.py src/laura/jobs/queues.py tests/test_fetch.py
git commit -m "$(printf 'feat(ingest): ingest.fetch job — resumable download + verify, then probe\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 6: API — import from URL

**Files:**
- Modify: `services/local-api/src/laura/api/models.py`
- Modify: `services/local-api/src/laura/api/assets.py`
- Test: `services/local-api/tests/test_api_assets.py`

- [ ] **Step 1: Write the failing tests**

Append to `services/local-api/tests/test_api_assets.py` (it already uses the `client` fixture; reuse its existing project-creation helper/pattern — create a project inline as the other tests in that file do):

```python
def test_import_from_url_queues_fetch(client: TestClient) -> None:
    project = client.post(
        "/projects", json={"name": "p", "rate_num": 30, "rate_den": 1, "drop_frame": False}
    ).json()
    resp = client.post(
        f"/projects/{project['id']}/assets/import",
        json={"source_url": "http://example.invalid/big.mp4"},
    )
    assert resp.status_code == 202
    asset_id = resp.json()["asset_id"]

    asset = client.get(f"/projects/{project['id']}/assets/{asset_id}").json()
    assert asset["online"] is False
    assert asset["display_name"] == "big.mp4"


def test_import_rejects_both_sources(client: TestClient) -> None:
    project = client.post(
        "/projects", json={"name": "p", "rate_num": 30, "rate_den": 1, "drop_frame": False}
    ).json()
    resp = client.post(
        f"/projects/{project['id']}/assets/import",
        json={"source_path": "/tmp/x.mp4", "source_url": "http://example.invalid/x.mp4"},
    )
    assert resp.status_code == 422


def test_import_rejects_no_source(client: TestClient) -> None:
    project = client.post(
        "/projects", json={"name": "p", "rate_num": 30, "rate_den": 1, "drop_frame": False}
    ).json()
    resp = client.post(f"/projects/{project['id']}/assets/import", json={})
    assert resp.status_code == 422
```

> NOTE: If the project-creation or single-asset-GET endpoints in this file use different
> paths/payloads, mirror the exact form already used by the existing tests in
> `test_api_assets.py` rather than the placeholders above.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_api_assets.py -k "url or source" -v`
Expected: FAIL — `source_url` is rejected as an unknown field / no validator yet.

- [ ] **Step 3: Update `AssetImport` in `models.py`**

Replace the existing `AssetImport` class with:

```python
class AssetImport(BaseModel):
    source_path: str | None = Field(default=None, min_length=1)
    source_url: str | None = Field(default=None, min_length=1)
    display_name: str | None = None

    @model_validator(mode="after")
    def _exactly_one_source(self) -> "AssetImport":
        if bool(self.source_path) == bool(self.source_url):
            raise ValueError("provide exactly one of source_path or source_url")
        return self
```

Add `model_validator` to the pydantic import at the top of the file:

```python
from pydantic import BaseModel, Field, model_validator
```

- [ ] **Step 4: Add the URL branch in `import_asset` (`assets.py`)**

Add the import near the top of `services/local-api/src/laura/api/assets.py`:

```python
from pathlib import Path
```

(`Path` is likely already imported — skip if so.) Then, inside `import_asset`, immediately after the `get_project` 404 check, insert:

```python
    if body.source_url:
        name = body.display_name or Path(body.source_url.split("?", 1)[0]).name or "download.bin"
        asset = repos.create_asset(
            db, project_id=project_id, type="video",
            display_name=name, source_path=f"url:{body.source_url}", online=False,
        )
        job_id = enqueue(
            db, queue="ingest.io", kind="ingest.fetch",
            payload={"asset_id": asset["id"], "source_url": body.source_url},
            idempotency_key=f"fetch:{asset['id']}", max_attempts=5,
        )
        return ImportAccepted(asset_id=asset["id"], job_id=job_id)

    assert body.source_path is not None  # guaranteed by AssetImport validator
```

The existing `src = Path(body.source_path)` line and everything below it stays unchanged.

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_api_assets.py -v`
Expected: PASS (existing + 3 new)

- [ ] **Step 6: Typecheck + lint**

Run: `uv run mypy src/laura/api/models.py src/laura/api/assets.py && uv run ruff check src/laura/api/models.py src/laura/api/assets.py`
Expected: no errors

- [ ] **Step 7: Commit**

```bash
git add src/laura/api/models.py src/laura/api/assets.py tests/test_api_assets.py
git commit -m "$(printf 'feat(api): import assets from a URL via the resilient fetch job\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 7: Documentation

**Files:**
- Modify: `docs/06-storage.md`

- [ ] **Step 1: Document the downloads layout and operational notes**

Add a section to `docs/06-storage.md` (German prose, matching the doc's style):

```markdown
## URL-Ingest: Downloads

Per URL importierte Quellen werden vor der Probe nach
`workspace/<project>/downloads/<asset_id>/<dateiname>` geladen. Während des Downloads
existiert eine `<dateiname>.part`-Datei; sie wird erst nach bestandener Größen-/Decode-
Prüfung atomar zur finalen Datei. Schlägt die Prüfung endgültig fehl, bleibt das Asset
`online=false` und ein `integrity.json` (als `asset_file` kind `integrity`) hält den
Grund fest.

**Google Drive:** Kein Confirm-Token-Handling. Für große Drive-Dateien den direkten
`googleusercontent`-Link verwenden (umgeht die „Virenscan"-Bestätigungsseite, unterstützt
Resume).

**Manueller „kaputtes Netz"-Test:** Einen impairing Proxy (toxiproxy, cross-platform) oder
`clumsy` (Windows) zwischen Service und Quelle schalten und Latenz/Bandbreitenlimit/
Verbindungsabbrüche injizieren. Headless nicht automatisierbar → manuell zu prüfen.
```

- [ ] **Step 2: Commit**

```bash
git add docs/06-storage.md
git commit -m "$(printf 'docs: document URL-ingest downloads layout and manual network test\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 8: Full verification

- [ ] **Step 1: Run the whole suite**

Run: `uv run pytest -q`
Expected: all green (new download/integrity/fetch/api tests included; ffmpeg-dependent ones skip cleanly if ffmpeg is absent).

- [ ] **Step 2: Typecheck + lint the whole package**

Run: `uv run mypy src && uv run ruff check src`
Expected: no errors

- [ ] **Step 3: Manual smoke test (mark as manual — not automated)**

Start the service, import a real direct `googleusercontent` link via
`POST /projects/{id}/assets/import` with `{"source_url": "..."}`, optionally route it
through toxiproxy/clumsy to simulate a flaky link, and confirm the asset ends up
`online=true` with a playable proxy. Record the result in `lessons.md` if anything
surprises you.

---

## Notes on the "auto-retry, then mark corrupt" guarantee

- `ingest.fetch` is enqueued with `max_attempts=5`. The runner re-queues a retriable
  failure immediately (`runner._finish_fail`), so each `run_once` is one attempt.
- **Download interruption** (`DownloadError`) leaves `<dest>.part` → the next attempt's
  `Range` request resumes.
- **Verify failure** deletes `<dest>` and `<dest>.part` → the next attempt downloads in
  full (resume cannot repair corrupt bytes).
- After attempts are exhausted the job is `failed`, the asset stays `online=false`, and
  `integrity.json` records why. No silent success.
```
