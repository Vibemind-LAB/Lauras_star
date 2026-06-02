"""Portion 15.1 — list pagination (limit/offset + X-Total-Count, defensive clamping).

Assertions are count-based (order-independent): equal ``created_at`` timestamps can
tie under coarse OS clock resolution, so we never depend on cross-query ordering.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app


def _ctx(tmp_path: Path) -> tuple[SqliteDatabase, TestClient]:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    client = TestClient(create_app(settings))
    client.__enter__()
    return db, client


def _project(client: TestClient, name: str = "P") -> dict[str, Any]:
    return dict(
        client.post(
            "/projects",
            json={"name": name, "sequence_rate_num": 30, "sequence_rate_den": 1},
        ).json()
    )


def test_projects_limit_offset_and_total(tmp_path: Path) -> None:
    _db, client = _ctx(tmp_path)
    try:
        ids = {_project(client, f"P{i}")["id"] for i in range(3)}

        page1 = client.get("/projects", params={"limit": 2})
        assert page1.status_code == 200
        assert len(page1.json()) == 2
        assert page1.headers["X-Total-Count"] == "3"
        assert {p["id"] for p in page1.json()} <= ids  # subset, order-independent

        page2 = client.get("/projects", params={"limit": 2, "offset": 2})
        assert len(page2.json()) == 1
        assert page2.headers["X-Total-Count"] == "3"
    finally:
        client.__exit__(None, None, None)


def test_offset_past_end_is_empty(tmp_path: Path) -> None:
    _db, client = _ctx(tmp_path)
    try:
        _project(client)
        resp = client.get("/projects", params={"offset": 99})
        assert resp.status_code == 200
        assert resp.json() == []
        assert resp.headers["X-Total-Count"] == "1"
    finally:
        client.__exit__(None, None, None)


def test_limit_and_offset_are_clamped(tmp_path: Path) -> None:
    _db, client = _ctx(tmp_path)
    try:
        for i in range(3):
            _project(client, f"P{i}")
        # limit below 1 clamps to 1
        assert len(client.get("/projects", params={"limit": 0}).json()) == 1
        # negative offset clamps to 0 -> full set
        assert len(client.get("/projects", params={"offset": -5}).json()) == 3
        # absurd limit clamps to MAX_LIMIT without error -> still all rows
        assert len(client.get("/projects", params={"limit": 10_000}).json()) == 3
    finally:
        client.__exit__(None, None, None)


def test_default_returns_all_with_total(tmp_path: Path) -> None:
    _db, client = _ctx(tmp_path)
    try:
        for i in range(5):
            _project(client, f"P{i}")
        resp = client.get("/projects")
        assert len(resp.json()) == 5
        assert resp.headers["X-Total-Count"] == "5"
    finally:
        client.__exit__(None, None, None)


def test_assets_and_timelines_paginate(tmp_path: Path) -> None:
    db, client = _ctx(tmp_path)
    try:
        p = _project(client)
        for i in range(3):
            repos.create_asset(
                db, project_id=p["id"], type="video",
                display_name=f"a{i}.mov", source_path=f"a{i}.mov",
            )
            client.post(
                f"/projects/{p['id']}/timelines",
                json={"name": f"T{i}", "kind": "rough_cut"},
            )

        a = client.get(f"/projects/{p['id']}/assets", params={"limit": 2})
        assert len(a.json()) == 2
        assert a.headers["X-Total-Count"] == "3"

        t = client.get(
            f"/projects/{p['id']}/timelines", params={"limit": 1, "offset": 2}
        )
        assert len(t.json()) == 1
        assert t.headers["X-Total-Count"] == "3"
    finally:
        client.__exit__(None, None, None)
