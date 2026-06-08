"""Backend import-cancel: repo flag, endpoint, and cooperative abort in handle_fetch.

The httpx download loop is driven deterministically (the cancel flag is flipped from
the progress callback / monkeypatched check) so the abort is exercised without relying
on wall-clock timing. A genuinely threaded abort is covered too, tolerantly.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest import handlers
from laura.ingest.handlers import ImportCancelled, handle_fetch, register_ingest_handlers
from laura.jobs import JobRunner, default_registry, enqueue
from laura.jobs.runner import JobContext
from laura.main import create_app

from ._flaky_http import serve

# A body large enough that the single-stream loop yields several progress ticks
# (chunk_bytes default 1 MiB -> use a small chunk_bytes in tests via the handler path).
BIG = b"laura-cancel-" * 200_000  # ~2.6 MiB, deterministic


def _fresh_db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path).db_path)
    db.migrate()
    return db


def _project_and_asset(db: SqliteDatabase, tmp_path: Path) -> dict[str, str]:
    project_root = tmp_path / "project"
    project_root.mkdir(parents=True, exist_ok=True)
    project = repos.create_project(
        db, name="t", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(project_root),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="big.bin", source_path="url:pending", online=False,
    )
    return {"project_id": project["id"], "asset_id": asset["id"]}


def _online(db: SqliteDatabase, asset_id: str) -> int:
    asset = repos.get_asset(db, asset_id)
    assert asset is not None
    return int(asset["online"])


# --- repo helpers -----------------------------------------------------------

def test_request_and_is_import_cancelled_roundtrip(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": "a", "source_url": "http://x"}, idempotency_key="fetch:a",
    )
    assert repos.is_import_cancelled(db, "a") is False
    assert repos.request_import_cancel(db, "a") is True
    assert repos.is_import_cancelled(db, "a") is True


def test_request_import_cancel_noop_without_job(tmp_path: Path) -> None:
    db = _fresh_db(tmp_path)
    # No fetch job exists (import already finished / never started) -> harmless no-op.
    assert repos.request_import_cancel(db, "ghost") is False
    assert repos.is_import_cancelled(db, "ghost") is False


# --- endpoint ---------------------------------------------------------------

def _client_db(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    app = create_app(settings)
    client = TestClient(app)
    db: SqliteDatabase = app.state.db
    return client, db


def test_import_cancel_endpoint_sets_flag(tmp_path: Path) -> None:
    client, db = _client_db(tmp_path)
    ids = _project_and_asset(db, tmp_path)
    aid = ids["asset_id"]
    enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": aid, "source_url": "http://x/y.mp4"},
        idempotency_key=f"fetch:{aid}",
    )
    resp = client.post(f"/assets/{aid}/import-cancel")
    assert resp.status_code == 202
    assert repos.is_import_cancelled(db, aid) is True
    # Idempotent: a second call is still accepted.
    assert client.post(f"/assets/{aid}/import-cancel").status_code == 202


def test_import_cancel_endpoint_404_unknown_asset(tmp_path: Path) -> None:
    client, _ = _client_db(tmp_path)
    assert client.post("/assets/does-not-exist/import-cancel").status_code == 404


def test_import_status_reports_cancelled(tmp_path: Path) -> None:
    client, db = _client_db(tmp_path)
    ids = _project_and_asset(db, tmp_path)
    aid = ids["asset_id"]
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": aid, "source_url": "http://x/y.mp4"},
        idempotency_key=f"fetch:{aid}",
    )
    repos.set_job_progress(db, job_id, '{"downloaded":50,"total":200,"speed_bps":25}')
    repos.request_import_cancel(db, aid)
    body = client.get(f"/assets/{aid}/import-status").json()
    assert body["phase"] == "cancelled"


# --- handler: start-of-handler guard ---------------------------------------

def _ctx_for(db: SqliteDatabase, job_id: str, asset_id: str) -> JobContext:
    return JobContext(
        job_id=job_id, kind="ingest.fetch", queue="ingest.io",
        payload={"asset_id": asset_id, "source_url": "http://127.0.0.1:1/never"},
        db=db,
    )


def test_handler_exits_immediately_when_already_cancelled(tmp_path: Path) -> None:
    # A queued/retried fetch of an already-cancelled import must not touch the network.
    db = _fresh_db(tmp_path)
    ids = _project_and_asset(db, tmp_path)
    aid = ids["asset_id"]
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": aid, "source_url": "http://127.0.0.1:1/never"},
        idempotency_key=f"fetch:{aid}",
    )
    repos.request_import_cancel(db, aid)
    # source_url points at a closed port; if the guard failed this would hang/raise a
    # transport error instead of returning cleanly.
    result = handle_fetch(_ctx_for(db, job_id, aid))
    assert result == {"asset_id": aid, "cancelled": True}
    assert _online(db, aid) == 0  # never promoted


def test_cancelled_fetch_is_terminal_across_retries(tmp_path: Path) -> None:
    # With the start-of-handler guard, re-running the job (the runner's retry path)
    # is a no-op: it never goes online no matter how many attempts run.
    db = _fresh_db(tmp_path)
    ids = _project_and_asset(db, tmp_path)
    aid = ids["asset_id"]
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": aid, "source_url": "http://127.0.0.1:1/never"},
        idempotency_key=f"fetch:{aid}", max_attempts=5,
    )
    repos.request_import_cancel(db, aid)
    for _ in range(3):
        assert handle_fetch(_ctx_for(db, job_id, aid)) == {"asset_id": aid, "cancelled": True}
    assert _online(db, aid) == 0


# --- handler: cooperative abort mid-download (httpx single stream) ---------

def test_httpx_download_aborts_on_cancel_and_removes_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drive the real download loop; flip the cancel flag from inside the progress
    check so the abort happens deterministically between chunks."""
    db = _fresh_db(tmp_path)
    ids = _project_and_asset(db, tmp_path)
    aid = ids["asset_id"]
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": aid, "source_url": "REPLACED"},
        idempotency_key=f"fetch:{aid}",
    )

    # Make the cancel check return True from the 2nd call on: the first progress tick
    # writes part of the .part file, the next one raises ImportCancelled.
    calls = {"n": 0}

    def fake_is_cancelled(database: object, asset_id: str) -> bool:
        calls["n"] += 1
        return calls["n"] >= 2

    # handlers looks up ``repos.is_import_cancelled`` at call time, so patching the
    # repos module here is what the handler's progress callback will observe.
    monkeypatch.setattr(repos, "is_import_cancelled", fake_is_cancelled)

    base_dir = tmp_path / "project" / "downloads" / aid
    # BIG (~2.6 MiB) is below the 8 MiB segment threshold, so the single-stream loop
    # (1 MiB chunks) runs, yielding multiple progress ticks for the cancel to land on.
    with serve(BIG) as url:
        ctx = JobContext(
            job_id=job_id, kind="ingest.fetch", queue="ingest.io",
            payload={"asset_id": aid, "source_url": url}, db=db,
        )
        result = handle_fetch(ctx)

    assert result == {"asset_id": aid, "cancelled": True}
    assert _online(db, aid) == 0
    # No partial bytes left behind anywhere under the asset's download dir.
    assert not base_dir.exists() or not any(base_dir.rglob("*"))


