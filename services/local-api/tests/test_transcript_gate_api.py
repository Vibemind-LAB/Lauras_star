"""Gate-A API: transcript confirm endpoint + reindex-on-segment-edit (Transkript-Gates Task 3)."""

from __future__ import annotations

from typing import Any

from laura.db import repos
from laura.db.database import Database


def _seed_asset_with_transcript(db: Database) -> str:
    """Project + asset + transcript run + one segment. Returns the asset_id.

    Seed sequence lifted from tests/conftest.py's ``seeded_timeline`` fixture / from
    test_semantic_sync.py's ``_seed_one_segment`` (source of truth for the
    transcript_segments column names).
    """
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="",
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4", source_path="a.mp4",
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="test", config={})
    repos.insert_segment_with_words(
        db,
        asset_id=asset["id"],
        run_id=run["id"],
        speaker_id=None,
        segment={
            "start_sample": 0, "end_sample": 48_000,
            "start_frame": 0, "end_frame": 30,
            "text": "hallo welt", "confidence": 1.0,
        },
        words=[],
    )
    return str(asset["id"])


def _first_segment_id(db: Database, asset_id: str) -> str:
    run = repos.get_latest_transcript_run(db, asset_id)
    assert run is not None
    segments = repos.get_transcript(db, asset_id, str(run["id"]))
    return str(segments[0]["id"])


def test_confirm_transcript_stamps(client: Any, db: Database) -> None:
    aid = _seed_asset_with_transcript(db)
    r = client.post(f"/assets/{aid}/transcript:confirm")
    assert r.status_code == 200, r.text
    assert r.json()["transcript_confirmed_at"] is not None
    row = repos.get_asset(db, aid)
    assert row is not None and row["transcript_confirmed_at"] is not None


def test_confirm_unknown_asset_404(client: Any) -> None:
    assert client.post("/assets/nope/transcript:confirm").status_code == 404


def test_segment_patch_triggers_reindex(client: Any, db: Database, monkeypatch: Any) -> None:
    aid = _seed_asset_with_transcript(db)
    seg_id = _first_segment_id(db, aid)
    calls: list[tuple[str, list[str]]] = []

    def _fake_reindex(_db: Database, a: str, ids: list[str]) -> int:
        calls.append((a, ids))
        return 1

    monkeypatch.setattr("laura.api.analysis.reindex_segments", _fake_reindex)
    r = client.patch(f"/transcript/segments/{seg_id}", json={"text": "Claude Code"})
    assert r.status_code == 200, r.text
    assert calls == [(aid, [seg_id])]


def test_segment_patch_does_not_reset_confirmation(client: Any, db: Database) -> None:
    """Deliberate: a correction after confirmation stays confirmed — the card shows the
    (now stale relative to the edit) confirmed state rather than silently reverting it."""
    aid = _seed_asset_with_transcript(db)
    seg_id = _first_segment_id(db, aid)
    confirm = client.post(f"/assets/{aid}/transcript:confirm")
    confirmed_at = confirm.json()["transcript_confirmed_at"]

    r = client.patch(f"/transcript/segments/{seg_id}", json={"text": "edited"})
    assert r.status_code == 200, r.text

    row = repos.get_asset(db, aid)
    assert row is not None
    assert row["transcript_confirmed_at"] == confirmed_at
