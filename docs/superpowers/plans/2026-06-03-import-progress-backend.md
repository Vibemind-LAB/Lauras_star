# Import Progress Backend (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Expose real per-import download progress (bytes/total/speed/ETA + phase) and a retry endpoint, fed by both the segmented httpx engine and the aria2 engine.

**Architecture:** The `ingest.fetch` job persists a throttled progress sample to a new `jobs.progress_json` column via the engines' `on_progress` callback (aria2 is reworked to stream `aria2c` stdout and parse it). A read-only `GET /assets/{id}/import-status` derives a phase from the fetch job + asset state; `POST /assets/{id}/import-retry` re-enqueues a failed fetch.

**Tech Stack:** Python 3.11, FastAPI, SQLite/Postgres migrations, `subprocess.Popen` (aria2 streaming), `pytest`.

**Working directory for all commands:** `services/local-api`. Tests run with `uv run --no-sync pytest`.

**Git hygiene:** The user works in `apps/desktop/` in parallel. For EVERY commit, `git add` only the exact files listed and run `git status` to confirm nothing under `apps/desktop/` is staged. Branch: `docs/resilient-url-ingest-spec` (do not switch). If any command reports `No space left on device`, STOP and report BLOCKED.

**Spec:** [`docs/superpowers/specs/2026-06-03-import-ui-and-progress-design.md`](../specs/2026-06-03-import-ui-and-progress-design.md)

**This is Phase 1 of 2.** Phase 2 (the Electron import UI) has its own plan and consumes the endpoints built here.

---

## File Structure

- Create: `services/local-api/src/laura/db/migrations/0002_job_progress.sql` — add `jobs.progress_json`.
- Modify: `services/local-api/src/laura/db/repos.py` — `get_fetch_job`, `set_job_progress`.
- Modify: `services/local-api/src/laura/ingest/aria2.py` — stream stdout + `_parse_aria2_progress` + `on_progress`.
- Modify: `services/local-api/src/laura/ingest/handlers.py` — throttled progress writer in `handle_fetch`.
- Modify: `services/local-api/src/laura/api/models.py` — `ImportStatusOut`.
- Modify: `services/local-api/src/laura/api/assets.py` — `import-status` + `import-retry` routes.
- Tests: `tests/test_aria2_progress.py`, `tests/test_import_status.py`.

---

## Task 1: Migration + repo helpers

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0002_job_progress.sql`
- Modify: `services/local-api/src/laura/db/repos.py`
- Test: `services/local-api/tests/test_import_status.py`

- [ ] **Step 1: Write the migration**

`services/local-api/src/laura/db/migrations/0002_job_progress.sql`:
```sql
-- Per-job progress sample (latest), written throttled by the fetch handler.
ALTER TABLE jobs ADD COLUMN progress_json TEXT;
```

- [ ] **Step 2: Write the failing test** — create `tests/test_import_status.py`:
```python
"""Backend import-status: migration, repo helpers, endpoint phases, retry."""

from __future__ import annotations

from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs import enqueue


def _fresh_db(tmp_path) -> SqliteDatabase:
    from laura.config import Settings
    db = SqliteDatabase(Settings(workspace_root=tmp_path).db_path)
    db.migrate()
    return db


def test_set_and_get_fetch_job_progress(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": "asset-1", "source_url": "http://x/y.mp4"},
        idempotency_key="fetch:asset-1",
    )
    assert repos.get_fetch_job(db, "asset-1")["id"] == job_id
    repos.set_job_progress(db, job_id, '{"downloaded":10,"total":100,"speed_bps":5}')
    again = repos.get_fetch_job(db, "asset-1")
    assert again["progress_json"] == '{"downloaded":10,"total":100,"speed_bps":5}'


