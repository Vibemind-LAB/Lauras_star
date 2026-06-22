"""Content-addressing helpers for short-build recipes.

Provides:
- ``canonical_json`` — stable, compact JSON serialisation (key-sorted).
- ``compute_recipe_hash`` — sha256 of canonical_json(recipe).
- ``compute_short_id`` — sha256 of canonical_json({input_sha256, pipeline_version, recipe_hash}).
- ``mint_short_run`` — compute IDs then persist via LedgerStore.

The *recipe* is intentionally opaque: any JSON-serialisable mapping the caller
provides.  This module imposes no schema on it; later phases (P7 batch /
recipe-from-trace) formalise the contents.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from laura import PIPELINE_VERSION

from .store import LedgerStore

#: Keys present in export-options that carry runtime observations, NOT user build params.
#: These must be excluded from the recipe hash to preserve idempotency (Invariant 7).
RECIPE_EXCLUDED_KEYS: frozenset[str] = frozenset(
    {"short_run_id", "quality_status", "quality_verified"}
)


def canonical_json(obj: Any) -> str:
    """Return a compact, key-sorted JSON string for *obj*.

    Guaranteed to be deterministic regardless of dict insertion order.
    Uses ``ensure_ascii=True`` so output is a pure-ASCII byte string when
    encoded — consistent across Python builds.
    """
    return json.dumps(obj, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def compute_recipe_hash(recipe: Mapping[str, Any]) -> str:
    """Return the sha256 hexdigest of the canonical JSON of *recipe*.

    Key-order-independent: ``{"a":1,"b":2}`` and ``{"b":2,"a":1}`` hash
    identically.
    """
    raw = canonical_json(recipe)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def compute_short_id(
    *,
    input_sha256: str | None,
    pipeline_version: str,
    recipe_hash: str,
) -> str:
    """Return the sha256 hexdigest that uniquely identifies a build attempt.

    The id is derived from the canonical JSON of the triple
    ``{input_sha256, pipeline_version, recipe_hash}``.  Using a dict (rather
    than string-join) avoids delimiter-ambiguity attacks.

    *input_sha256* may be ``None`` (un-hashed asset); ``None`` serialises to
    JSON ``null`` and produces a stable, distinct id from the string ``"None"``.
    """
    payload = {
        "input_sha256": input_sha256,
        "pipeline_version": pipeline_version,
        "recipe_hash": recipe_hash,
    }
    raw = canonical_json(payload)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def mint_short_run(
    store: LedgerStore,
    *,
    recipe: Mapping[str, Any],
    input_sha256: str | None,
    pipeline_version: str = PIPELINE_VERSION,
    status: str = "queued",
) -> dict[str, Any]:
    """Compute IDs and record a new short-build run in *store*.

    Returns the recorded run dict (as returned by ``store.record_run``).

    Parameters
    ----------
    store:
        Active ``LedgerStore`` backend.
    recipe:
        Any JSON-serialisable mapping of build parameters (opaque here).
    input_sha256:
        sha256 of the source media asset (``media_assets.sha256``); may be
        ``None`` for assets that have not been hashed yet.
    pipeline_version:
        Defaults to ``PIPELINE_VERSION`` from ``laura``.  Bump this to
        invalidate cached results.
    status:
        Initial run status; must be one of the four allowed values
        (``"queued"``, ``"running"``, ``"succeeded"``, ``"failed"``).
    """
    recipe_hash = compute_recipe_hash(recipe)
    short_id = compute_short_id(
        input_sha256=input_sha256,
        pipeline_version=pipeline_version,
        recipe_hash=recipe_hash,
    )
    return store.record_run(
        short_id=short_id,
        pipeline_version=pipeline_version,
        input_sha256=input_sha256,
        recipe_hash=recipe_hash,
        status=status,
    )
