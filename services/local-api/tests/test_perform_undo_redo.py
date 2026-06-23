import pytest

from laura.db import repos
from laura.editing import history


def test_undo_then_redo_round_trips(seeded_rough_cut):
    db, tl, _ = seeded_rough_cut
    a = repos.capture_timeline_snapshot(db, tl)            # state A: 1 clip
    with history.timeline_checkpoint(db, tl, "Edit"):
        repos.replace_timeline_clips(db, tl, [])           # state B: 0 clips
    b = repos.capture_timeline_snapshot(db, tl)
    history.perform_undo(db, tl)
    assert repos.capture_timeline_snapshot(db, tl)["clips"] == a["clips"]
    history.perform_redo(db, tl)
    assert repos.capture_timeline_snapshot(db, tl)["clips"] == b["clips"]


def test_undo_on_empty_stack_raises(seeded_rough_cut):
    db, tl, _ = seeded_rough_cut
    with pytest.raises(history.HistoryEmpty):
        history.perform_undo(db, tl)


def test_new_edit_clears_redo(seeded_rough_cut):
    db, tl, _ = seeded_rough_cut
    with history.timeline_checkpoint(db, tl, "E1"):
        repos.replace_timeline_clips(db, tl, [])
    history.perform_undo(db, tl)
    assert repos.get_history_state(db, tl)["can_redo"] is True
    with history.timeline_checkpoint(db, tl, "E2"):
        repos.replace_timeline_clips(db, tl, [])
    assert repos.get_history_state(db, tl)["can_redo"] is False
