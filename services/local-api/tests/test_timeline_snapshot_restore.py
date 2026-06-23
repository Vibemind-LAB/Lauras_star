from laura.db import repos


def _set_nondefault_clip_columns(db, timeline_id):
    """Force non-default role/transition/linked columns so a column-DROPPING restore would fail."""
    clip = repos.list_timeline_clips(db, timeline_id)[0]
    with db.transaction() as conn:
        conn.execute(
            "UPDATE timeline_clips SET role='replace', transition_after_kind='crossfade', "
            "transition_after_frames=12, linked_audio_group='grp1' WHERE id=?",
            (clip["id"],),
        )


def test_restore_roundtrip_is_byte_identical_including_extra_columns(seeded_rough_cut):
    db, tl, _asset = seeded_rough_cut
    _set_nondefault_clip_columns(db, tl)
    before = repos.capture_timeline_snapshot(db, tl)
    # Sanity: the non-default columns are actually present in the snapshot.
    assert before["clips"][0]["role"] == "replace"
    assert before["clips"][0]["transition_after_kind"] == "crossfade"
    # Mutate destructively, then restore.
    repos.replace_timeline_clips(db, tl, [])           # wipe clips
    assert repos.capture_timeline_snapshot(db, tl)["clips"] == []
    repos.restore_timeline_snapshot(db, tl, before)
    after = repos.capture_timeline_snapshot(db, tl)
    assert after == before          # all columns incl role/transition_after_*/linked_audio_group/music_*


def test_restore_is_atomic_on_bad_row(seeded_rough_cut):
    db, tl, _asset = seeded_rough_cut
    good = repos.capture_timeline_snapshot(db, tl)
    bad = {**good, "audio_clips": [{"id": "x", "nonexistent_col": 1}]}  # INSERT will fail
    with __import__("pytest").raises(Exception):
        repos.restore_timeline_snapshot(db, tl, bad)
    # The clips/scenes deletes must have rolled back — nothing lost.
    assert repos.capture_timeline_snapshot(db, tl)["clips"] == good["clips"]
    assert repos.capture_timeline_snapshot(db, tl)["scenes"] == good["scenes"]