def test_get_fetch_job_none_when_absent(tmp_path) -> None:
    db = _fresh_db(tmp_path)
    assert repos.get_fetch_job(db, "nope") is None
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/test_import_status.py -v`
Expected: FAIL with `AttributeError: module 'laura.db.repos' has no attribute 'get_fetch_job'`.

- [ ] **Step 4: Add repo helpers** to `services/local-api/src/laura/db/repos.py` (after `get_job`, ~line 95):
```python
def get_fetch_job(db: Database, asset_id: str) -> dict[str, Any] | None:
    """The ingest.fetch job for an asset (keyed by its stable idempotency key)."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM jobs WHERE idempotency_key = ?", (f"fetch:{asset_id}",)
        ).fetchone()
        return dict(row) if row is not None else None


def set_job_progress(db: Database, job_id: str, progress_json: str) -> None:
    """Store the latest progress sample for a job (throttled by the caller)."""
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET progress_json = ?, updated_at = ? WHERE id = ?",
            (progress_json, utcnow_iso(), job_id),
        )
```
(`utcnow_iso` and `Database`/`Any` are already imported in repos.py — confirm.)

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run --no-sync pytest tests/test_import_status.py -v`
Expected: PASS (2).

- [ ] **Step 6: Typecheck + lint + commit**

Run: `uv run --no-sync mypy src/laura/db/repos.py && uv run --no-sync ruff check src/laura/db/repos.py tests/test_import_status.py`
```bash
git add services/local-api/src/laura/db/migrations/0002_job_progress.sql services/local-api/src/laura/db/repos.py services/local-api/tests/test_import_status.py
git status   # nothing under apps/desktop/
git commit -m "$(printf 'feat(db): jobs.progress_json + get_fetch_job/set_job_progress\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 2: aria2 progress streaming + parser

**Files:**
- Modify: `services/local-api/src/laura/ingest/aria2.py`
- Test: `services/local-api/tests/test_aria2_progress.py`

- [ ] **Step 1: Write the failing test** — create `tests/test_aria2_progress.py`:
```python
"""Parser for aria2c periodic progress lines (no network, no aria2c needed)."""

from __future__ import annotations

from laura.ingest.aria2 import _parse_aria2_progress


def test_parses_standard_progress_line() -> None:
    line = "[#7d3c1a 1.2GiB/30.0GiB(4%) CN:16 DL:5.0MiB ETA:1h38m]"
    got = _parse_aria2_progress(line)
    assert got is not None
    downloaded, total, speed = got
    assert downloaded == int(1.2 * 1024**3)
    assert total == int(30.0 * 1024**3)
    assert speed == int(5.0 * 1024**2)


def test_returns_none_for_non_progress_line() -> None:
    assert _parse_aria2_progress("12/34 08:00:00 [NOTICE] Downloading 1 item(s)") is None
    assert _parse_aria2_progress("") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/test_aria2_progress.py -v`
Expected: FAIL with `ImportError: cannot import name '_parse_aria2_progress'`.

- [ ] **Step 3: Add the parser + a size helper to `aria2.py`** (top-level, after imports):
```python
import re

_SIZE_RE = re.compile(r"(?P<num>[\d.]+)(?P<unit>[KMGT]?i?B)", re.IGNORECASE)
_UNIT_FACTORS = {
    "b": 1, "kib": 1024, "mib": 1024**2, "gib": 1024**3, "tib": 1024**4,
    "kb": 1000, "mb": 1000**2, "gb": 1000**3, "tb": 1000**4,
}
_PROGRESS_RE = re.compile(
    r"(?P<dl>[\d.]+[KMGT]?i?B)/(?P<total>[\d.]+[KMGT]?i?B)\(\d+%\).*?DL:(?P<speed>[\d.]+[KMGT]?i?B)"
)


def _to_bytes(token: str) -> int | None:
    m = _SIZE_RE.fullmatch(token.strip())
    if m is None:
        return None
    factor = _UNIT_FACTORS.get(m.group("unit").lower())
    if factor is None:
        return None
    return int(float(m.group("num")) * factor)


def _parse_aria2_progress(line: str) -> tuple[int, int, int] | None:
    """Extract (downloaded_bytes, total_bytes, speed_bps) from an aria2c progress line.

    Returns None for lines that are not periodic progress summaries, or if any field
    can't be parsed (tolerant across aria2 versions/units)."""
    m = _PROGRESS_RE.search(line)
    if m is None:
        return None
    downloaded = _to_bytes(m.group("dl"))
    total = _to_bytes(m.group("total"))
    speed = _to_bytes(m.group("speed"))
    if downloaded is None or total is None or speed is None:
        return None
    return downloaded, total, speed
