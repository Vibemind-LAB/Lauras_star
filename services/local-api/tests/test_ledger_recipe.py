"""P3-T2 — recipe hash + content-addressed short_id + mint_short_run.

TDD suite: written first (fails before implementation), green after.
New files under test:
  - laura/ledger/recipe.py  (canonical_json, compute_recipe_hash, compute_short_id, mint_short_run)
"""

from __future__ import annotations

from laura.db.database import Database
from laura.ledger import (
    canonical_json,
    compute_recipe_hash,
    compute_short_id,
    mint_short_run,
)
from laura.ledger.sqlite_store import SQLiteLedgerStore

# ---------------------------------------------------------------------------
# canonical_json
# ---------------------------------------------------------------------------


def test_canonical_json_compact() -> None:
    """canonical_json must produce compact JSON without extra spaces."""
    result = canonical_json({"b": 2, "a": 1})
    assert result == '{"a":1,"b":2}'


def test_canonical_json_sorts_keys() -> None:
    """Keys must be sorted, not insertion-ordered."""
    r1 = canonical_json({"z": 99, "a": 1})
    r2 = canonical_json({"a": 1, "z": 99})
    assert r1 == r2


def test_canonical_json_nested() -> None:
    """Nested dicts also sort keys."""
    result = canonical_json({"outer": {"b": 2, "a": 1}})
    assert result == '{"outer":{"a":1,"b":2}}'


def test_canonical_json_none_value() -> None:
    """None serialises to JSON null."""
    result = canonical_json({"x": None})
    assert result == '{"x":null}'


# ---------------------------------------------------------------------------
# compute_recipe_hash
# ---------------------------------------------------------------------------


def test_recipe_hash_is_hex_string() -> None:
    h = compute_recipe_hash({"model": "clip", "threshold": 0.5})
    assert isinstance(h, str)
    assert len(h) == 64  # sha256 hexdigest
    assert all(c in "0123456789abcdef" for c in h)


def test_recipe_hash_determinism() -> None:
    recipe = {"model": "clip", "threshold": 0.5, "version": 1}
    h1 = compute_recipe_hash(recipe)
    h2 = compute_recipe_hash(recipe)
    assert h1 == h2


def test_recipe_hash_key_order_independence() -> None:
    """Same keys/values in different dict order must produce the same hash."""
    h1 = compute_recipe_hash({"a": 1, "b": 2})
    h2 = compute_recipe_hash({"b": 2, "a": 1})
    assert h1 == h2


def test_recipe_hash_changes_on_value_change() -> None:
    h1 = compute_recipe_hash({"model": "clip", "threshold": 0.5})
    h2 = compute_recipe_hash({"model": "clip", "threshold": 0.9})
    assert h1 != h2


def test_recipe_hash_changes_on_key_change() -> None:
    h1 = compute_recipe_hash({"model": "clip"})
    h2 = compute_recipe_hash({"model": "blip"})
    assert h1 != h2


# ---------------------------------------------------------------------------
# compute_short_id
# ---------------------------------------------------------------------------


def test_short_id_is_hex_string() -> None:
    recipe_hash = compute_recipe_hash({"x": 1})
    sid = compute_short_id(
        input_sha256="abc123",
        pipeline_version="2",
        recipe_hash=recipe_hash,
    )
    assert isinstance(sid, str)
    assert len(sid) == 64
    assert all(c in "0123456789abcdef" for c in sid)


def test_short_id_determinism() -> None:
    recipe_hash = compute_recipe_hash({"x": 1})
    sid1 = compute_short_id(
        input_sha256="abc123",
        pipeline_version="2",
        recipe_hash=recipe_hash,
    )
    sid2 = compute_short_id(
        input_sha256="abc123",
        pipeline_version="2",
        recipe_hash=recipe_hash,
    )
    assert sid1 == sid2


def test_short_id_input_sha256_sensitivity() -> None:
    recipe_hash = compute_recipe_hash({"x": 1})
    sid1 = compute_short_id(
        input_sha256="aaa",
        pipeline_version="2",
        recipe_hash=recipe_hash,
    )
    sid2 = compute_short_id(
        input_sha256="bbb",
        pipeline_version="2",
        recipe_hash=recipe_hash,
    )
    assert sid1 != sid2


def test_short_id_pipeline_version_sensitivity() -> None:
    recipe_hash = compute_recipe_hash({"x": 1})
    sid1 = compute_short_id(
        input_sha256="abc",
        pipeline_version="2",
        recipe_hash=recipe_hash,
    )
    sid2 = compute_short_id(
        input_sha256="abc",
        pipeline_version="3",
        recipe_hash=recipe_hash,
    )
    assert sid1 != sid2


