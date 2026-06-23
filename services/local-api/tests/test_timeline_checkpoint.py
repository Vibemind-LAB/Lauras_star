"""Unit test: timeline_checkpoint pushes a pre-edit snapshot onto the undo stack."""

from __future__ import annotations

import json

from laura.db import repos
from laura.editing.history import timeline_checkpoint


def test_checkpoint_pushes_pre_edit_snapshot(seeded_rough_cut):
    db, tl, _ = seeded_rough_cut
    before = repos.capture_timeline_snapshot(db, tl)
    with timeline_checkpoint(db, tl, "Wörter gelöscht"):
        repos.replace_timeline_clips(db, tl, [])  # the "edit"
    st = repos.get_history_state(db, tl)
    assert st["can_undo"] and st["undo_label"] == "Wörter gelöscht"
    with db.connection() as conn:
        payload = conn.execute(
            "SELECT payload_json FROM timeline_history "
            "WHERE timeline_id=? ORDER BY seq_no DESC LIMIT 1",
            (tl,),
        ).fetchone()["payload_json"]
    assert json.loads(payload)["clips"] == before["clips"]  # the PRE-edit snapshot
