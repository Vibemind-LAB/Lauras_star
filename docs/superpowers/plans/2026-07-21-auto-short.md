# Auto-Short Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /projects/{pid}/auto-short {topic}` — a scout agent picks the best asset + scenes across ALL project transcripts (with rationale, validated, never-dying fallback) and starts a normal v2 production session on the winner.

**Architecture:** Three layers (spec `docs/superpowers/specs/2026-07-21-auto-short-design.md`): (1) `search_material` — deterministic discovery over all project transcripts (semantic via the existing `laura/semantic.py` index when available, else the existing lexical `repos.search_transcript`), hits mapped to the asset's rough-cut scenes READ-ONLY; (2) a single-agent scout (`run_scout`) that chooses `{asset_id, scene_numbers, rationale}` with validation → one retry → deterministic fallback; (3) the endpoint that composes the session task text and reuses the exact session-creation path (extracted helper shared with `create_production`).

**Tech Stack:** Python 3.11 + uv, FastAPI, pydantic v2, pytest; AutoGen AssistantAgent + in-process FunctionTools (v1 pattern), optional `[semantic]` extra (qdrant_client + fastembed).

## Global Constraints

- Backend only; `uv` from `services/local-api/`; gates are `uv run pytest -q` (exit code, timeout 600000), **bare `uv run mypy`** ("Success"), `uv run ruff check src tests` ("All checks passed!"). Never two parallel `uv run`; ALL commands FOREGROUND, never run_in_background, never monitors.
- **Discovery must never create anything**: no `get_or_create_*` in the ranking path — a read-only rough-cut lookup; assets without a rough cut or scenes fall out of the ranking with a reason.
- The scout can NEVER kill the endpoint: invalid reply → one retry → deterministic fallback (top-ranked asset, its scene hits, rationale `"automatic fallback: top search score"`); exceptions/timeouts → same fallback. Fallback is marked (`fallback: True`).
- 422 (no material) BEFORE any session/scout work — no corpse sessions.
- Session creation is byte-identical to `create_production` (shared extracted helper); `_require_autoshort` + `_require_usable_agent_config` + `warnings: config_warnings(...)` exactly like the existing enqueue endpoints.
- Existing search/semantic/production code stays untouched except the named extraction in `api/short_creator.py`.
- mypy strict; no `print`; ruff line length 100.
- Commits: conventional commits, English, explicit `git add <paths>` (never `-A`), trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 1: `search_material` — deterministic discovery core

**Files:**
- Create: `services/local-api/src/laura/short_creator/discovery.py`
- Modify: `services/local-api/src/laura/db/repos.py` (one read-only helper)
- Test: `services/local-api/tests/test_discovery.py` (create)

**Interfaces:**
- Consumes: `laura.semantic.get_index()` (`SemanticIndex.query(text, project_id=…, limit=…) -> [{score, **payload}]`, payload keys: `project_id, asset_id, segment_id, asset_name, text, start_frame, end_frame, speaker_label`), `repos.search_transcript(db, project_id=…, query=…, limit=…)` (same row keys, no score), `repos.list_scenes`, `repos.list_timeline_clips`, `laura.short_creator.context._scene_src_ranges` (read `production_tools._resolve_scene` at ~line 577 for the exact composition — mirror it, WITHOUT the get_or_create).
- Produces:
  - `repos.get_asset_rough_cut(db, project_id: str, asset_id: str) -> dict[str, Any] | None` — the SELECT half of `get_or_create_asset_rough_cut` (~line 1620), returning the newest `rough_cut` timeline `created_from=asset_id` or `None`, never creating.
  - `discovery.search_material(db, project_id: str, topic: str, *, limit: int = 40) -> dict[str, Any]` with shape `{"source": "semantic"|"lexical", "ranking": [{"asset_id", "display_name", "score": float, "scene_hits": [{"scene_number": int, "snippet": str, "score": float}]}], "skipped": [{"asset_id", "reason": str}]}` — ranking sorted by score descending; empty ranking is a normal result.

- [ ] **Step 1: Write the failing tests**

Create `services/local-api/tests/test_discovery.py`:

```python
"""search_material: topic -> ranked assets + rough-cut scene hits across the whole project.

The discovery layer under auto-short (spec 2026-07-21-auto-short-design.md §1): semantic
when the [semantic] extra answers, lexical fallback otherwise, hits mapped onto each
asset's rough-cut scenes READ-ONLY — ranking must never create timelines as a side effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator import discovery

FPS = 30


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _seed_asset_with_scenes(
    db: Database, project_id: str, name: str, *, segments: list[tuple[int, int, str]]
) -> str:
    """Asset + succeeded analysis run with *segments* (start_frame, end_frame, text) + a
    rough-cut timeline with one 1:1 clip over [0, 600) and two scenes [0,300)/[300,600)."""
    asset = repos.create_asset(
        db, project_id=project_id, type="video", display_name=name, source_path=f"/tmp/{name}"
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )
    repos.start_analysis_run(db, run["id"])
    for start, end, text in segments:
        repos.insert_segment_with_words(
            db,
            asset_id=asset["id"],
            run_id=run["id"],
            speaker_id=None,
            segment={
                "start_sample": start * 1600,
                "end_sample": end * 1600,
                "start_frame": start,
                "end_frame": end,
                "text": text,
                "confidence": 1.0,
            },
            words=[],
        )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    timeline = repos.create_timeline(
        db, project_id=project_id, name="Rough Cut", kind="rough_cut",
        created_from=asset["id"],
    )
    repos.add_timeline_clip(
        db, timeline_id=timeline["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=600,
        seq_in_frame=0, seq_out_frame_exclusive=600,
    )
    repos.replace_scenes(db, project_id, timeline["id"], [(0, 300), (300, 600)])
    return str(asset["id"])


def test_lexical_ranking_maps_hits_to_scenes_and_ranks_assets(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    strong = _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission"),
                  (320, 380, "agents execute the mission end to end")],
    )
    weak = _seed_asset_with_scenes(
        db, project["id"], "weak.mp4",
        segments=[(10, 60, "unrelated dashboard talk"),
                  (320, 380, "one mission mention only")],
    )

    out = discovery.search_material(db, project["id"], "mission")

    assert out["source"] == "lexical"
    assert [r["asset_id"] for r in out["ranking"]] == [strong, weak]
    top = out["ranking"][0]
    # scene 1 covers src [0,300), scene 2 covers [300,600)
    assert [h["scene_number"] for h in top["scene_hits"]] == [1, 2]
    assert "mission" in top["scene_hits"][0]["snippet"]
    assert out["skipped"] == []


def test_asset_without_rough_cut_is_skipped_not_created(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    # Asset with a transcript hit but NO rough-cut timeline at all.
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="raw.mp4",
        source_path="/tmp/raw.mp4",
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.start_analysis_run(db, run["id"])
    repos.insert_segment_with_words(
        db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
        segment={"start_sample": 0, "end_sample": 16000, "start_frame": 0,
                 "end_frame": 50, "text": "mission talk", "confidence": 1.0},
        words=[],
    )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})

    out = discovery.search_material(db, project["id"], "mission")

    assert out["ranking"] == []
    assert out["skipped"] == [{"asset_id": str(asset["id"]), "reason": "no rough cut"}]
    # READ-ONLY invariant: discovery must not have created a timeline as a side effect.
    assert repos.get_asset_rough_cut(db, project["id"], str(asset["id"])) is None


def test_no_hits_is_an_empty_ranking_not_an_error(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[(10, 60, "hello world")])

    out = discovery.search_material(db, project["id"], "quantum chromodynamics")

    assert out == {"source": "lexical", "ranking": [], "skipped": []}
```

Plus a semantic-path test gated exactly like the repo's existing semantic tests (find them: grep tests/ for `semantic_available` or `importorskip` on `qdrant_client` and mirror the gating): index two seeded segments into an in-memory `SemanticIndex`, monkeypatch `discovery.get_index` to return it, assert `out["source"] == "semantic"` and the ranking is non-empty.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_discovery.py -q; echo EXIT=$?`
Expected: FAIL — `discovery` module / `get_asset_rough_cut` do not exist.

- [ ] **Step 3: Implement**

`repos.get_asset_rough_cut` — directly above `get_or_create_asset_rough_cut` (~line 1620), reusing its exact SELECT:

```python
def get_asset_rough_cut(
    db: Database, project_id: str, asset_id: str
) -> dict[str, Any] | None:
    """The newest rough_cut timeline for this asset, or None — NEVER creates (the
    discovery ranking must not leave timelines behind; get_or_create_asset_rough_cut is
    the writing sibling)."""
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM timelines WHERE project_id=? AND kind='rough_cut' "
            "AND created_from=? ORDER BY created_at DESC, id DESC LIMIT 1",
            (project_id, asset_id),
        ).fetchone()
    return dict(row) if row is not None else None
```

Refactor `get_or_create_asset_rough_cut` to call it (DRY; behavior identical).

`services/local-api/src/laura/short_creator/discovery.py`:

```python
"""Topic -> ranked material across the whole project (auto-short discovery layer).

Semantic when the [semantic] extra answers (the same index the search endpoint uses),
lexical fallback otherwise — one row shape either way. Hits are mapped onto each asset's
rough-cut scenes READ-ONLY: the ranking must never create timelines as a side effect
(spec 2026-07-21-auto-short-design.md §1)."""

from __future__ import annotations

import logging
from typing import Any

from ..db import repos
from ..db.database import Database
from ..semantic import get_index
from . import context

logger = logging.getLogger(__name__)

_SNIPPET_CHARS = 160
_MAX_SCENE_SNIPPETS = 3


def _segment_hits(
    db: Database, project_id: str, topic: str, limit: int
) -> tuple[list[dict[str, Any]], str]:
    """(hits, source): semantic when the index exists AND answers, else lexical.
    Mirrors api/search.py's fallback stance — a broken index degrades, never raises."""
    index = get_index()
    if index is not None:
        try:
            hits = index.query(topic, project_id=project_id, limit=limit)
        except Exception:  # noqa: BLE001 - semantic search is best-effort
            logger.warning("semantic query failed; falling back to lexical", exc_info=True)
            hits = []
        if hits:
            return hits, "semantic"
    return (
        repos.search_transcript(db, project_id=project_id, query=topic, limit=limit),
        "lexical",
    )


def _scene_ranges(
    db: Database, project_id: str, asset_id: str
) -> list[tuple[int, int, int]] | None:
    """[(scene_number, src_start, src_end_exclusive)] for the asset's rough cut, or None
    when there is no rough cut / no scenes. Mirrors production_tools._resolve_scene's
    composition (list_scenes order_index+1, clips, context._scene_src_ranges) but strictly
    read-only."""
    timeline = repos.get_asset_rough_cut(db, project_id, asset_id)
    if timeline is None:
        return None
    scenes = repos.list_scenes(db, str(timeline["id"]))
    if not scenes:
        return None
    clips = repos.list_timeline_clips(db, str(timeline["id"]))
    # NOTE for the implementer: call context._scene_src_ranges exactly the way
    # production_tools._resolve_scene (~line 577) does — read that function first and
    # reuse its argument construction verbatim; build the full ordered list
    # [(order_index + 1, src_start, src_end_exclusive)] from its result.
    ...


def search_material(
    db: Database, project_id: str, topic: str, *, limit: int = 40
) -> dict[str, Any]:
    hits, source = _segment_hits(db, project_id, topic, limit)
    per_asset: dict[str, dict[str, Any]] = {}
    skipped: list[dict[str, str]] = []
    ranges_cache: dict[str, list[tuple[int, int, int]] | None] = {}
    for hit in hits:
        asset_id = str(hit["asset_id"])
        if asset_id not in ranges_cache:
            ranges_cache[asset_id] = _scene_ranges(db, project_id, asset_id)
            if ranges_cache[asset_id] is None:
                skipped.append({"asset_id": asset_id, "reason": "no rough cut"})
        ranges = ranges_cache[asset_id]
        if ranges is None:
            continue
        start = int(hit.get("start_frame", 0))
        scene_number = next(
            (n for n, lo, hi in ranges if lo <= start < hi), None
        )
        if scene_number is None:
            continue
        score = float(hit.get("score") or 1.0)  # lexical rows carry no score -> 1.0/hit
        entry = per_asset.setdefault(
            asset_id,
            {
                "asset_id": asset_id,
                "display_name": str(hit.get("asset_name", "")),
                "score": 0.0,
                "scene_hits": [],
            },
        )
        entry["score"] += score
        entry["scene_hits"].append(
            {
                "scene_number": scene_number,
                "snippet": str(hit.get("text", ""))[:_SNIPPET_CHARS],
                "score": score,
            }
        )
    ranking = sorted(per_asset.values(), key=lambda e: e["score"], reverse=True)
    for entry in ranking:
        # strongest first, capped per asset; stable by scene_number for equal scores
        entry["scene_hits"] = sorted(
            entry["scene_hits"], key=lambda h: (-h["score"], h["scene_number"])
        )[:_MAX_SCENE_SNIPPETS]
        entry["scene_hits"].sort(key=lambda h: h["scene_number"])
    return {"source": source, "ranking": ranking, "skipped": skipped}
```