```

- [ ] **Step 4: Run parser test to verify it passes**

Run: `uv run --no-sync pytest tests/test_aria2_progress.py -v`
Expected: PASS (2).

- [ ] **Step 5: Rework `aria2_download` to stream + report progress**

Replace the `subprocess.run(...)` body of `aria2_download` with a streaming `Popen`. Add an `on_progress` parameter. Add `--summary-interval=1` to the command. New signature + body:
```python
def aria2_download(
    url: str,
    dest_dir: Path | str,
    *,
    filename: str | None = None,
    opts: Aria2Opts | None = None,
    on_progress: Callable[[int, int, int], None] | None = None,
) -> list[Path]:
    """Download ``url`` into ``dest_dir`` via one-shot aria2c, streaming progress.

    ``on_progress(downloaded, total, speed_bps)`` is called for each periodic summary
    line aria2c emits (best-effort). Returns the produced file paths."""
    opts = opts or Aria2Opts()
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    cmd = [
        aria2_bin(),
        "--dir", str(dest_dir),
        "--continue=true",
        "--auto-file-renaming=false",
        "--allow-overwrite=true",
        "--summary-interval=1",
        "--console-log-level=warn",
        f"--max-connection-per-server={opts.connections}",
        f"--split={opts.connections}",
        "--seed-time=0",
    ]
    if filename:
        cmd += ["--out", filename]
    if opts.max_overall_download_limit:
        cmd += [f"--max-overall-download-limit={opts.max_overall_download_limit}"]
    if opts.all_proxy:
        cmd += [f"--all-proxy={opts.all_proxy}"]
    cmd.append(url)

    tail: list[str] = []
    try:
        proc = subprocess.Popen(  # noqa: S603
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
        )
    except FileNotFoundError as exc:
        raise Aria2Error(f"aria2c not found: {aria2_bin()}") from exc
    assert proc.stdout is not None
    for line in proc.stdout:
        tail.append(line)
        if len(tail) > 100:
            del tail[0]
        if on_progress is not None:
            parsed = _parse_aria2_progress(line)
            if parsed is not None:
                on_progress(*parsed)
    returncode = proc.wait()
    if returncode != 0:
        raise Aria2Error(("".join(tail) or "aria2c failed").strip()[-2000:])

    files = _list_downloaded(dest_dir)
    if not files:
        raise Aria2Error(f"aria2c produced no files in {dest_dir}")
    return files
```
Add `from collections.abc import Callable` to the imports at the top of `aria2.py`.

- [ ] **Step 6: Verify the existing aria2 test still passes**

Run: `uv run --no-sync pytest tests/test_aria2.py tests/test_aria2_progress.py -v`
Expected: `test_aria2_available_reflects_path` PASS; `test_aria2_downloads_http_file` PASS if aria2c installed else SKIP; parser tests PASS. (The existing download test calls `aria2_download` WITHOUT `on_progress`, which must still work.)

- [ ] **Step 7: Typecheck + lint + commit**

Run: `uv run --no-sync mypy src/laura/ingest/aria2.py && uv run --no-sync ruff check src/laura/ingest/aria2.py tests/test_aria2_progress.py`
```bash
git add services/local-api/src/laura/ingest/aria2.py services/local-api/tests/test_aria2_progress.py
git status
git commit -m "$(printf 'feat(ingest): stream aria2c progress via Popen + parser\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 3: Persist progress from handle_fetch (throttled)

**Files:**
- Modify: `services/local-api/src/laura/ingest/handlers.py`
- Test: `services/local-api/tests/test_import_status.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_import_status.py`:
```python
import json
from pathlib import Path

import pytest

from laura.ingest.handlers import _ProgressWriter  # throttled writer


def test_progress_writer_throttles_and_writes(tmp_path, monkeypatch) -> None:
    db = _fresh_db(tmp_path)
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": "a", "source_url": "http://x"}, idempotency_key="fetch:a",
    )
    # monotonic clock we control
    clock = {"t": 1000.0}
    monkeypatch.setattr("laura.ingest.handlers.time.monotonic", lambda: clock["t"])

    w = _ProgressWriter(db, job_id, min_interval=1.0)
    w(0, 100)               # first call always writes (speed 0)
    w(10, 100)              # same instant -> throttled, no write
    clock["t"] = 1001.5
    w(60, 100)              # >1s later -> writes, speed = 50 bytes/1.5s

    prog = json.loads(repos.get_fetch_job(db, "a")["progress_json"])
    assert prog["downloaded"] == 60
    assert prog["total"] == 100
    assert prog["speed_bps"] == pytest.approx(50 / 1.5, rel=0.2)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/test_import_status.py -k progress_writer -v`
