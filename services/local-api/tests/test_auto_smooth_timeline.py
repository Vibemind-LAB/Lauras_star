"""S6 — auto_smooth_timeline: heuristic crossfade wiring tests (model-free).

All tests use StubVlmBackend via the LAURA_VLM_MODEL env being unset (so default_backend()
returns None and the function falls back to StubVlmBackend + no-op frame extractor).
No ffmpeg, no model, no proxy files required.
"""

from __future__ import annotations

import pytest

from laura.db import repos
from laura.db.database import Database
from laura.scenes import build as build_module  # noqa: E402
from laura.scenes.build import auto_smooth_timeline

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_project(db: Database) -> tuple[str, str]:
    """Create a project + asset; return (project_id, asset_id)."""
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="a.mp4",
        source_path="/tmp/a.mp4",
    )
    return str(project["id"]), str(asset["id"])


def _rough_cut_timeline(db: Database, project_id: str) -> str:
    tl = repos.create_timeline(db, project_id=project_id, name="rc", kind="rough_cut")
    return str(tl["id"])


def _add_clip(
    db: Database,
    timeline_id: str,
    asset_id: str,
    src_in: int,
    src_out: int,
    seq_in: int,
    seq_out: int,
) -> str:
    repos.add_timeline_clip(
        db,
        timeline_id=timeline_id,
        asset_id=asset_id,
        src_in_frame=src_in,
        src_out_frame_exclusive=src_out,
        seq_in_frame=seq_in,
        seq_out_frame_exclusive=seq_out,
    )
    clips = repos.list_timeline_clips(db, timeline_id)
    # return last added clip id
    ordered = sorted(clips, key=lambda c: int(c["seq_in_frame"]))
    return str(ordered[-1]["id"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_contiguous_same_source_gets_crossfade(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two clips from the same asset, source-contiguous (no gap) → boundary gets a crossfade."""
    monkeypatch.delenv("LAURA_VLM_MODEL", raising=False)
    monkeypatch.delenv("LAURA_VLM", raising=False)
    monkeypatch.delenv("LAURA_AUTO_SMOOTH", raising=False)

    project_id, asset_id = _seed_project(db)
    tl_id = _rough_cut_timeline(db, project_id)
    # Clip A: src [0,100), seq [0,100)
    clip_a_id = _add_clip(db, tl_id, asset_id, 0, 100, 0, 100)
    # Clip B: src [100,200), seq [100,200) — contiguous same-source, gap==0
    _add_clip(db, tl_id, asset_id, 100, 200, 100, 200)

    result = auto_smooth_timeline(db, tl_id, k=6)

    assert result == {"boundaries": 1, "crossfades_applied": 1}
    # Clip A's transition_after must now be crossfade with k frames
    clips = repos.list_timeline_clips(db, tl_id)
    ordered = sorted(clips, key=lambda c: int(c["seq_in_frame"]))
    clip_a = next(c for c in ordered if str(c["id"]) == clip_a_id)
    assert clip_a["transition_after_kind"] == "crossfade"
    assert int(clip_a["transition_after_frames"]) == 6


def test_normal_boundary_stays_hard(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boundary between two *different* assets → StubVlmBackend says 'none' → stays hard cut."""
    monkeypatch.delenv("LAURA_VLM_MODEL", raising=False)
    monkeypatch.delenv("LAURA_VLM", raising=False)
    monkeypatch.delenv("LAURA_AUTO_SMOOTH", raising=False)

    project_id, asset_a_id = _seed_project(db)
    project = repos.get_project(db, project_id)
    assert project is not None
    asset_b = repos.create_asset(
        db,
        project_id=project_id,
        type="video",
        display_name="b.mp4",
        source_path="/tmp/b.mp4",
    )
    asset_b_id = str(asset_b["id"])

    tl_id = _rough_cut_timeline(db, project_id)
    clip_a_id = _add_clip(db, tl_id, asset_a_id, 0, 100, 0, 100)
    _add_clip(db, tl_id, asset_b_id, 0, 100, 100, 200)

    result = auto_smooth_timeline(db, tl_id, k=6)

    assert result["crossfades_applied"] == 0
    clips = repos.list_timeline_clips(db, tl_id)
    clip_a = next(c for c in clips if str(c["id"]) == clip_a_id)
    # transition_after_kind should be None/null or 'hard' — either way, NOT crossfade
    kind = clip_a.get("transition_after_kind")
    assert kind != "crossfade"


def test_same_asset_with_gap_stays_hard(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Same asset but source gap (src_in_b > src_out_a) → NOT same_source → no crossfade."""
    monkeypatch.delenv("LAURA_VLM_MODEL", raising=False)
    monkeypatch.delenv("LAURA_VLM", raising=False)
    monkeypatch.delenv("LAURA_AUTO_SMOOTH", raising=False)

    project_id, asset_id = _seed_project(db)
    tl_id = _rough_cut_timeline(db, project_id)
    clip_a_id = _add_clip(db, tl_id, asset_id, 0, 100, 0, 100)
    # gap: src [200, 300) — not contiguous with [0,100)
    _add_clip(db, tl_id, asset_id, 200, 300, 100, 200)

    result = auto_smooth_timeline(db, tl_id, k=6)

    assert result["crossfades_applied"] == 0
    clips = repos.list_timeline_clips(db, tl_id)
    clip_a = next(c for c in clips if str(c["id"]) == clip_a_id)
    assert clip_a.get("transition_after_kind") != "crossfade"


def test_gate_disables_smoothing(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LAURA_AUTO_SMOOTH=0 → skipped immediately, no DB writes."""
    monkeypatch.setenv("LAURA_AUTO_SMOOTH", "0")

    project_id, asset_id = _seed_project(db)
    tl_id = _rough_cut_timeline(db, project_id)
    _add_clip(db, tl_id, asset_id, 0, 100, 0, 100)
    _add_clip(db, tl_id, asset_id, 100, 200, 100, 200)

    result = auto_smooth_timeline(db, tl_id, k=6)

    assert result == {"skipped": 1}
    # No reviews should have been written
    reviews = repos.list_transition_reviews(db, tl_id)
    assert reviews == []


def test_gate_false_string(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LAURA_AUTO_SMOOTH=false also triggers the gate."""
    monkeypatch.setenv("LAURA_AUTO_SMOOTH", "false")

    project_id, asset_id = _seed_project(db)
    tl_id = _rough_cut_timeline(db, project_id)

    result = auto_smooth_timeline(db, tl_id, k=6)

    assert result == {"skipped": 1}


def test_empty_timeline_is_robust(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Empty timeline → no boundaries → returns zeros, no crash."""
    monkeypatch.delenv("LAURA_VLM_MODEL", raising=False)
    monkeypatch.delenv("LAURA_VLM", raising=False)
    monkeypatch.delenv("LAURA_AUTO_SMOOTH", raising=False)

    project_id, _ = _seed_project(db)
    tl_id = _rough_cut_timeline(db, project_id)

    result = auto_smooth_timeline(db, tl_id)

    assert result == {"boundaries": 0, "crossfades_applied": 0}


def test_idempotent_double_call(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Calling auto_smooth_timeline twice produces the same result without errors."""
    monkeypatch.delenv("LAURA_VLM_MODEL", raising=False)
    monkeypatch.delenv("LAURA_VLM", raising=False)
    monkeypatch.delenv("LAURA_AUTO_SMOOTH", raising=False)

    project_id, asset_id = _seed_project(db)
    tl_id = _rough_cut_timeline(db, project_id)
    _add_clip(db, tl_id, asset_id, 0, 100, 0, 100)
    _add_clip(db, tl_id, asset_id, 100, 200, 100, 200)

    result1 = auto_smooth_timeline(db, tl_id, k=6)
    result2 = auto_smooth_timeline(db, tl_id, k=6)

    # Both calls succeed and the crossfade count matches
    assert result1 == {"boundaries": 1, "crossfades_applied": 1}
    # Second call: review is cached (same digest) + apply_fix sets same value again — still "ok"
    assert result2["crossfades_applied"] == 1

    # Only one review row in DB (idempotent upsert)
    reviews = repos.list_transition_reviews(db, tl_id)
    assert len(reviews) == 1

    # The clip still carries exactly one crossfade (not doubled)
    clips = repos.list_timeline_clips(db, tl_id)
    ordered = sorted(clips, key=lambda c: int(c["seq_in_frame"]))
    assert ordered[0]["transition_after_kind"] == "crossfade"
    assert int(ordered[0]["transition_after_frames"]) == 6


# ---------------------------------------------------------------------------
# New tests for adversarial-review fixes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", ["off", "OFF", "FALSE", "No", "NO"])
def test_gate_extended_vocabulary(
    db: Database, monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    """LAURA_AUTO_SMOOTH=off/OFF/FALSE/No/NO → gate skips (case-insensitive, sibling-compatible)."""
    monkeypatch.setenv("LAURA_AUTO_SMOOTH", value)

    project_id, asset_id = _seed_project(db)
    tl_id = _rough_cut_timeline(db, project_id)
    _add_clip(db, tl_id, asset_id, 0, 100, 0, 100)
    _add_clip(db, tl_id, asset_id, 100, 200, 100, 200)

    result = auto_smooth_timeline(db, tl_id, k=6)

    assert result == {"skipped": 1}
    reviews = repos.list_transition_reviews(db, tl_id)
    assert reviews == []


def test_single_clip_timeline_no_boundaries(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Timeline with exactly 1 clip has no boundaries → returns zeros, no exception."""
    monkeypatch.delenv("LAURA_VLM_MODEL", raising=False)
    monkeypatch.delenv("LAURA_VLM", raising=False)
    monkeypatch.delenv("LAURA_AUTO_SMOOTH", raising=False)

    project_id, asset_id = _seed_project(db)
    tl_id = _rough_cut_timeline(db, project_id)
    _add_clip(db, tl_id, asset_id, 0, 100, 0, 100)

    result = auto_smooth_timeline(db, tl_id, k=6)

    assert result == {"boundaries": 0, "crossfades_applied": 0}


def test_vlm_backend_error_returns_safe_dict_not_raise(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If run_transition_review raises (e.g. VLM backend/inference error), auto_smooth_timeline
    must NOT propagate — it returns a safe dict so grouping can still proceed."""
    monkeypatch.delenv("LAURA_VLM_MODEL", raising=False)
    monkeypatch.delenv("LAURA_VLM", raising=False)
    monkeypatch.delenv("LAURA_AUTO_SMOOTH", raising=False)

    project_id, asset_id = _seed_project(db)
    tl_id = _rough_cut_timeline(db, project_id)
    _add_clip(db, tl_id, asset_id, 0, 100, 0, 100)
    _add_clip(db, tl_id, asset_id, 100, 200, 100, 200)

    # Monkeypatch run_transition_review on the build module to simulate a VLM/backend crash
    def _boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("simulated VLM inference failure")

    monkeypatch.setattr(build_module, "run_transition_review", _boom)

    # Must NOT raise — must return the safe error dict
    result = auto_smooth_timeline(db, tl_id, k=6)

    assert "error" in result
    assert result["boundaries"] == 0
    assert result["crossfades_applied"] == 0
    # Grouping would proceed normally — no scenes were lost (no exception escaped)
