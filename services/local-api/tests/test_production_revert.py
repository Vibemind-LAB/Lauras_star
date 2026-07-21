"""POST /production/{sid}/revert: revert one artifact, heal the suffix — no job, no agent.

The chips show stale/restored since the provenance arc, but the user had no way to ACT on
them: revert_artifact exists only as an agent tool (a chat revert costs a nondeterministic
team run). This endpoint mirrors the tool's validation, runs board.revert +
restore_coherent_suffix synchronously, and returns the same enriched status as the GET so
the UI updates without a second fetch (spec 2026-07-21-revert-ui-design.md).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.main import create_app
from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BoardMeta,
    Chapter,
    ContactSheet,
    ContactSheetTile,
    Cutlist,
    CutSegment,
    QaReport,
    RenderReport,
    Script,
    ScriptLine,
    Storyline,
    VoiceArtifact,
    content_hash,
)
from laura.short_creator.production_orchestrator import board_root_for

_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _app(tmp_path: Path) -> tuple[TestClient, Any]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    return TestClient(app), app.state.db


def _seed_session(client: TestClient, db: Any, monkeypatch: Any) -> tuple[str, str]:
    """Project + asset + production session whose job is finished (not 409-blocking)."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    r = client.post(
        f"/assets/{asset['id']}/production",
        json={"task": "t", "target_seconds": 30},
        headers=_H,
    )
    assert r.status_code == 202, r.text
    session_id = str(r.json()["session_id"])
    session = repos.get_production_session(db, session_id)
    assert session is not None
    with db.transaction() as conn:
        conn.execute(
            "UPDATE jobs SET status='succeeded', finished_at='2026-07-21T00:00:00+00:00' "
            "WHERE id=?",
            (str(session["latest_job_id"]),),
        )
    return session_id, str(asset["id"])


def _seed_chain(db: Any, asset_id: str, session_id: str) -> Board:
    """A board with storyline->script->voice->cutlist(v1, v2)->render->qa, parents stamped
    the way the write sites do, so reverting cutlist to v1 leaves render/qa restorable only
    if THEIR recorded cutlist hash matches v1 — here they were built on v1, so the walk
    brings them back after the v2 detour is reverted."""
    board = Board.create(
        board_root_for(db, asset_id, session_id),
        BoardMeta(
            session_id=session_id,
            asset_id=asset_id,
            created_utc="2026-07-21T00:00:00+00:00",
            task="t",
            language="English",
            target_seconds=30.0,
        ),
    )
    board.save(
        "storyline",
        Storyline(
            red_thread="r",
            arc=[
                Chapter(chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=10.0)
            ],
        ),
    )
    storyline = board.load("storyline")
    assert storyline is not None
    board.save(
        "script",
        Script(
            language="English",
            lines=[ScriptLine(chapter=1, scene_number=1, text="one line")],
        ).model_copy(update={"parents": {"storyline": content_hash(storyline)}}),
    )
    script = board.load("script")
    assert script is not None
    board.save(
        "voice",
        VoiceArtifact(
            script_hash="k",
            mp3_path="v.mp3",
            parents={"storyline": content_hash(storyline), "script": content_hash(script)},
        ),
    )
    voice = board.load("voice")
    assert voice is not None

    def _cut(end: int) -> Cutlist:
        return Cutlist(
            segments=[CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=end)],
            parents={
                "storyline": content_hash(storyline),
                "script": content_hash(script),
                "voice": content_hash(voice),
            },
        )

    board.save("cutlist", _cut(240))  # v1 — the take sheet/render/qa were built on
    cut_v1 = board.load("cutlist")
    assert cut_v1 is not None
    # The sheet must exist: the walk ends at the FIRST missing link with no acceptable
    # candidate, and contact_sheet sits between cutlist and render in the chain.
    board.save(
        "contact_sheet",
        ContactSheet(
            png_path="s.png",
            cols=1,
            rows=1,
            tiles=[ContactSheetTile(order=0, scene_number=1, frame=100, label="1")],
            parents={"cutlist": content_hash(cut_v1)},
        ),
    )
    board.save(
        "render_report",
        RenderReport(
            export_id="e1",
            video_s=8.0,
            width=1920,
            height=1080,
            parents={
                "storyline": content_hash(storyline),
                "script": content_hash(script),
                "voice": content_hash(voice),
                "cutlist": content_hash(cut_v1),
            },
        ),
    )
    render = board.load("render_report")
    assert render is not None
    board.save(
        "qa_report",
        QaReport(verdict="ship", findings=[], parents={"render_report": content_hash(render)}),
    )
    board.save("cutlist", _cut(480))  # v2 detour — archives v1's sheetless suffix
    return board


def test_revert_cutlist_heals_the_suffix_and_returns_fresh_status(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _app(tmp_path)
    session_id, asset_id = _seed_session(client, db, monkeypatch)
    _seed_chain(db, asset_id, session_id)

    r = client.post(
        f"/production/{session_id}/revert",
        json={"artifact": "cutlist", "version": 1},
        headers=_H,
    )

    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["artifact"] == "cutlist" and body["version"] == 1
    # v2 had already wiped render/qa when it was saved, so nothing was present to invalidate.
    assert body["invalidated"] == []
    # The walk brings back v1's own sheet, render and qa (their recorded parent hashes
    # match the reverted v1 instance, link by link).
    assert body["restored"] == ["contact_sheet", "render_report", "qa_report"]
    assert body["status"]["board_ready"] is True
    assert body["status"]["artifacts"]["render_report"]["version"] is not None
    assert body["status"]["artifacts"]["qa_report"]["stale"] is False


def test_revert_rejects_while_a_job_is_queued_or_running(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _app(tmp_path)
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a", source_path="/tmp/a.mp4"
    )
    r = client.post(
        f"/assets/{asset['id']}/production",
        json={"task": "t", "target_seconds": 30},
        headers=_H,
    )
    session_id = str(r.json()["session_id"])  # job stays queued

    r2 = client.post(
        f"/production/{session_id}/revert",
        json={"artifact": "cutlist", "version": 1},
        headers=_H,
    )

    assert r2.status_code == 409


def test_revert_404_on_unknown_session_and_missing_board(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _app(tmp_path)
    r = client.post(
        "/production/nope/revert", json={"artifact": "cutlist", "version": 1}, headers=_H
    )
    assert r.status_code == 404

    session_id, _asset_id = _seed_session(client, db, monkeypatch)  # no board built
    r2 = client.post(
        f"/production/{session_id}/revert",
        json={"artifact": "cutlist", "version": 1},
        headers=_H,
    )
    assert r2.status_code == 404


def test_revert_422_on_unknown_artifact_and_unarchived_version(
    tmp_path: Path, monkeypatch: Any
) -> None:
    client, db = _app(tmp_path)
    session_id, asset_id = _seed_session(client, db, monkeypatch)
    _seed_chain(db, asset_id, session_id)

    r = client.post(
        f"/production/{session_id}/revert",
        json={"artifact": "bogus", "version": 1},
        headers=_H,
    )
    assert r.status_code == 422
    assert "valid:" in r.json()["detail"]

    r2 = client.post(
        f"/production/{session_id}/revert",
        json={"artifact": "voice", "version": 99},
        headers=_H,
    )
    assert r2.status_code == 422
    assert r2.json()["detail"] == "no archived voice v99"