Expected: FAIL with `ImportError: cannot import name '_ProgressWriter'`.

- [ ] **Step 3: Add `_ProgressWriter` and wire it into `handle_fetch`**

In `services/local-api/src/laura/ingest/handlers.py`, add the class (top-level, after imports — `time`, `json`, `repos` are already imported):
```python
class _ProgressWriter:
    """Throttled persistence of download progress into the job's progress_json."""

    def __init__(self, db: Any, job_id: str, *, min_interval: float = 1.0) -> None:
        self._db = db
        self._job_id = job_id
        self._min_interval = min_interval
        self._last_t = 0.0
        self._last_bytes = 0
        self._started = False

    def __call__(self, downloaded: int, total: int | None) -> None:
        now = time.monotonic()
        if self._started and now - self._last_t < self._min_interval:
            return
        speed = 0.0
        if self._started and now > self._last_t:
            speed = (downloaded - self._last_bytes) / (now - self._last_t)
        self._started = True
        self._last_t = now
        self._last_bytes = downloaded
        repos.set_job_progress(
            self._db, self._job_id,
            json.dumps({"downloaded": downloaded, "total": total, "speed_bps": speed}),
        )
```

Then in `handle_fetch`, replace the `_heartbeat` closure usage so progress is BOTH heartbeated and persisted. Concretely, build the writer once and call heartbeat from it:
- Replace the existing `last_hb`/`_heartbeat` definitions with:
```python
    progress = _ProgressWriter(ctx.db, ctx.job_id)
    last_hb = [0.0]

    def _on_progress(downloaded: int, total: int | None) -> None:
        progress(downloaded, total)
        now = time.monotonic()
        if now - last_hb[0] > 10.0:
            ctx.heartbeat()
            last_hb[0] = now
```
- In the **httpx branch**, pass `on_progress=_on_progress` to `download_resumable` (it already receives a callback — swap the old `_heartbeat` for `_on_progress`).
- In the **aria2 branch**, pass progress through: the aria2 `on_progress` signature is `(downloaded, total, speed)`, so adapt:
```python
        files = aria2_download(
            url, base_dir,
            on_progress=lambda d, t, _s: _on_progress(d, t),
        )
```
  Keep the existing heartbeat thread around `aria2_download` (it covers the gaps between progress lines). Leave everything else (media filter, fan-out) unchanged.

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/test_import_status.py tests/test_fetch.py -v`
Expected: the new `_ProgressWriter` test PASSES; existing fetch tests still PASS (or skip without ffmpeg).

- [ ] **Step 5: Typecheck + lint + commit**

Run: `uv run --no-sync mypy src/laura/ingest/handlers.py && uv run --no-sync ruff check src/laura/ingest/handlers.py tests/test_import_status.py`
```bash
git add services/local-api/src/laura/ingest/handlers.py services/local-api/tests/test_import_status.py
git status
git commit -m "$(printf 'feat(ingest): persist throttled download progress to the fetch job\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 4: `import-status` endpoint + phase derivation

**Files:**
- Modify: `services/local-api/src/laura/api/models.py`
- Modify: `services/local-api/src/laura/api/assets.py`
- Test: `services/local-api/tests/test_import_status.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_import_status.py`:
```python
from fastapi.testclient import TestClient

from laura.main import create_app
from laura.config import Settings


def _client_db(tmp_path):
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    client = TestClient(app)
    db = app.state.db
    return client, db


def _project(client) -> str:
    return client.post(
        "/projects", json={"name": "p", "sequence_rate_num": 30, "sequence_rate_den": 1}
    ).json()["id"]


def test_import_status_downloading_then_error(tmp_path) -> None:
    client, db = _client_db(tmp_path)
    pid = _project(client)
    asset = repos.create_asset(
        db, project_id=pid, type="video", display_name="x.mp4",
        source_path="url:http://x/y.mp4", online=False,
    )
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": asset["id"], "source_url": "http://x/y.mp4"},
        idempotency_key=f"fetch:{asset['id']}",
    )
    repos.set_job_progress(db, job_id, '{"downloaded":50,"total":200,"speed_bps":25}')

    body = client.get(f"/assets/{asset['id']}/import-status").json()
    assert body["phase"] == "downloading"
    assert body["downloaded_bytes"] == 50
    assert body["total_bytes"] == 200
    assert body["eta_seconds"] == pytest.approx((200 - 50) / 25, rel=0.01)

    # mark the job failed -> phase error
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET status='failed', error_json=? WHERE id=?",
                     ('{"error":"boom"}', job_id))
    body = client.get(f"/assets/{asset['id']}/import-status").json()
    assert body["phase"] == "error"
    assert "boom" in (body["error"] or "")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/test_import_status.py -k downloading -v`