def test_run_fetch_raises_import_cancelled_mid_download(tmp_path: Path) -> None:
    """_run_fetch (the engine dispatch) propagates ImportCancelled out of the real
    httpx download loop when the progress callback raises mid-stream — confirming the
    exception type that handle_fetch relies on to short-circuit retries."""
    db = _fresh_db(tmp_path)
    ids = _project_and_asset(db, tmp_path)
    aid = ids["asset_id"]
    job_id = enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": aid, "source_url": "REPLACED"},
        idempotency_key=f"fetch:{aid}",
    )
    asset = repos.get_asset(db, aid)
    assert asset is not None

    calls = {"n": 0}

    def _on_progress(downloaded: int, total: int | None) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:  # first tick proceeds, second aborts
            raise ImportCancelled(aid)

    base_dir = tmp_path / "project" / "downloads" / aid
    with serve(BIG) as url:
        ctx = JobContext(
            job_id=job_id, kind="ingest.fetch", queue="ingest.io",
            payload={"asset_id": aid, "source_url": url}, db=db,
        )
        with pytest.raises(ImportCancelled):
            handlers._run_fetch(
                ctx, asset, url, base_dir,
                full_scan=True, fmt=None, cookies_from_browser=None,
                on_progress=_on_progress,
            )


# --- handler: threaded real abort (tolerant) -------------------------------

def test_threaded_fetch_cancel_mid_download(tmp_path: Path) -> None:
    """A genuine background fetch is cancelled via request_import_cancel while running.

    Uses a server that throttles the body so the download is still in flight when the
    cancel lands. Tolerant: the key assertions are that the run terminates, the asset
    never goes online, and no partial file survives."""
    db = _fresh_db(tmp_path)
    ids = _project_and_asset(db, tmp_path)
    aid = ids["asset_id"]

    registry = default_registry()
    register_ingest_handlers(registry)
    runner = JobRunner(db, registry)

    # A server that drips the body slowly so the download stays in flight.
    huge = b"laura-throttle-" * 2_000_000  # ~30 MiB

    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Slow(BaseHTTPRequestHandler):
        def log_message(self, *a: object) -> None:
            pass

        def do_GET(self) -> None:  # noqa: N802
            self.send_response(200)
            self.send_header("Content-Length", str(len(huge)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            sent = 0
            step = 64 * 1024
            try:
                while sent < len(huge):
                    self.wfile.write(huge[sent:sent + step])
                    self.wfile.flush()
                    sent += step
                    time.sleep(0.02)
            except (BrokenPipeError, ConnectionResetError, OSError):
                return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Slow)
    server.socket.settimeout(1.0)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    host, port = str(server.server_address[0]), int(server.server_address[1])
    url = f"http://{host}:{port}/file"

    enqueue(
        db, queue="ingest.io", kind="ingest.fetch",
        payload={"asset_id": aid, "source_url": url},
        idempotency_key=f"fetch:{aid}", max_attempts=2,
    )

    worker = threading.Thread(target=runner.run_once, daemon=True)
    worker.start()
    try:
        # Let some bytes flow, then cancel.
        deadline = time.monotonic() + 5.0
        job = repos.get_fetch_job(db, aid)
        while time.monotonic() < deadline:
            job = repos.get_fetch_job(db, aid)
            if job is not None and job.get("progress_json"):
                break
            time.sleep(0.05)
        repos.request_import_cancel(db, aid)
        worker.join(timeout=15.0)
        assert not worker.is_alive(), "fetch did not abort after cancel"
    finally:
        server.shutdown()
        server.server_close()
        t.join(timeout=5)

    a = repos.get_asset(db, aid)
    assert a is not None
    assert a["online"] == 0  # cancelled -> never promoted
    base_dir = tmp_path / "project" / "downloads" / aid
    assert not base_dir.exists() or not any(base_dir.rglob("*"))
