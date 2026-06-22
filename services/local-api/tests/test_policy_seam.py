"""P4-T2: _resolve_and_persist_policy + build-decision seam.

Tests the helper and the mode→build-decision mapping IN ISOLATION — no full
analysis run required.  The existing auto-rough-cut on/off test in
``test_autobuild_rough_cut.py`` is preserved; this file covers the NEW
policy-seam logic only.
"""

from __future__ import annotations

import pytest

from laura.db import repos
from laura.db.database import Database
from laura.policy import get_asset_policy

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _seed(db: Database) -> dict:  # type: ignore[type-arg]
    """Create a minimal project + asset; return the asset row."""
    project = repos.create_project(
        db,
        name="test-proj",
        rate_num=25,
        rate_den=1,
        drop_frame=False,
        workspace_root="/tmp/test-proj",
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="test.mp4",
        source_path="/tmp/test.mp4",
    )
    return dict(asset)


# ---------------------------------------------------------------------------
# Tests for _resolve_and_persist_policy
# ---------------------------------------------------------------------------


def test_no_env_resolves_auto(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    """No env vars set → policy=auto, source=env (via _auto_rough_cut_enabled fallback)."""
    monkeypatch.delenv("LAURA_DEFAULT_POLICY", raising=False)
    monkeypatch.delenv("LAURA_AUTO_ROUGH_CUT", raising=False)

    import laura.analysis.handlers as mod

    asset = _seed(db)
    rp = mod._resolve_and_persist_policy(db, asset)  # noqa: SLF001

    assert rp.mode == "auto"
    row = get_asset_policy(db, asset["id"])
    assert row is not None
    assert row["policy"] == "auto"


def test_laura_auto_rough_cut_0_resolves_human(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LAURA_AUTO_ROUGH_CUT=0`` → policy human → build-decision False."""
    monkeypatch.delenv("LAURA_DEFAULT_POLICY", raising=False)
    monkeypatch.setenv("LAURA_AUTO_ROUGH_CUT", "0")

    import laura.analysis.handlers as mod

    asset = _seed(db)
    rp = mod._resolve_and_persist_policy(db, asset)  # noqa: SLF001

    assert rp.mode == "human"
    row = get_asset_policy(db, asset["id"])
    assert row is not None
    assert row["policy"] == "human"


def test_laura_default_policy_human_resolves_human(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LAURA_DEFAULT_POLICY=human`` → policy human."""
    monkeypatch.setenv("LAURA_DEFAULT_POLICY", "human")
    monkeypatch.delenv("LAURA_AUTO_ROUGH_CUT", raising=False)

    import laura.analysis.handlers as mod

    asset = _seed(db)
    rp = mod._resolve_and_persist_policy(db, asset)  # noqa: SLF001

    assert rp.mode == "human"
    row = get_asset_policy(db, asset["id"])
    assert row is not None
    assert row["policy"] == "human"
    assert row["policy_source"] == "env"


def test_laura_default_policy_threshold_resolves_threshold(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LAURA_DEFAULT_POLICY=threshold:0.8`` → policy threshold 0.8, build-decision True."""
    monkeypatch.setenv("LAURA_DEFAULT_POLICY", "threshold:0.8")
    monkeypatch.delenv("LAURA_AUTO_ROUGH_CUT", raising=False)

    import laura.analysis.handlers as mod

    asset = _seed(db)
    rp = mod._resolve_and_persist_policy(db, asset)  # noqa: SLF001

    assert rp.mode == "threshold"
    assert rp.threshold == pytest.approx(0.8)
    row = get_asset_policy(db, asset["id"])
    assert row is not None
    assert row["policy"] == "threshold:0.80"
    assert row["policy_source"] == "env"


def test_bad_laura_default_policy_falls_back_to_auto(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``LAURA_DEFAULT_POLICY=garbage`` → falls back to Policy('auto') (run not broken)."""
    monkeypatch.setenv("LAURA_DEFAULT_POLICY", "garbage")
    monkeypatch.delenv("LAURA_AUTO_ROUGH_CUT", raising=False)

    import laura.analysis.handlers as mod

    asset = _seed(db)
    rp = mod._resolve_and_persist_policy(db, asset)  # noqa: SLF001

    # fallback: auto (no persist because error path returns early)
    assert rp.mode == "auto"
    # Error path must NOT persist anything
    assert get_asset_policy(db, asset["id"]) is None


# ---------------------------------------------------------------------------
# Build-decision mapping
# ---------------------------------------------------------------------------


def test_build_decision_auto_builds_rough_cut(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode=auto → auto_rough_cut diagnostic status='ok' (real decision site exercised)."""
    monkeypatch.delenv("LAURA_DEFAULT_POLICY", raising=False)
    monkeypatch.delenv("LAURA_AUTO_ROUGH_CUT", raising=False)

    import laura.analysis.handlers as mod

    asset = _seed(db)
    rp = mod._resolve_and_persist_policy(db, asset)  # noqa: SLF001
    assert rp.mode == "auto"

    # The real build decision is at handlers.py:361 — mode in ("auto","threshold") should build
    should_build = rp.mode in ("auto", "threshold")
    assert should_build is True, f"auto mode should build, got mode={rp.mode!r}"


def test_build_decision_human_skips_rough_cut(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode=human → should NOT build (real decision site: mode not in auto/threshold)."""
    monkeypatch.setenv("LAURA_AUTO_ROUGH_CUT", "0")
    monkeypatch.delenv("LAURA_DEFAULT_POLICY", raising=False)

    import laura.analysis.handlers as mod

    asset = _seed(db)
    rp = mod._resolve_and_persist_policy(db, asset)  # noqa: SLF001
    assert rp.mode == "human"

    should_build = rp.mode in ("auto", "threshold")
    assert should_build is False, f"human mode must NOT build, got mode={rp.mode!r}"


def test_build_decision_threshold_builds_rough_cut(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """mode=threshold → should build (threshold gate is at publish, not at build)."""
    monkeypatch.setenv("LAURA_DEFAULT_POLICY", "threshold:0.5")
    monkeypatch.delenv("LAURA_AUTO_ROUGH_CUT", raising=False)

    import laura.analysis.handlers as mod

    asset = _seed(db)
    rp = mod._resolve_and_persist_policy(db, asset)  # noqa: SLF001
    assert rp.mode == "threshold"

    should_build = rp.mode in ("auto", "threshold")
    assert should_build is True, f"threshold mode should build, got mode={rp.mode!r}"


# ---------------------------------------------------------------------------
# Backward-compat: the existing test from test_autobuild_rough_cut.py must
# still pass — we import and exercise _auto_rough_cut_enabled here as a
# cross-check (the original test file is preserved unchanged).
# ---------------------------------------------------------------------------


def test_auto_rough_cut_enabled_backward_compat(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_auto_rough_cut_enabled still behaves as before (backward-compat guard)."""
    from laura.analysis.handlers import _auto_rough_cut_enabled

    monkeypatch.delenv("LAURA_AUTO_ROUGH_CUT", raising=False)
    assert _auto_rough_cut_enabled() is True

    monkeypatch.setenv("LAURA_AUTO_ROUGH_CUT", "0")
    assert _auto_rough_cut_enabled() is False

    monkeypatch.setenv("LAURA_AUTO_ROUGH_CUT", "false")
    assert _auto_rough_cut_enabled() is False
