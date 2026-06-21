"""P4-T1 — per-input policy model + precedence resolver + persistence.

TDD suite: written first (fails before implementation), green after.
All files under test are new:
  - laura/policy/policy.py   (Policy, ResolvedPolicy, parse_policy, policy_to_str, resolve_policy)
  - laura/policy/store.py    (set_asset_policy, get_asset_policy)
  - laura/policy/__init__.py (public re-exports)
  - db/migrations/0028_asset_policies.sql
"""

from __future__ import annotations

import pytest

from laura.db import repos
from laura.db.database import Database
from laura.policy import (
    Policy,
    ResolvedPolicy,
    get_asset_policy,
    parse_policy,
    policy_to_str,
    resolve_policy,
    set_asset_policy,
)

# ---------------------------------------------------------------------------
# parse_policy — valid inputs
# ---------------------------------------------------------------------------


def test_parse_auto() -> None:
    p = parse_policy("auto")
    assert p.mode == "auto"
    assert p.threshold is None


def test_parse_human() -> None:
    p = parse_policy("human")
    assert p.mode == "human"
    assert p.threshold is None


def test_parse_threshold_point_eight() -> None:
    p = parse_policy("threshold:0.8")
    assert p.mode == "threshold"
    assert p.threshold == pytest.approx(0.8)


def test_parse_threshold_zero() -> None:
    p = parse_policy("threshold:0")
    assert p.mode == "threshold"
    assert p.threshold == pytest.approx(0.0)


def test_parse_threshold_one() -> None:
    p = parse_policy("threshold:1")
    assert p.mode == "threshold"
    assert p.threshold == pytest.approx(1.0)


def test_parse_case_insensitive_threshold() -> None:
    p = parse_policy("THRESHOLD:0.5")
    assert p.mode == "threshold"
    assert p.threshold == pytest.approx(0.5)


def test_parse_case_insensitive_auto() -> None:
    p = parse_policy("AUTO")
    assert p.mode == "auto"


def test_parse_case_insensitive_human() -> None:
    p = parse_policy("HUMAN")
    assert p.mode == "human"


def test_parse_whitespace_stripped_auto() -> None:
    p = parse_policy("  auto  ")
    assert p.mode == "auto"


def test_parse_whitespace_stripped_threshold() -> None:
    p = parse_policy("  threshold:0.3  ")
    assert p.mode == "threshold"
    assert p.threshold == pytest.approx(0.3)


# ---------------------------------------------------------------------------
# parse_policy — invalid inputs → ValueError
# ---------------------------------------------------------------------------


def test_parse_threshold_no_value_raises() -> None:
    with pytest.raises(ValueError):
        parse_policy("threshold")


def test_parse_threshold_above_one_raises() -> None:
    with pytest.raises(ValueError):
        parse_policy("threshold:2")


def test_parse_threshold_negative_raises() -> None:
    with pytest.raises(ValueError):
        parse_policy("threshold:-1")


def test_parse_unknown_mode_raises() -> None:
    with pytest.raises(ValueError):
        parse_policy("foo")


def test_parse_empty_string_raises() -> None:
    with pytest.raises(ValueError):
        parse_policy("")


def test_parse_threshold_colon_no_number_raises() -> None:
    with pytest.raises(ValueError):
        parse_policy("threshold:")


# ---------------------------------------------------------------------------
# policy_to_str — output format
# ---------------------------------------------------------------------------


def test_to_str_auto() -> None:
    assert policy_to_str(Policy(mode="auto")) == "auto"


def test_to_str_human() -> None:
    assert policy_to_str(Policy(mode="human")) == "human"


def test_to_str_threshold() -> None:
    result = policy_to_str(Policy(mode="threshold", threshold=0.8))
    assert result == "threshold:0.80"


def test_to_str_threshold_zero() -> None:
    result = policy_to_str(Policy(mode="threshold", threshold=0.0))
    assert result == "threshold:0.00"


def test_to_str_threshold_one() -> None:
    result = policy_to_str(Policy(mode="threshold", threshold=1.0))
    assert result == "threshold:1.00"


# ---------------------------------------------------------------------------
# round-trip: parse(to_str(p)) == p
# ---------------------------------------------------------------------------


def test_roundtrip_auto() -> None:
    p = Policy(mode="auto")
    assert parse_policy(policy_to_str(p)) == p


def test_roundtrip_human() -> None:
    p = Policy(mode="human")
    assert parse_policy(policy_to_str(p)) == p


def test_roundtrip_threshold() -> None:
    p = Policy(mode="threshold", threshold=0.75)
    assert parse_policy(policy_to_str(p)) == p


# ---------------------------------------------------------------------------
# resolve_policy — precedence
# ---------------------------------------------------------------------------


def test_resolve_row_beats_all() -> None:
    r = resolve_policy(row="human", pattern="auto", env="auto", default="auto")
    assert r.policy.mode == "human"
    assert r.source == "row"


def test_resolve_pattern_beats_env_and_default() -> None:
    r = resolve_policy(row=None, pattern="human", env="auto", default="auto")
    assert r.policy.mode == "human"
    assert r.source == "pattern"


