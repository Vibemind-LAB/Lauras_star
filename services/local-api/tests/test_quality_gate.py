"""P1-T2b — min_quality publish gate tests.

Tests every row of the behaviour table from the brief.  Each row is exercised
against BOTH publish endpoints:
  - POST /timelines/{id}/render-reel  (api/reels.py)
  - POST /timelines/{id}/render       (api/timelines.py)

Behaviour table
---------------
| persisted status      | min_quality | result                          |
|-----------------------|-------------|--------------------------------|
| computed, overall≥min | set         | 200, quality_verified=True      |
| computed, overall<min | set         | 422 (named reason)              |
| computed              | None        | 200, quality_verified=True      |
| no_video              | any         | 200, quality_verified=False     |
| error                 | any         | 200, quality_verified=False     |
| pending (no row)      | any         | 200, quality_verified=False     |
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _setup(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    app = create_app(settings)
    client = TestClient(app)
    return client, db


def _make_timeline(client: TestClient, db: SqliteDatabase) -> str:
    pid_resp = client.post(
        "/projects", json={"name": "test-proj", "sequence_rate_num": 30, "sequence_rate_den": 1}
    )
    pid = pid_resp.json()["id"]
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")
    return str(tl["id"])


def _seed_quality(
    db: SqliteDatabase,
    tl_id: str,
    status: str,
    overall: float | None = None,
) -> None:
    """Seed a timeline_quality row with the given status and overall score."""
    repos.set_timeline_quality(
        db,
        tl_id,
        status=status,
        overall=overall,
        visual_exactness=overall,
        editorial_cleanliness=overall,
        n_cuts=1 if overall is not None else None,
        n_split_cuts=0 if overall is not None else None,
    )


def _export_options(db: SqliteDatabase, export_id: str) -> dict[str, Any]:
    exp = repos.get_export(db, export_id)
    assert exp is not None
    return exp["options"]  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Helpers to call both endpoints with the same fixture
# ---------------------------------------------------------------------------

def _post_render(client: TestClient, tl_id: str, min_quality: float | None = None) -> Any:
    body: dict[str, Any] = {}
    if min_quality is not None:
        body["min_quality"] = min_quality
    return client.post(f"/timelines/{tl_id}/render", json=body)


def _post_render_reel(client: TestClient, tl_id: str, min_quality: float | None = None) -> Any:
    body: dict[str, Any] = {}
    if min_quality is not None:
        body["min_quality"] = min_quality
    return client.post(f"/timelines/{tl_id}/render-reel", json=body)


# ---------------------------------------------------------------------------
# Row 1: computed, overall ≥ min_quality → 200, quality_verified=True
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", ["render", "render-reel"])
def test_gate_computed_above_threshold_200_verified(
    tmp_path: Path, endpoint: str
) -> None:
    client, db = _setup(tmp_path)
    tl_id = _make_timeline(client, db)
    _seed_quality(db, tl_id, status="computed", overall=0.8)

    if endpoint == "render":
        r = _post_render(client, tl_id, min_quality=0.7)
    else:
        r = _post_render_reel(client, tl_id, min_quality=0.7)

    assert r.status_code == 202, r.text
    opts = _export_options(db, r.json()["export_id"])
    assert opts["quality_verified"] is True
    assert opts["quality_status"] == "computed"


# ---------------------------------------------------------------------------
# Row 2: computed, overall < min_quality → 422
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", ["render", "render-reel"])
def test_gate_computed_below_threshold_422(
    tmp_path: Path, endpoint: str
) -> None:
    client, db = _setup(tmp_path)
    tl_id = _make_timeline(client, db)
    _seed_quality(db, tl_id, status="computed", overall=0.4)

    if endpoint == "render":
        r = _post_render(client, tl_id, min_quality=0.7)
    else:
        r = _post_render_reel(client, tl_id, min_quality=0.7)

    assert r.status_code == 422, r.text
    # Named reason in detail
    detail = r.json().get("detail", "")
    assert "0.40" in detail, f"expected score in detail, got: {detail}"
    assert "0.70" in detail, f"expected min_quality in detail, got: {detail}"


# ---------------------------------------------------------------------------
# Row 3: computed + no min_quality → 200, quality_verified=True
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", ["render", "render-reel"])
def test_gate_computed_no_min_quality_200_verified(
    tmp_path: Path, endpoint: str
) -> None:
    client, db = _setup(tmp_path)
    tl_id = _make_timeline(client, db)
    _seed_quality(db, tl_id, status="computed", overall=0.9)

    r = _post_render(client, tl_id) if endpoint == "render" else _post_render_reel(client, tl_id)

    assert r.status_code == 202, r.text
    opts = _export_options(db, r.json()["export_id"])
    assert opts["quality_verified"] is True
    assert opts["quality_status"] == "computed"


# ---------------------------------------------------------------------------
# Row 4: no_video → 200, quality_verified=False
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("min_q", [None, 0.5, 0.0])
@pytest.mark.parametrize("endpoint", ["render", "render-reel"])
def test_gate_no_video_200_unverified(
    tmp_path: Path, endpoint: str, min_q: float | None
) -> None:
    client, db = _setup(tmp_path)
    tl_id = _make_timeline(client, db)
    _seed_quality(db, tl_id, status="no_video")

    if endpoint == "render":
        r = _post_render(client, tl_id, min_quality=min_q)
    else:
        r = _post_render_reel(client, tl_id, min_quality=min_q)

    assert r.status_code == 202, r.text
    opts = _export_options(db, r.json()["export_id"])
    assert opts["quality_verified"] is False
    assert opts["quality_status"] == "no_video"


# ---------------------------------------------------------------------------
# Row 5: error → 200, quality_verified=False
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", ["render", "render-reel"])
def test_gate_error_200_unverified(tmp_path: Path, endpoint: str) -> None:
    client, db = _setup(tmp_path)
    tl_id = _make_timeline(client, db)
    _seed_quality(db, tl_id, status="error")

    if endpoint == "render":
        r = _post_render(client, tl_id, min_quality=0.9)
    else:
        r = _post_render_reel(client, tl_id, min_quality=0.9)

    assert r.status_code == 202, r.text
    opts = _export_options(db, r.json()["export_id"])
    assert opts["quality_verified"] is False
    assert opts["quality_status"] == "error"


# ---------------------------------------------------------------------------
# Row 6: pending (no row) → 200, quality_verified=False
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("endpoint", ["render", "render-reel"])
def test_gate_pending_no_row_200_unverified(tmp_path: Path, endpoint: str) -> None:
    client, db = _setup(tmp_path)
    tl_id = _make_timeline(client, db)
    # No quality row seeded — status is "pending"

    if endpoint == "render":
        r = _post_render(client, tl_id, min_quality=0.5)
    else:
        r = _post_render_reel(client, tl_id, min_quality=0.5)

    assert r.status_code == 202, r.text
    opts = _export_options(db, r.json()["export_id"])
    assert opts["quality_verified"] is False
    assert opts["quality_status"] == "pending"


# ---------------------------------------------------------------------------
# RenderExportOut surfaces quality_status + quality_verified
# ---------------------------------------------------------------------------

def test_list_exports_surfaces_quality_stamp(tmp_path: Path) -> None:
    """GET /projects/{pid}/exports returns quality_verified and quality_status from options."""
    client, db = _setup(tmp_path)
    tl_id = _make_timeline(client, db)
    _seed_quality(db, tl_id, status="computed", overall=0.85)

    # Get project id from timeline
    tl_row = repos.get_timeline(db, tl_id)
    assert tl_row is not None
    pid = tl_row["project_id"]

    # Trigger a render to create an export with quality stamp
    r = _post_render(client, tl_id, min_quality=0.5)
    assert r.status_code == 202, r.text

    exports = client.get(f"/projects/{pid}/exports").json()
    assert len(exports) >= 1
    exp = exports[0]
    assert exp["quality_verified"] is True
    assert exp["quality_status"] == "computed"


def test_list_exports_unverified_when_no_quality(tmp_path: Path) -> None:
    """Exports with pending quality show quality_verified=False in list."""
    client, db = _setup(tmp_path)
    tl_id = _make_timeline(client, db)
    # No quality row

    tl_row = repos.get_timeline(db, tl_id)
    assert tl_row is not None
    pid = tl_row["project_id"]

    r = _post_render(client, tl_id)
    assert r.status_code == 202

    exports = client.get(f"/projects/{pid}/exports").json()
    assert len(exports) >= 1
    exp = exports[0]
    assert exp["quality_verified"] is False
    assert exp["quality_status"] == "pending"