(The `...` in `_scene_ranges` is deliberate: `context._scene_src_ranges`'s exact call shape must be read from `production_tools._resolve_scene`, not guessed — the tests pin the resulting mapping.)

- [ ] **Step 4: GREEN + neighbors**

Run: `uv run pytest tests/test_discovery.py -q; echo EXIT=$?` → EXIT=0.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/discovery.py services/local-api/src/laura/db/repos.py services/local-api/tests/test_discovery.py
git commit -m "feat(short-creator): topic discovery ranks material across the whole project

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: the scout — agentic choice with a never-dying fallback

**Files:**
- Create: `services/local-api/src/laura/short_creator/scout.py`
- Test: `services/local-api/tests/test_scout.py` (create)

**Interfaces:**
- Consumes: Task 1's `search_material` output shape (`material["ranking"]` non-empty — the endpoint 422s before calling the scout); `AgentConfig`/client construction from `providers` (read how the v1 orchestrator/agents build an AssistantAgent with in-process FunctionTools and a resolved client — mirror that pattern for ONE agent); `discovery.search_material`, `repos` for `list_project_assets`, `production_tools`' scene-context building via existing pieces (`get_scene_context`-equivalent — read `production_tools.get_scene_context` and reuse its underlying function, not the ToolSpec).
- Produces:
  - `ScoutDecision` (TypedDict): `{"asset_id": str, "scene_numbers": list[int], "rationale": str, "fallback": bool}`.
  - `run_scout(db, config, *, project_id: str, topic: str, material: dict[str, Any], runner: Callable[[str], str] | None = None) -> ScoutDecision` — `runner` takes the composed task text and returns the agent's final reply text; `None` builds the real single-agent (AssistantAgent + FunctionTools `search_material`/`list_project_assets`/`get_scene_context`, `max_tool_iterations` small, wall-clock timeout `_SCOUT_TIMEOUT_S = 60.0`).

**Contract (the tests pin exactly this):**
- Task text embeds the topic AND the ranking (asset ids, names, scores, top snippets) so tools are optional depth, not required; asks for a JSON object `{"asset_id": str, "scene_numbers": [int], "rationale": str}` as the final answer.
- Reply parsing: last `{...}` JSON block in the reply; `asset_id` must be in `material["ranking"]`, `scene_numbers` non-empty and a subset of that asset's KNOWN scene numbers (validate against the ranking's scene universe PLUS `discovery._scene_ranges` for full coverage — an agent may legitimately pick a scene the snippets didn't show).
- Invalid (parse failure, unknown asset, bad scenes) → ONE retry with the validation error appended to the task; still invalid → fallback. `runner` raising or timing out → fallback (no retry storm).
- Fallback = `{"asset_id": ranking[0]["asset_id"], "scene_numbers": [h["scene_number"] for h in ranking[0]["scene_hits"]], "rationale": "automatic fallback: top search score", "fallback": True}`; adopted replies get `"fallback": False`.
- `material["ranking"]` empty → `ValueError` (programming error; the endpoint guards).

- [ ] **Step 1: failing tests** — `tests/test_scout.py` with injected `runner` fakes (no real LLM): (a) valid JSON reply → adopted verbatim, `fallback is False`; (b) unknown asset_id first, valid on retry → adopted, and the SECOND task text passed to the runner contains the validation error; (c) invalid twice → fallback shape exactly as above; (d) runner raises → fallback; (e) empty ranking → `pytest.raises(ValueError)`. Seed a real DB (reuse Task 1's `_seed_asset_with_scenes` pattern — import or duplicate per the repo's self-contained-test convention, read a neighboring test file's docstring for which) so scene validation runs against real scene ranges.
- [ ] **Step 2: RED** — module missing.
- [ ] **Step 3: implement** per the contract; the real default runner mirrors the v1 single-agent construction (read `short_creator/agents.py` + `orchestrator.py` for client + FunctionTool + run pattern; asyncio.run with `asyncio.wait_for(..., _SCOUT_TIMEOUT_S)`).
- [ ] **Step 4: GREEN**: `uv run pytest tests/test_scout.py tests/test_discovery.py -q; echo EXIT=$?` → EXIT=0.
- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/scout.py services/local-api/tests/test_scout.py
git commit -m "feat(short-creator): a scout agent picks the material and says why

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: the endpoint — topic in, session out

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py` (extract `_create_production_session` from `create_production`; new request model + endpoint)
- Test: `services/local-api/tests/test_auto_short_endpoint.py` (create)

**Interfaces:**
- Consumes: `discovery.search_material`, `scout.run_scout`/`ScoutDecision` (import both INTO `api/short_creator.py` at module level so tests can monkeypatch `laura.api.short_creator.run_scout`), `resolve_from_env`/`config_warnings`, `_require_autoshort`/`_require_usable_agent_config`, `repos.get_project/get_asset`.
- Produces:
  - `_create_production_session(db, asset_id: str, *, task: str, target_seconds: float, format: str, language: str) -> tuple[str, str]` — the session-row + enqueue + `set_production_session_job` block extracted VERBATIM from `create_production` (~line 286; read `ProductionCreateRequest` for the exact field names/defaults and keep them); `create_production` calls it (its response byte-identical).
  - `POST /projects/{project_id}/auto-short`, body `AutoShortRequest {topic: str (min_length=1), target_seconds/format/language with ProductionCreateRequest's exact defaults}`:
    1. 404 unknown project; `_require_autoshort()`; `_require_usable_agent_config()`.
    2. `material = search_material(db, project_id, body.topic)`; empty ranking → **422** with `detail={"reason": "no material found for topic", "skipped": material["skipped"], "source": material["source"]}`.
    3. `decision = run_scout(db, resolve_from_env(), project_id=project_id, topic=body.topic, material=material)`.
    4. Task text composed EXACTLY as: `f"{body.topic}\n\nMaterial scout: use asset '{display_name}'. Focus on scenes {', '.join(map(str, decision['scene_numbers']))} — transcript hits: {'; '.join(top snippets of the chosen asset)}. Scout rationale: {decision['rationale']}"`.
    5. `_create_production_session(...)` on `decision["asset_id"]`.
    6. 202: `{"session_id", "job_id", "asset_id", "scene_numbers", "rationale", "fallback", "ranking": material["ranking"], "warnings": config_warnings(resolve_from_env())}`.

- [ ] **Step 1: failing tests** — `tests/test_auto_short_endpoint.py` (TestClient pattern like test_production_liveness; `_autoshort_available` monkeypatched True; seed via Task 1's seeding pattern): (a) happy path with monkeypatched `run_scout` returning a fixed decision → 202, session row exists on that asset, the enqueued job payload's `task` contains topic + "Focus on scenes" + the rationale, response carries scene_numbers/rationale/fallback/ranking/warnings; (b) `run_scout` monkeypatched to return a `fallback: True` decision → response `fallback is True`; (c) no material (project with no transcript hits) → 422 with `detail["reason"] == "no material found for topic"` AND no production session row was created; (d) unknown project → 404; (e) `create_production`'s existing tests stay green (the extraction is invisible — run test_production_liveness.py); (f) preflight 503: env with `LAURA_AGENT_PROVIDER=openai-compat` but NO `LAURA_AGENT_API_KEY` (monkeypatch setenv/delenv, mirroring the existing preflight endpoint tests) → 503, and neither scout nor session ran.
- [ ] **Step 2: RED** — 404/405 route missing.
- [ ] **Step 3: implement** per Interfaces.
- [ ] **Step 4: GREEN + full gates** — `uv run pytest tests/test_auto_short_endpoint.py tests/test_production_liveness.py tests/test_scout.py tests/test_discovery.py -q` → EXIT=0; then `uv run pytest -q` (timeout 600000) → bare `uv run mypy` → `uv run ruff check src tests`.
- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/api/short_creator.py services/local-api/tests/test_auto_short_endpoint.py
git commit -m "feat(short-creator): auto-short endpoint - topic in, scouted session out

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
