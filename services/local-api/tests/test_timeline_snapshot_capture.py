"""Tests for repos.capture_timeline_snapshot (Task 3 — Undo/Redo)."""
from laura.db import repos


def test_capture_has_four_groups_and_full_columns(seeded_rough_cut):
    db, timeline_id, _asset = seeded_rough_cut
    snap = repos.capture_timeline_snapshot(db, timeline_id)
    assert set(snap) == {"clips", "scenes", "audio_clips", "transitions"}
    assert snap["clips"] and snap["scenes"] and snap["audio_clips"]
    with db.connection() as conn:
        clip_cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(timeline_clips)").fetchall()
        }
        scene_cols = {
            r["name"]
            for r in conn.execute("PRAGMA table_info(scenes)").fetchall()
        }
    # catches a future ADD COLUMN not captured
    assert set(snap["clips"][0]) == clip_cols
    assert set(snap["scenes"][0]) == scene_cols
    expected_clip_cols = {
        "role", "transition_after_kind", "transition_after_frames", "linked_audio_group"
    }
    assert expected_clip_cols <= clip_cols