def test_resolve_env_beats_default() -> None:
    r = resolve_policy(row=None, pattern=None, env="human", default="auto")
    assert r.policy.mode == "human"
    assert r.source == "env"


def test_resolve_default_used_when_nothing_else() -> None:
    r = resolve_policy(row=None, pattern=None, env=None, default="human")
    assert r.policy.mode == "human"
    assert r.source == "default"


def test_resolve_default_auto_when_nothing_provided() -> None:
    r = resolve_policy()
    assert r.policy.mode == "auto"
    assert r.source == "default"


def test_resolve_empty_string_skipped_like_none() -> None:
    r = resolve_policy(row="", pattern="", env="", default="human")
    assert r.policy.mode == "human"
    assert r.source == "default"


def test_resolve_empty_row_falls_through_to_pattern() -> None:
    r = resolve_policy(row="", pattern="auto")
    assert r.source == "pattern"


def test_resolve_source_tagged_correctly_for_threshold() -> None:
    r = resolve_policy(env="threshold:0.6")
    assert r.policy.mode == "threshold"
    assert r.policy.threshold == pytest.approx(0.6)
    assert r.source == "env"


def test_resolve_bad_row_value_raises_not_silently_falls_through() -> None:
    """A present-but-unparseable value must raise ValueError, not silently skip."""
    with pytest.raises(ValueError):
        resolve_policy(row="garbage")


def test_resolve_bad_pattern_value_raises() -> None:
    with pytest.raises(ValueError):
        resolve_policy(row=None, pattern="threshold:99")


# ---------------------------------------------------------------------------
# ResolvedPolicy dataclass fields
# ---------------------------------------------------------------------------


def test_resolved_policy_has_policy_and_source() -> None:
    r = resolve_policy(row="auto")
    assert isinstance(r, ResolvedPolicy)
    assert isinstance(r.policy, Policy)
    assert r.source in {"row", "pattern", "env", "default"}


# ---------------------------------------------------------------------------
# Migration: 0028_asset_policies table
# ---------------------------------------------------------------------------


def test_migration_creates_asset_policies_table(db: Database) -> None:
    with db.connection() as conn:
        cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(asset_policies)").fetchall()
        }
    assert {"asset_id", "policy", "policy_source", "resolved_at"} <= cols


def test_migration_has_policy_source_check_constraint(db: Database) -> None:
    """Inserting an invalid policy_source must fail the CHECK constraint."""
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mov", source_path="a.mov"
    )
    import sqlite3

    with pytest.raises(sqlite3.IntegrityError), db.transaction() as conn:
        conn.execute(
            "INSERT INTO asset_policies(asset_id, policy, policy_source, resolved_at) "
            "VALUES (?, ?, ?, ?)",
            (asset["id"], "auto", "bogus", "2026-01-01T00:00:00.000000Z"),
        )


# ---------------------------------------------------------------------------
# store.set_asset_policy / get_asset_policy
# ---------------------------------------------------------------------------


def _make_asset(db: Database) -> str:
    project = repos.create_project(
        db, name="P", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/ws"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mov", source_path="a.mov"
    )
    return str(asset["id"])


def test_set_and_get_round_trip(db: Database) -> None:
    asset_id = _make_asset(db)
    row = set_asset_policy(db, asset_id, policy="auto", source="default")
    assert row["asset_id"] == asset_id
    assert row["policy"] == "auto"
    assert row["policy_source"] == "default"
    assert row["resolved_at"]

    fetched = get_asset_policy(db, asset_id)
    assert fetched is not None
    assert fetched["asset_id"] == asset_id
    assert fetched["policy"] == "auto"
    assert fetched["policy_source"] == "default"


def test_get_returns_none_for_unknown_asset(db: Database) -> None:
    assert get_asset_policy(db, "no-such-asset") is None


def test_upsert_replaces_previous_row(db: Database) -> None:
    asset_id = _make_asset(db)
    set_asset_policy(db, asset_id, policy="auto", source="default")
    set_asset_policy(db, asset_id, policy="human", source="row")

    with db.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) AS c FROM asset_policies WHERE asset_id=?", (asset_id,)
        ).fetchone()["c"]
    assert count == 1

    fetched = get_asset_policy(db, asset_id)
    assert fetched is not None
    assert fetched["policy"] == "human"
    assert fetched["policy_source"] == "row"


def test_set_bad_policy_raises_value_error(db: Database) -> None:
    asset_id = _make_asset(db)
    with pytest.raises(ValueError):
        set_asset_policy(db, asset_id, policy="garbage", source="default")


def test_set_bad_source_raises_value_error(db: Database) -> None:
    asset_id = _make_asset(db)
    with pytest.raises(ValueError):
        set_asset_policy(db, asset_id, policy="auto", source="bogus")


def test_set_threshold_policy_persisted_correctly(db: Database) -> None:
    asset_id = _make_asset(db)
    set_asset_policy(db, asset_id, policy="threshold:0.75", source="pattern")
    fetched = get_asset_policy(db, asset_id)
    assert fetched is not None
    assert fetched["policy"] == "threshold:0.75"
    assert fetched["policy_source"] == "pattern"
