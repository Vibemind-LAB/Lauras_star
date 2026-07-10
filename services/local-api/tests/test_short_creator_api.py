"""Auto-short endpoint + job handler (Iteration 8).

The endpoint's 404/422/503/202 branches are covered (``_autoshort_available`` monkeypatched so the
test never depends on whether the optional extra is installed). The handler is covered for the
asset-not-found guard and the happy path via an injected ``execute`` (no autogen, no LLM).
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database
from laura.short_creator import handlers, orchestrator, providers


def _project(db: Database) -> str:
    p = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    return str(p["id"])


def _asset(db: Database, project_id: str) -> str:
    a = repos.create_asset(
        db, project_id=project_id, type="video", display_name="v", source_path="/tmp/v.mp4"
    )
    return str(a["id"])


# --- endpoint --------------------------------------------------------------------------------


def test_auto_short_unknown_asset_404(client: TestClient) -> None:
    r = client.post("/assets/nope/auto-short", json={"topic": "cats"})
    assert r.status_code == 404


def test_auto_short_invalid_body_422(client: TestClient, db: Database) -> None:
    aid = _asset(db, _project(db))
    r = client.post(f"/assets/{aid}/auto-short", json={"topic": "", "target_seconds": 0})
    assert r.status_code == 422


def test_auto_short_missing_extra_503(client: TestClient, db: Database, monkeypatch: Any) -> None:
    aid = _asset(db, _project(db))
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: False)
    r = client.post(f"/assets/{aid}/auto-short", json={"topic": "cats"})
    assert r.status_code == 503


def test_auto_short_enqueues_when_available(
    client: TestClient, db: Database, monkeypatch: Any
) -> None:
    aid = _asset(db, _project(db))
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    r = client.post(f"/assets/{aid}/auto-short", json={"topic": "cats", "target_seconds": 45})
    assert r.status_code == 202, r.text
    assert r.json()["job_id"]


# --- job handler -----------------------------------------------------------------------------


def test_handle_short_creator_asset_not_found(db: Database) -> None:
    ctx = SimpleNamespace(db=db, payload={"asset_id": "nope", "topic": "x"})
    out = handlers.handle_short_creator_run(ctx)  # type: ignore[arg-type]
    assert out["ok"] is False
    assert out["error"] == "asset not found"


def test_handle_short_creator_happy_with_injected_execute(db: Database) -> None:
    aid = _asset(db, _project(db))

    def fake_execute(
        db_: Database, config: providers.AgentConfig, stage: str, kind: str, task: str
    ) -> orchestrator.StageOutcome:
        return orchestrator.StageOutcome(
            status="ok", weak=False, summary="done", team="magentic", stage="A"
        )

    ctx = SimpleNamespace(db=db, payload={"asset_id": aid, "topic": "cats", "target_seconds": 30})
    out = handlers.handle_short_creator_run(ctx, execute=fake_execute)  # type: ignore[arg-type]
    assert out["ok"] is True
    assert out["stage"] == "A"


# --- streaming endpoint ----------------------------------------------------------------------


async def _fake_stream(
    db: Database,
    config: providers.AgentConfig,
    *,
    asset_id: str,
    topic: str,
    target_seconds: int = 60,
    execute_stream: Any = None,
) -> AsyncIterator[dict[str, Any]]:
    yield {"type": "stage", "stage": "A", "team": "magentic"}
    yield {"type": "agent", "agent": "scout", "text": "searching"}
    yield {
        "type": "done",
        "ok": True,
        "stage": "A",
        "team": "magentic",
        "weak": False,
        "escalated": False,
        "summary": "",
    }


def test_auto_short_stream_unknown_asset_404(client: TestClient) -> None:
    r = client.post("/assets/nope/auto-short/stream", json={"topic": "cats"})
    assert r.status_code == 404


def test_auto_short_stream_missing_extra_503(
    client: TestClient, db: Database, monkeypatch: Any
) -> None:
    aid = _asset(db, _project(db))
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: False)
    r = client.post(f"/assets/{aid}/auto-short/stream", json={"topic": "cats"})
    assert r.status_code == 503


def test_auto_short_stream_streams_ndjson_events(
    client: TestClient, db: Database, settings: Settings, monkeypatch: Any
) -> None:
    aid = _asset(db, _project(db))
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr("laura.short_creator.stream.run_short_creator_stream", _fake_stream)
    r = client.post(f"/assets/{aid}/auto-short/stream", json={"topic": "cats"})
    assert r.status_code == 200, r.text
    events = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert [e["type"] for e in events] == ["stage", "agent", "done"]
    assert events[-1]["ok"] is True

    # Every event ALSO lands in an NDJSON run log (meta line first) — runs are debuggable
    # after the fact without the chat panel's copy (which dies with the window).
    logs = sorted((settings.workspace_root / "agent-runs").glob("*.ndjson"))
    assert logs, "run log file missing"
    logged = [
        json.loads(line)
        for line in logs[-1].read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert logged[0]["type"] == "meta"
    assert logged[0]["asset_id"] == aid
    assert logged[0]["topic"] == "cats"
    assert [e["type"] for e in logged[1:]] == ["stage", "agent", "done"]