Expected: FAIL (404 — route not defined).

- [ ] **Step 3: Add the model** to `services/local-api/src/laura/api/models.py`:
```python
class ImportStatusOut(BaseModel):
    phase: str  # queued | downloading | verifying | analyzing | ready | error
    downloaded_bytes: int | None = None
    total_bytes: int | None = None
    speed_bps: float | None = None
    eta_seconds: float | None = None
    error: str | None = None
```

- [ ] **Step 4: Add the route + derivation** to `services/local-api/src/laura/api/assets.py`

Add `import json` at the top if missing. Add the helper + route:
```python
def _derive_import_status(db: Database, asset: dict[str, Any]) -> ImportStatusOut:
    job = repos.get_fetch_job(db, asset["id"])
    if job is not None and job["status"] == "failed":
        files = {f["kind"]: f for f in repos.list_asset_files(db, asset["id"])}
        detail = None
        integ = files.get("integrity")
        if integ is not None:
            try:
                detail = json.loads(Path(integ["path"]).read_text(encoding="utf-8")).get("detail")
            except (OSError, ValueError):
                detail = None
        if detail is None and job.get("error_json"):
            try:
                detail = json.loads(job["error_json"]).get("error")
            except ValueError:
                detail = job["error_json"]
        return ImportStatusOut(phase="error", error=detail)

    if job is not None and job["status"] in ("queued", "leased", "running"):
        prog = json.loads(job["progress_json"]) if job.get("progress_json") else None
        if prog:
            downloaded, total = prog.get("downloaded"), prog.get("total")
            speed = prog.get("speed_bps")
            eta = ((total - downloaded) / speed) if (total and speed and speed > 0) else None
            return ImportStatusOut(
                phase="downloading", downloaded_bytes=downloaded, total_bytes=total,
                speed_bps=speed, eta_seconds=eta,
            )
        return ImportStatusOut(phase="queued")

    # download done (or a plain file import): derive from probe/proxy artifacts
    if not asset["online"]:
        return ImportStatusOut(phase="queued")
    kinds = {f["kind"] for f in repos.list_asset_files(db, asset["id"])}
    if "waveform" in kinds or "proxy" in kinds:
        return ImportStatusOut(phase="ready")
    return ImportStatusOut(phase="analyzing")


@router.get("/assets/{asset_id}/import-status", response_model=ImportStatusOut)
def import_status(asset_id: str, request: Request) -> ImportStatusOut:
    db = _db(request)
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    return _derive_import_status(db, asset)
```
Confirm `ImportStatusOut`, `Database`, `Path`, `Any`, `repos`, `HTTPException`, `status`, `Request`, `_db`, `router` are imported in assets.py (add `ImportStatusOut` to the models import; the rest already exist).

- [ ] **Step 5: Run tests**

Run: `uv run --no-sync pytest tests/test_import_status.py -v`
Expected: all PASS.

- [ ] **Step 6: Typecheck + lint + commit**

