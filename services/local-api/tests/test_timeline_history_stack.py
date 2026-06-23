from laura.db import repos


def test_push_sets_undo_and_clears_redo(seeded_rough_cut):
    db, tl, _ = seeded_rough_cut
    repos.push_undo_checkpoint(db, tl, "Edit A")
    st = repos.get_history_state(db, tl)
    assert st["can_undo"] is True
    assert st["undo_label"] == "Edit A"
    assert st["can_redo"] is False


def test_depth_cap_keeps_newest_50(seeded_rough_cut):
    db, tl, _ = seeded_rough_cut
    for i in range(55):
        repos.push_undo_checkpoint(db, tl, f"Edit {i}")
    with db.connection() as conn:
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM timeline_history WHERE timeline_id=? AND stack='undo'", (tl,)
        ).fetchone()["c"]
    assert n == 50
    assert repos.get_history_state(db, tl)["undo_label"] == "Edit 54"