def test_short_id_recipe_sensitivity() -> None:
    rh1 = compute_recipe_hash({"model": "a"})
    rh2 = compute_recipe_hash({"model": "b"})
    sid1 = compute_short_id(
        input_sha256="abc",
        pipeline_version="2",
        recipe_hash=rh1,
    )
    sid2 = compute_short_id(
        input_sha256="abc",
        pipeline_version="2",
        recipe_hash=rh2,
    )
    assert sid1 != sid2


def test_short_id_none_input_sha256_stable() -> None:
    """None as input_sha256 must not crash and must produce a stable id."""
    recipe_hash = compute_recipe_hash({"x": 1})
    sid1 = compute_short_id(
        input_sha256=None,
        pipeline_version="2",
        recipe_hash=recipe_hash,
    )
    sid2 = compute_short_id(
        input_sha256=None,
        pipeline_version="2",
        recipe_hash=recipe_hash,
    )
    assert sid1 == sid2


def test_short_id_none_vs_string_differ() -> None:
    """None and 'None' must not collide."""
    rh = compute_recipe_hash({"x": 1})
    sid_none = compute_short_id(input_sha256=None, pipeline_version="2", recipe_hash=rh)
    sid_str = compute_short_id(input_sha256="None", pipeline_version="2", recipe_hash=rh)
    assert sid_none != sid_str


# ---------------------------------------------------------------------------
# mint_short_run — round-trip via SQLiteLedgerStore
# ---------------------------------------------------------------------------


def test_mint_short_run_records_row(db: Database) -> None:
    store = SQLiteLedgerStore(db)
    recipe = {"model": "clip", "threshold": 0.8}
    row = mint_short_run(store, recipe=recipe, input_sha256="sha256abc")

    # returned row has expected fields
    assert row["short_id"]
    assert row["recipe_hash"]
    assert row["status"] == "queued"
    assert row["input_sha256"] == "sha256abc"


def test_mint_short_run_short_id_matches_standalone(db: Database) -> None:
    """short_id recorded must equal standalone compute_short_id."""
    store = SQLiteLedgerStore(db)
    recipe = {"model": "clip", "threshold": 0.8}
    input_sha = "deadbeef"
    pipeline_version = "2"

    row = mint_short_run(
        store,
        recipe=recipe,
        input_sha256=input_sha,
        pipeline_version=pipeline_version,
    )

    expected_recipe_hash = compute_recipe_hash(recipe)
    expected_short_id = compute_short_id(
        input_sha256=input_sha,
        pipeline_version=pipeline_version,
        recipe_hash=expected_recipe_hash,
    )

    assert row["recipe_hash"] == expected_recipe_hash
    assert row["short_id"] == expected_short_id


def test_mint_short_run_get_run_roundtrip(db: Database) -> None:
    """mint_short_run row can be retrieved via get_run."""
    store = SQLiteLedgerStore(db)
    recipe = {"step": "encode", "codec": "h264"}
    row = mint_short_run(store, recipe=recipe, input_sha256="cafebabe")

    fetched = store.get_run(row["id"])
    assert fetched is not None
    assert fetched["short_id"] == row["short_id"]
    assert fetched["recipe_hash"] == row["recipe_hash"]
    assert fetched["status"] == "queued"


def test_mint_short_run_default_status_queued(db: Database) -> None:
    store = SQLiteLedgerStore(db)
    row = mint_short_run(store, recipe={"a": 1}, input_sha256="xyz")
    assert row["status"] == "queued"


def test_mint_short_run_custom_status(db: Database) -> None:
    store = SQLiteLedgerStore(db)
    row = mint_short_run(store, recipe={"a": 1}, input_sha256="xyz", status="running")
    assert row["status"] == "running"


def test_mint_short_run_none_input_sha256(db: Database) -> None:
    store = SQLiteLedgerStore(db)
    row = mint_short_run(store, recipe={"x": 1}, input_sha256=None)
    assert row["input_sha256"] is None
    assert row["short_id"]  # stable id despite None


def test_mint_short_run_same_inputs_same_short_id(db: Database) -> None:
    """Same inputs → same short_id (idempotency anchor)."""
    store = SQLiteLedgerStore(db)
    recipe = {"model": "clip"}
    input_sha = "abc"
    pipeline_version = "2"

    r1 = mint_short_run(
        store, recipe=recipe, input_sha256=input_sha, pipeline_version=pipeline_version
    )
    r2 = mint_short_run(
        store, recipe=recipe, input_sha256=input_sha, pipeline_version=pipeline_version
    )

    # Two distinct run rows but same short_id
    assert r1["id"] != r2["id"]
    assert r1["short_id"] == r2["short_id"]


def test_mint_short_run_uses_pipeline_version_default(db: Database) -> None:
    """Default pipeline_version must be PIPELINE_VERSION = '2'."""
    from laura import PIPELINE_VERSION

    store = SQLiteLedgerStore(db)
    row = mint_short_run(store, recipe={"x": 1}, input_sha256="sha")
    assert row["pipeline_version"] == PIPELINE_VERSION