Run: `uv run --no-sync mypy src/laura/api/assets.py src/laura/api/models.py && uv run --no-sync ruff check src/laura/api/assets.py src/laura/api/models.py tests/test_import_status.py`
```bash
git add services/local-api/src/laura/api/assets.py services/local-api/src/laura/api/models.py services/local-api/tests/test_import_status.py
git status
git commit -m "$(printf 'feat(api): GET import-status with phase + progress derivation\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 5: `import-retry` endpoint

**Files:**
- Modify: `services/local-api/src/laura/api/assets.py`
- Test: `services/local-api/tests/test_import_status.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_import_status.py`:
```python
def test_import_retry_requeues_failed_fetch(tmp_path) -> None:
    client, db = _client_db(tmp_path)
    pid = _project(client)
    asset = repos.create_asset(
        db, project_id=pid, type="video", display_name="x.mp4",
        source_path="url:http://x/y.mp4", online=False,
    )
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": asset["id"], "source_url": "http://x/y.mp4"},
        idempotency_key=f"fetch:{asset['id']}",
    )
    with db.connection() as conn:
        conn.execute("UPDATE jobs SET status='failed' WHERE id=?", (job_id,))

    assert client.post(f"/assets/{asset['id']}/import-retry").status_code == 202
    # a fresh queued fetch job now exists for the asset
    job = repos.get_fetch_job(db, asset["id"])
    assert job["status"] == "queued"


def test_import_retry_conflict_when_not_error(tmp_path) -> None:
    client, db = _client_db(tmp_path)
    pid = _project(client)
    asset = repos.create_asset(
        db, project_id=pid, type="video", display_name="x.mp4",
        source_path="url:http://x/y.mp4", online=False,
    )
    enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": asset["id"], "source_url": "http://x/y.mp4"},
        idempotency_key=f"fetch:{asset['id']}",
    )  # status queued, not failed
    assert client.post(f"/assets/{asset['id']}/import-retry").status_code == 409
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run --no-sync pytest tests/test_import_status.py -k retry -v`
Expected: FAIL (404 — route not defined).

- [ ] **Step 3: Add the route** to `services/local-api/src/laura/api/assets.py`:
```python
@router.post("/assets/{asset_id}/import-retry", status_code=status.HTTP_202_ACCEPTED)
def import_retry(asset_id: str, request: Request) -> dict[str, str]:
    db = _db(request)
    asset = repos.get_asset(db, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    job = repos.get_fetch_job(db, asset_id)
    if job is None or job["status"] != "failed":
        raise HTTPException(status.HTTP_409_CONFLICT, "no failed import to retry")
    source_url = json.loads(job["payload_json"]).get("source_url")
    if not source_url:
        raise HTTPException(status.HTTP_409_CONFLICT, "fetch job has no source_url")
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": asset_id, "source_url": source_url},
        idempotency_key=f"fetch:{asset_id}", max_attempts=5,
    )
    return {"asset_id": asset_id, "job_id": job_id}
```
(`enqueue` is already imported in assets.py — confirm; `json` added in Task 4.)

NOTE: `enqueue` reuses a non-failed job for the same idempotency key, but DELETES a `failed` one and inserts fresh (per the runner's enqueue logic) — so re-enqueuing a failed fetch produces a new `queued` job. Verify this against `jobs/runner.py::enqueue` before relying on it; if the semantics differ, adjust the test's expectation to match real behavior (do not weaken intent: a retry must result in a runnable fetch job).

- [ ] **Step 4: Run tests**

Run: `uv run --no-sync pytest tests/test_import_status.py -v`
Expected: all PASS.

- [ ] **Step 5: Typecheck + lint + commit**

Run: `uv run --no-sync mypy src/laura/api/assets.py && uv run --no-sync ruff check src/laura/api/assets.py tests/test_import_status.py`
```bash
git add services/local-api/src/laura/api/assets.py services/local-api/tests/test_import_status.py
git status
git commit -m "$(printf 'feat(api): POST import-retry to re-enqueue a failed fetch\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 6: Full verification

- [ ] **Step 1:** `uv run --no-sync pytest -q` → all green (new tests included; ffmpeg/aria2c-gated skip cleanly).
- [ ] **Step 2:** `uv run --no-sync mypy src && uv run --no-sync ruff check src tests` → no errors.
- [ ] **Step 3:** Manual smoke (mark manual): start the service, import a real URL, and `curl http://127.0.0.1:8765/assets/<id>/import-status` repeatedly to watch the phase/progress advance.

---

## Notes
- `progress_json` holds only the LATEST sample (overwritten each throttled write) — no history, by design.
- Phase derivation is read-only and works for BOTH url imports (fetch job present) and plain file imports (no fetch job → derive from online + artifacts).
- `eta_seconds`/`speed_bps` are best-effort and `null` when unknown.
