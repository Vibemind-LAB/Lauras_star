# Auto-Overview Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /projects/{pid}/auto-overview` turns a topic into a watchable overview cut
mixed from several videos — a new sequence plus a rendered MP4.

**Architecture:** The Phase-1 discovery layer is reused unchanged except for carrying segment
frames. A pure function turns transcript hits into short candidate windows; a single agent
picks and orders them **by index** (it never invents frames); a builder writes one
`kind="overview"` source timeline, one scene per window, and a NEW sequence; the existing
`export.render` job renders it. No migration, no new job kind, no board involvement.

**Tech Stack:** Python 3.11+ (uv), FastAPI, SQLite via `laura.db.repos`, AutoGen 0.4
(optional `autoshort` extra), pytest.

**Spec:** [`docs/superpowers/specs/2026-07-31-auto-overview-design.md`](../specs/2026-07-31-auto-overview-design.md)

## Global Constraints

- **Timeline state is integer frames, end-exclusive** (`CLAUDE.md` invariants 1 + 2). Seconds
  exist only as module constants converted through an asset's `rate_num`/`rate_den`.
- **`transcript_segments.end_frame` is ALREADY end-exclusive** (`mapping.map_segment` snaps it
  with `snap_out_to_frame`, CEIL, docstring "Out-point (exclusive)"). Carry it through
  unchanged — a `+1`/`-1` anywhere on this path is an off-by-one bug.
- **Nothing is written before the 422.** All resolution (scene → source bounds → windows)
  happens first; a topic with no usable material leaves zero rows behind.
- **The project sequence is never touched.** `repos.get_or_create_project_sequence` returns the
  OLDEST `kind='sequence'` row; the overview always creates a new one.
- **The overview's source timeline uses `kind="overview"`** — never `"rough_cut"`, which
  `repos.get_asset_rough_cut` (`kind='rough_cut' AND created_from=<asset_id>`) would pick up.
- **Phase 1 stays untouched:** `scout.py`, `api/short_creator.py`'s auto-short route, the
  production board and its provenance chain are not modified. `discovery.search_material` is
  extended additively only.
- **No `print`** — module logger. **mypy strict** and **ruff** must pass.
- Python identifiers, comments and commit messages in English; docs prose in German.
- Conventional commits, explicit `git add <paths>` (never `-A`), trailer
  `Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>`.

**Gates (run from `services/local-api`):**
```bash
uv run pytest -q && uv run mypy && uv run ruff check src tests
```
`uv run mypy` bare (no `src` argument) — CI type-checks the tests too. Never run two
`uv run` commands in the same project in parallel.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/laura/short_creator/discovery.py` (modify) | carry each hit's segment frames into `scene_hits` |
| `src/laura/short_creator/overview_windows.py` (create) | pure: hits → candidate windows; trim to target |
| `src/laura/short_creator/overview_scout.py` (create) | one agent picks + orders candidates by index; validate → retry → fallback |
| `src/laura/short_creator/overview_build.py` (create) | DB writes: source timeline + scenes + new sequence |
| `src/laura/api/short_creator.py` (modify) | the endpoint: preflight → discovery → candidates → scout → build → render |
| `tests/test_discovery.py` (modify) | frames are carried |
| `tests/test_overview_windows.py` (create) | window rules |
| `tests/test_overview_scout.py` (create) | selection, retry, fallback, two-source rule |
| `tests/test_overview_build.py` (create) | the three levels are wired correctly and nothing else moves |
| `tests/test_auto_overview_endpoint.py` (create) | status codes, no-writes-before-422, render enqueued |

---

### Task 1: Discovery carries segment frames

**Files:**
- Modify: `services/local-api/src/laura/short_creator/discovery.py:111-117`
- Test: `services/local-api/tests/test_discovery.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: each entry in `search_material(...)["ranking"][i]["scene_hits"]` additionally
  carries `"start_frame": int` and `"end_frame_exclusive": int` (source-frame space).

- [ ] **Step 1: Write the failing test**

Append to `services/local-api/tests/test_discovery.py`:

```python
def test_scene_hits_carry_segment_frames_for_the_overview(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Auto-overview builds its windows from these frames (spec 2026-07-31 §2).

    ``end_frame_exclusive`` is the segment's ``end_frame`` verbatim: that column is ALREADY
    an exclusive out-point (mapping.map_segment snaps it with snap_out_to_frame/CEIL), so any
    +/-1 here would be an off-by-one.
    """
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    _seed_asset_with_scenes(
        db, project["id"], "a.mp4", segments=[(10, 60, "the agent farm plans the mission")]
    )

    out = discovery.search_material(db, project["id"], "mission")

    hit = out["ranking"][0]["scene_hits"][0]
    assert hit["start_frame"] == 10
    assert hit["end_frame_exclusive"] == 60
    # The Phase-1 keys stay exactly as they were.
    assert hit["scene_number"] == 1
    assert "mission" in hit["snippet"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
uv run pytest tests/test_discovery.py::test_scene_hits_carry_segment_frames_for_the_overview -q
```
Expected: FAIL with `KeyError: 'start_frame'`.

- [ ] **Step 3: Carry the frames through**

In `search_material`, replace the `entry["scene_hits"].append({...})` block with:

```python
        entry["scene_hits"].append(
            {
                "scene_number": scene_number,
                "snippet": str(hit.get("text", ""))[:_SNIPPET_CHARS],
                "score": score,
                # Auto-overview (spec 2026-07-31 §2) builds its candidate windows from these;
                # Phase 1 never reads them. `end_frame` is ALREADY end-exclusive
                # (mapping.map_segment -> snap_out_to_frame, CEIL) — carried verbatim.
                "start_frame": start,
                "end_frame_exclusive": int(hit.get("end_frame", start)),
            }
        )
```

`start` is the already-computed `int(hit.get("start_frame", 0))` a few lines above. Both the
semantic path (Qdrant payload) and the lexical path (`repos.search_transcript` selects
`s.start_frame`, `s.end_frame`) provide the same two keys.

- [ ] **Step 4: Run the discovery suite**

```bash
uv run pytest tests/test_discovery.py -q
```
Expected: PASS — including every pre-existing test (the change is additive).

- [ ] **Step 5: Commit**

```bash
git add src/laura/short_creator/discovery.py tests/test_discovery.py
git commit -m "feat(short-creator): discovery hits carry their segment frames"
```

---

### Task 2: Candidate windows (pure)

**Files:**
- Create: `services/local-api/src/laura/short_creator/overview_windows.py`
- Test: `services/local-api/tests/test_overview_windows.py`

**Interfaces:**
- Consumes: Task 1's `scene_hits` shape.
- Produces:
  - `Candidate` (frozen dataclass): `asset_id: str`, `display_name: str`, `scene_number: int`,
    `start_frame: int`, `end_frame_exclusive: int`, `snippet: str`
  - `build_candidates(ranking, *, scene_bounds, fps_by_asset) -> list[Candidate]`
    where `scene_bounds: dict[tuple[str, int], tuple[int, int]]` maps
    `(asset_id, scene_number) -> (src_start, src_end_exclusive)` and
    `fps_by_asset: dict[str, tuple[int, int]]` maps `asset_id -> (rate_num, rate_den)`
  - `trim_to_target(candidates, *, target_seconds, fps_by_asset) -> list[Candidate]`
  - `duration_seconds(candidates, *, fps_by_asset) -> float`

- [ ] **Step 1: Write the failing tests**

Create `services/local-api/tests/test_overview_windows.py`:

```python
"""Candidate windows: transcript hits -> short, clamped, merged clips (spec
2026-07-31-auto-overview-design.md §3). Pure functions — no DB, no agent."""

from __future__ import annotations

from laura.short_creator.overview_windows import (
    Candidate,
    build_candidates,
    duration_seconds,
    trim_to_target,
)

FPS = {"a": (30, 1), "b": (30, 1)}
# One scene per asset, generous bounds unless a test overrides them.
BOUNDS = {("a", 1): (0, 100_000), ("b", 1): (0, 100_000)}


def _ranking(asset_id: str, name: str, hits: list[tuple[int, int]]) -> dict:
    return {
        "asset_id": asset_id,
        "display_name": name,
        "score": 1.0,
        "scene_hits": [
            {
                "scene_number": 1,
                "snippet": f"hit {start}",
                "score": 1.0,
                "start_frame": start,
                "end_frame_exclusive": end,
            }
            for start, end in hits
        ],
    }


def test_pads_by_one_second_on_both_sides() -> None:
    out = build_candidates(
        [_ranking("a", "A", [(300, 450)])], scene_bounds=BOUNDS, fps_by_asset=FPS
    )
    assert len(out) == 1
    # 30fps -> 30 frames of padding each side.
    assert out[0].start_frame == 270
    assert out[0].end_frame_exclusive == 480


def test_clamps_to_the_scene_bounds() -> None:
    """A window never leaves the scene its hit was mapped into."""
    out = build_candidates(
        [_ranking("a", "A", [(10, 200)])],
        scene_bounds={("a", 1): (5, 205)},
        fps_by_asset=FPS,
    )
    assert out[0].start_frame == 5
    assert out[0].end_frame_exclusive == 205


def test_merges_overlapping_and_near_neighbours_of_the_same_asset() -> None:
    # Two hits 30 frames (1.0s) apart after padding -> below the 1.5s merge gap.
    out = build_candidates(
        [_ranking("a", "A", [(300, 400), (460, 560)])],
        scene_bounds=BOUNDS,
        fps_by_asset=FPS,
    )
    assert len(out) == 1
    assert out[0].start_frame == 270
    assert out[0].end_frame_exclusive == 590


def test_does_not_merge_across_assets() -> None:
    out = build_candidates(
        [_ranking("a", "A", [(300, 400)]), _ranking("b", "B", [(300, 400)])],
        scene_bounds=BOUNDS,
        fps_by_asset=FPS,
    )
    assert len(out) == 2
    assert {c.asset_id for c in out} == {"a", "b"}


def test_drops_windows_below_the_minimum() -> None:
    """4.0s floor: a 1.0s hit padded to 3.0s is still too short to watch."""
    out = build_candidates(
        [_ranking("a", "A", [(300, 330)])], scene_bounds=BOUNDS, fps_by_asset=FPS
    )
    assert out == []


def test_caps_windows_at_the_maximum() -> None:
    """20.0s ceiling, cut at the END so the window keeps its start."""
    out = build_candidates(
        [_ranking("a", "A", [(1000, 3000)])], scene_bounds=BOUNDS, fps_by_asset=FPS
    )
    assert out[0].start_frame == 970
    assert out[0].end_frame_exclusive == 970 + 600  # 20s * 30fps


def test_non_integer_frame_rate_rounds_the_padding() -> None:
    """29.97 (30000/1001): 1.0s of padding is 30 frames, not 29 or 31."""
    out = build_candidates(
        [_ranking("a", "A", [(300, 450)])],
        scene_bounds=BOUNDS,
        fps_by_asset={"a": (30000, 1001)},
    )
    assert out[0].start_frame == 270
    assert out[0].end_frame_exclusive == 480


def test_unknown_scene_bounds_drop_the_hit_instead_of_raising() -> None:
    out = build_candidates(
        [_ranking("a", "A", [(300, 450)])], scene_bounds={}, fps_by_asset=FPS
    )
    assert out == []


def test_trim_to_target_drops_from_the_end() -> None:
    made = [
        Candidate("a", "A", 1, 0, 300, "one"),    # 10s
        Candidate("b", "B", 1, 0, 300, "two"),    # 10s
        Candidate("a", "A", 1, 600, 900, "three"),  # 10s
    ]
    kept = trim_to_target(made, target_seconds=20, fps_by_asset=FPS)
    # 20s target + 20% tolerance = 24s -> two clips fit, the third does not.
    assert [c.snippet for c in kept] == ["one", "two"]


def test_duration_seconds_sums_across_assets() -> None:
    made = [Candidate("a", "A", 1, 0, 300, "one"), Candidate("b", "B", 1, 0, 150, "two")]
    assert duration_seconds(made, fps_by_asset=FPS) == 15.0
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_overview_windows.py -q
```
Expected: FAIL with `ModuleNotFoundError: No module named 'laura.short_creator.overview_windows'`.

- [ ] **Step 3: Implement the module**

Create `services/local-api/src/laura/short_creator/overview_windows.py`:

```python
"""Transcript hits -> short candidate windows for the auto-overview (spec
2026-07-31-auto-overview-design.md §3).

Pure: no DB, no agent, no clock. Scene bounds and frame rates are passed in, so every rule
here is testable in isolation — and the auto-overview endpoint can resolve everything BEFORE
it writes a single row.

All arithmetic is in integer frames, end-exclusive (CLAUDE.md invariants 1 + 2). The seconds
below are constants converted through each asset's own rate; they never become state.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Padding keeps a window from cutting the first/last word of the matched segment.
_PAD_S = 1.0
# Two windows closer than this read as one thought — join them instead of hard-cutting twice.
_MERGE_GAP_S = 1.5
# Below this a clip is a blip, not a statement.
_MIN_S = 4.0
# Above this one source starts dominating the overview.
_MAX_S = 20.0
# How much longer than target_seconds the final cut may run.
_TARGET_TOLERANCE = 1.2


@dataclass(frozen=True)
class Candidate:
    """One watchable window: a source-frame range of ONE asset, inside ONE of its scenes."""

    asset_id: str
    display_name: str
    scene_number: int
    start_frame: int
    end_frame_exclusive: int
    snippet: str

    @property
    def length_frames(self) -> int:
        return self.end_frame_exclusive - self.start_frame


def _frames(seconds: float, fps_num: int, fps_den: int) -> int:
    """Seconds -> frames at ``fps_num/fps_den``, rounded to the nearest frame.

    ROUND (not floor) so 29.97 gives the same 30-frame second a viewer expects.
    """
    return int(round(seconds * fps_num / fps_den))


def _fps(fps_by_asset: dict[str, tuple[int, int]], asset_id: str) -> tuple[int, int]:
    return fps_by_asset.get(asset_id, (25, 1))


def build_candidates(
    ranking: list[dict[str, Any]],
    *,
    scene_bounds: dict[tuple[str, int], tuple[int, int]],
    fps_by_asset: dict[str, tuple[int, int]],
) -> list[Candidate]:
    """``search_material``'s ranking -> ordered candidate windows.

    Order is the deterministic one the fallback relies on: assets in ranking order (by search
    score), chronological within an asset.

    A hit whose ``(asset_id, scene_number)`` has no entry in *scene_bounds* is dropped, not
    raised on — the ranking and the bounds are read separately, and a scene can disappear
    between the two reads.
    """
    out: list[Candidate] = []
    for entry in ranking:
        asset_id = str(entry["asset_id"])
        display_name = str(entry.get("display_name") or "")
        fps_num, fps_den = _fps(fps_by_asset, asset_id)
        pad = _frames(_PAD_S, fps_num, fps_den)
        merge_gap = _frames(_MERGE_GAP_S, fps_num, fps_den)
        min_len = _frames(_MIN_S, fps_num, fps_den)
        max_len = _frames(_MAX_S, fps_num, fps_den)

        padded: list[tuple[int, int, int, str]] = []  # (scene, start, end_excl, snippet)
        for hit in entry["scene_hits"]:
            scene_number = int(hit["scene_number"])
            bounds = scene_bounds.get((asset_id, scene_number))
            if bounds is None:
                continue
            lo, hi = bounds
            start = max(lo, int(hit["start_frame"]) - pad)
            end = min(hi, int(hit["end_frame_exclusive"]) + pad)
            if end <= start:
                continue
            padded.append((scene_number, start, end, str(hit.get("snippet") or "")))

        # Merge within the same scene only: a merged window must stay inside one scene's
        # bounds, which is exactly what the clamp above guarantees per scene.
        padded.sort(key=lambda p: (p[0], p[1]))
        merged: list[list[Any]] = []
        for scene_number, start, end, snippet in padded:
            if merged and merged[-1][0] == scene_number and start - merged[-1][2] < merge_gap:
                merged[-1][2] = max(merged[-1][2], end)
                continue
            merged.append([scene_number, start, end, snippet])

        for scene_number, start, end, snippet in merged:
            length = end - start
            if length < min_len:
                continue
            if length > max_len:
                end = start + max_len  # cut at the end; the start carries the cue
            out.append(
                Candidate(
                    asset_id=asset_id,
                    display_name=display_name,
                    scene_number=scene_number,
                    start_frame=start,
                    end_frame_exclusive=end,
                    snippet=snippet,
                )
            )
    return out


def duration_seconds(
    candidates: list[Candidate], *, fps_by_asset: dict[str, tuple[int, int]]
) -> float:
    """Total seconds of *candidates*, each converted at its own asset's rate."""
    total = 0.0
    for candidate in candidates:
        fps_num, fps_den = _fps(fps_by_asset, candidate.asset_id)
        total += candidate.length_frames * fps_den / fps_num
    return total


def trim_to_target(
    candidates: list[Candidate],
    *,
    target_seconds: int,
    fps_by_asset: dict[str, tuple[int, int]],
) -> list[Candidate]:
    """Keep candidates from the front until ``target_seconds * 1.2`` is exceeded.

    Cutting from the END preserves the chosen opening — the scout put its strongest clip
    first, and an overview that loses its own opening is worse than one that runs short.
    """
    budget = target_seconds * _TARGET_TOLERANCE
    kept: list[Candidate] = []
    for candidate in candidates:
        if duration_seconds([*kept, candidate], fps_by_asset=fps_by_asset) > budget:
            break
        kept.append(candidate)
    return kept
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_overview_windows.py -q
```
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/laura/short_creator/overview_windows.py tests/test_overview_windows.py
git commit -m "feat(short-creator): hits become short, clamped candidate windows"
```

---

### Task 3: The overview scout

**Files:**
- Create: `services/local-api/src/laura/short_creator/overview_scout.py`
- Test: `services/local-api/tests/test_overview_scout.py`

**Interfaces:**
- Consumes: Task 2's `Candidate`, `trim_to_target`; `providers.AgentConfig`,
  `providers.build_model_client` (existing).
- Produces:
  - `OverviewDecision` (TypedDict): `clips: list[Candidate]`, `rationale: str`,
    `fallback: bool`
  - `run_overview_scout(config, *, topic, candidates, target_seconds, fps_by_asset, runner=None) -> OverviewDecision`

- [ ] **Step 1: Write the failing tests**

Create `services/local-api/tests/test_overview_scout.py`:

```python
"""run_overview_scout: one agent PICKS AND ORDERS pre-built candidate windows by index and
never invents frames (spec 2026-07-31-auto-overview-design.md §4).

Injected ``runner`` fakes stand in for the real LLM — no autogen, no network, no DB.
"""

from __future__ import annotations

import json

from laura.short_creator.overview_scout import run_overview_scout
from laura.short_creator.overview_windows import Candidate
from laura.short_creator.providers import resolve_from_env

FPS = {"a": (30, 1), "b": (30, 1)}


def _candidates() -> list[Candidate]:
    return [
        Candidate("a", "A", 1, 0, 300, "alpha"),
        Candidate("b", "B", 2, 0, 300, "beta"),
        Candidate("a", "A", 3, 600, 900, "gamma"),
    ]


def _reply(clips: list[int], rationale: str = "covers both angles") -> str:
    return "here you go\n" + json.dumps({"clips": clips, "rationale": rationale})


def _config():
    """The real env-resolved config: the scout only passes it on to the runner, which every
    test replaces — so no provider is ever contacted."""
    return resolve_from_env()


def test_adopts_a_valid_selection_in_the_agents_order() -> None:
    out = run_overview_scout(
        _config(),
        topic="mission",
        candidates=_candidates(),
        target_seconds=180,
        fps_by_asset=FPS,
        runner=lambda _task: _reply([2, 0]),
    )
    assert [c.snippet for c in out["clips"]] == ["gamma", "alpha"]
    assert out["fallback"] is False
    assert out["rationale"] == "covers both angles"


def test_out_of_range_index_is_retried_once_then_adopted() -> None:
    calls: list[str] = []

    def runner(task: str) -> str:
        calls.append(task)
        return _reply([99]) if len(calls) == 1 else _reply([1, 0])

    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=runner,
    )
    assert len(calls) == 2
    assert "not a candidate index" in calls[1]
    assert [c.snippet for c in out["clips"]] == ["beta", "alpha"]
    assert out["fallback"] is False


def test_duplicate_indices_are_rejected() -> None:
    replies = [_reply([1, 1]), _reply([1, 0])]

    def runner(_task: str) -> str:
        return replies.pop(0)

    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=runner,
    )
    assert [c.snippet for c in out["clips"]] == ["beta", "alpha"]


def test_single_source_selection_is_rejected_when_two_are_available() -> None:
    replies = [_reply([0, 2]), _reply([0, 1])]

    def runner(_task: str) -> str:
        return replies.pop(0)

    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=runner,
    )
    assert {c.asset_id for c in out["clips"]} == {"a", "b"}


def test_single_source_selection_is_fine_when_only_one_asset_has_material() -> None:
    only_a = [Candidate("a", "A", 1, 0, 300, "alpha"), Candidate("a", "A", 3, 600, 900, "gamma")]
    out = run_overview_scout(
        _config(), topic="mission", candidates=only_a, target_seconds=180,
        fps_by_asset=FPS, runner=lambda _task: _reply([0, 1]),
    )
    assert out["fallback"] is False
    assert len(out["clips"]) == 2


def test_a_raising_runner_falls_back_without_an_exception() -> None:
    def runner(_task: str) -> str:
        raise RuntimeError("model exploded")

    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=runner,
    )
    assert out["fallback"] is True
    assert out["rationale"] == "automatic fallback: top search scores"
    assert [c.snippet for c in out["clips"]] == ["alpha", "beta", "gamma"]


def test_garbage_replies_twice_fall_back() -> None:
    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=lambda _task: "I would rather not",
    )
    assert out["fallback"] is True


def test_selection_is_trimmed_to_the_target() -> None:
    """3 x 10s against a 10s target (+20% tolerance) keeps only the first clip."""
    out = run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=10,
        fps_by_asset=FPS, runner=lambda _task: _reply([1, 0, 2]),
    )
    assert [c.snippet for c in out["clips"]] == ["beta"]


def test_the_task_text_lists_numbered_candidates() -> None:
    seen: list[str] = []

    def runner(task: str) -> str:
        seen.append(task)
        return _reply([0, 1])

    run_overview_scout(
        _config(), topic="mission", candidates=_candidates(), target_seconds=180,
        fps_by_asset=FPS, runner=runner,
    )
    assert "[0]" in seen[0] and "[2]" in seen[0]
    assert "alpha" in seen[0] and "gamma" in seen[0]
    assert "mission" in seen[0]
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_overview_scout.py -q
```
Expected: FAIL with `ModuleNotFoundError: No module named 'laura.short_creator.overview_scout'`.

- [ ] **Step 3: Implement the module**

Create `services/local-api/src/laura/short_creator/overview_scout.py`:

```python
"""The overview scout: one agent picks and orders pre-built candidate windows — or a
deterministic fallback does (spec 2026-07-31-auto-overview-design.md §4).

The agent answers with INDICES into the candidate list, never with frame numbers. The
candidates were built deterministically (:mod:`.overview_windows`), so a selection can be
wrong about taste but never about time. That split is the NL-short-creator's own lesson:
with small models, every contract belongs in code, not in a conditional prompt rule.

Hardened like :mod:`.scout`, whose shape survived live use: validate -> exactly ONE retry
with the concrete error -> deterministic fallback. A runner exception, a timeout and an
infrastructure failure during validation all land in the same place — the endpoint always
gets an answer.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypedDict

from .overview_windows import Candidate, trim_to_target
from .providers import AgentConfig, build_model_client

if TYPE_CHECKING:  # annotation only — never imported at runtime
    from autogen_agentchat.agents import AssistantAgent

logger = logging.getLogger(__name__)

_SCOUT_TIMEOUT_S = 60.0


class OverviewDecision(TypedDict):
    """The scout's answer, adopted or fallback — what the auto-overview endpoint consumes."""

    clips: list[Candidate]
    rationale: str
    fallback: bool


# --- task text (pure) --------------------------------------------------------------------------


def _task_text(
    topic: str,
    candidates: list[Candidate],
    target_seconds: int,
    fps_by_asset: dict[str, tuple[int, int]],
) -> str:
    sources = sorted({c.display_name for c in candidates})
    lines = [
        f'Topic: "{topic}"',
        f"Target length: about {target_seconds} seconds.",
        "",
        f"Candidate clips from {len(sources)} video(s), already cut to watchable length:",
    ]
    for index, candidate in enumerate(candidates):
        # Each clip's own rate — a hardcoded 30 would misreport every 25fps or 29.97 source.
        fps_num, fps_den = fps_by_asset.get(candidate.asset_id, (25, 1))
        seconds = candidate.length_frames * fps_den / fps_num
        lines.append(
            f"  [{index}] {candidate.display_name!r} scene {candidate.scene_number} "
            f"(~{seconds:.0f}s): \"{candidate.snippet}\""
        )
    lines += [
        "",
        "Choose the clips that together give the best OVERVIEW of the topic, and put them in "
        "the order they should be watched. Cover at least two different videos when the list "
        "offers more than one — an overview drawn from a single source is just a long clip.",
        "Answer with EXACTLY one JSON object as your final message, nothing before or after "
        "it. Use the clip NUMBERS shown in brackets:",
        '{"clips": [<index>, ...], "rationale": "<1-3 sentences on why these clips and this '
        'order>"}',
    ]
    return "\n".join(lines)


def _retry_task_text(task: str, error: str) -> str:
    return (
        f"{task}\n\n"
        f"Your previous reply was invalid: {error}. Reply again with ONE corrected JSON "
        "object as specified above — every entry of \"clips\" must be one of the candidate "
        "numbers in brackets, each used at most once."
    )


# --- reply parsing + validation (pure) ----------------------------------------------------------


def _last_json_object(text: str) -> dict[str, Any] | None:
    """The LAST complete top-level ``{...}`` object in *text*, or ``None`` (mirrors
    :func:`.scout._last_json_object`: an agent's answer is the last JSON block in its reply)."""
    decoder = json.JSONDecoder()
    candidate: dict[str, Any] | None = None
    for start, ch in enumerate(text):
        if ch != "{":
            continue
        try:
            obj, _end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            candidate = obj
    return candidate


def _validate_reply(
    candidates: list[Candidate], reply: str
) -> tuple[OverviewDecision | None, str | None]:
    """Parse + validate *reply* against the candidate list.

    Returns ``(decision, None)`` when good, else ``(None, error)`` — *error* goes into the
    retry task, so it stays short and names exactly what was wrong.
    """
    parsed = _last_json_object(reply)
    if parsed is None:
        return None, "no JSON object found in the reply"

    clips = parsed.get("clips")
    rationale = parsed.get("rationale")

    if not isinstance(clips, list) or not clips:
        return None, "clips is missing, not a list, or empty"
    if not all(isinstance(i, int) and not isinstance(i, bool) for i in clips):
        return None, "clips must be a list of integers"
    if not isinstance(rationale, str) or not rationale.strip():
        return None, "rationale is missing or not a string"

    out_of_range = sorted({i for i in clips if not 0 <= i < len(candidates)})
    if out_of_range:
        return None, f"{out_of_range} is not a candidate index (0..{len(candidates) - 1})"
    if len(set(clips)) != len(clips):
        return None, "the same clip number appears more than once"

    chosen = [candidates[i] for i in clips]
    available_assets = {c.asset_id for c in candidates}
    if len(available_assets) > 1 and len({c.asset_id for c in chosen}) < 2:
        return None, (
            "the selection uses only one video although several have material — an overview "
            "must cover at least two"
        )

    return (
        {"clips": chosen, "rationale": rationale.strip(), "fallback": False},
        None,
    )


def _fallback(candidates: list[Candidate]) -> OverviewDecision:
    """Deterministic: the candidates in their own order (assets by search score, chronological
    within an asset) — the scout must never leave the endpoint without SOME answer."""
    return {
        "clips": list(candidates),
        "rationale": "automatic fallback: top search scores",
        "fallback": True,
    }


# --- the real single-agent runner (autogen-touching) --------------------------------------------


def _build_scout_agent(config: AgentConfig) -> AssistantAgent:
    """One ``AssistantAgent``, no tools: everything it needs is in the task text (lazy autogen
    import, mirrors :func:`.scout._build_scout_agent`)."""
    try:
        from autogen_agentchat.agents import AssistantAgent
    except ImportError as exc:
        raise RuntimeError(
            "The short-creator needs the optional 'autoshort' extra. "
            "Install it with: uv sync --extra autoshort"
        ) from exc

    model_client = build_model_client(config, role="agent")
    return AssistantAgent(
        name="overview_scout",
        model_client=model_client,
        description="Picks and orders the clips of a multi-video overview.",
        system_message=(
            "You are the Overview Scout. Answer in the task's language — never switch "
            "languages. Given a topic and a numbered list of candidate clips, choose the ones "
            "that together explain the topic best and put them in a sensible watching order. "
            "Reply with EXACTLY one JSON object as your final message — nothing before or "
            "after it."
        ),
    )


def _last_message_text(result: Any) -> str:
    """The LAST non-empty message's text from a ``TaskResult`` (mirrors
    :func:`.scout._last_message_text`: messages[0] echoes the task itself)."""
    for msg in reversed(getattr(result, "messages", None) or []):
        to_text = getattr(msg, "to_model_text", None)
        text = (to_text() if callable(to_text) else str(getattr(msg, "content", ""))).strip()
        if text:
            return text
    return ""


def _default_runner(config: AgentConfig) -> Callable[[str], str]:
    def run(task: str) -> str:
        async def _run() -> str:
            agent = _build_scout_agent(config)
            result = await agent.run(task=task)
            return _last_message_text(result)

        return asyncio.run(asyncio.wait_for(_run(), _SCOUT_TIMEOUT_S))

    return run


# --- orchestration ------------------------------------------------------------------------------


def _safe_call(run: Callable[[str], str], task: str) -> str | None:
    try:
        return run(task)
    except Exception:  # noqa: BLE001 — any runner failure degrades to the fallback
        logger.warning("overview scout runner failed; falling back", exc_info=True)
        return None


def _safe_validate(
    candidates: list[Candidate], reply: str
) -> tuple[OverviewDecision | None, str | None]:
    try:
        return _validate_reply(candidates, reply)
    except Exception as exc:  # noqa: BLE001 — must not escape run_overview_scout
        logger.warning("overview scout validation failed; treating as invalid", exc_info=True)
        return None, f"validation failed: {exc}"


def run_overview_scout(
    config: AgentConfig,
    *,
    topic: str,
    candidates: list[Candidate],
    target_seconds: int,
    fps_by_asset: dict[str, tuple[int, int]],
    runner: Callable[[str], str] | None = None,
) -> OverviewDecision:
    """Pick and order the overview's clips out of *candidates*.

    ``runner`` takes the composed task text and returns the agent's final reply; ``None``
    builds the real one. An invalid reply gets exactly ONE retry with the error appended; a
    runner exception goes straight to the fallback (no retry storm). The result is trimmed to
    ``target_seconds`` either way. *candidates* empty is a programming error — the endpoint
    guards it with a 422 before calling — and raises ``ValueError``.
    """
    if not candidates:
        raise ValueError("candidates is empty — nothing for the overview scout to choose from")

    run = runner if runner is not None else _default_runner(config)
    task = _task_text(topic, candidates, target_seconds, fps_by_asset)

    decision: OverviewDecision | None = None
    reply = _safe_call(run, task)
    if reply is not None:
        decision, error = _safe_validate(candidates, reply)
        if decision is None:
            assert error is not None  # decision is None => _safe_validate always sets error
            retry_reply = _safe_call(run, _retry_task_text(task, error))
            if retry_reply is not None:
                decision, _error = _safe_validate(candidates, retry_reply)

    if decision is None:
        decision = _fallback(candidates)

    decision["clips"] = trim_to_target(
        decision["clips"], target_seconds=target_seconds, fps_by_asset=fps_by_asset
    )
    return decision
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_overview_scout.py -q
```
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/laura/short_creator/overview_scout.py tests/test_overview_scout.py
git commit -m "feat(short-creator): the overview scout picks clips by index, never by frame"
```

---

### Task 4: Build the montage (source timeline + scenes + sequence)

**Files:**
- Create: `services/local-api/src/laura/short_creator/overview_build.py`
- Test: `services/local-api/tests/test_overview_build.py`

**Interfaces:**
- Consumes: Task 2's `Candidate`.
- Produces: `build_overview(db, *, project_id, topic, clips) -> OverviewBuild`, an
  `OverviewBuild` TypedDict with `sequence_id: str`, `source_timeline_id: str`,
  `scene_ids: list[str]`.

- [ ] **Step 1: Write the failing tests**

Create `services/local-api/tests/test_overview_build.py`:

```python
"""build_overview writes the montage on three levels — its own source timeline, one scene per
clip, its own sequence — and touches nothing else (spec 2026-07-31-auto-overview-design.md §5).
"""

from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.overview_build import build_overview
from laura.short_creator.overview_windows import Candidate

FPS = 30


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _project_with_two_assets(db: Database) -> tuple[str, str, str]:
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    a = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="A", source_path="/tmp/a"
    )
    b = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="B", source_path="/tmp/b"
    )
    return str(project["id"]), str(a["id"]), str(b["id"])


def test_builds_source_timeline_scenes_and_sequence(tmp_path: Path) -> None:
    db = _db(tmp_path)
    project_id, a, b = _project_with_two_assets(db)
    clips = [
        Candidate(a, "A", 1, 100, 400, "alpha"),   # 300 frames
        Candidate(b, "B", 2, 500, 650, "beta"),    # 150 frames
    ]

    out = build_overview(db, project_id=project_id, topic="mission", clips=clips)

    source = repos.get_timeline(db, out["source_timeline_id"])
    assert source is not None
    assert source["kind"] == "overview", "must NOT be rough_cut — see get_asset_rough_cut"
    assert "mission" in source["name"]

    # One clip per window, laid out contiguously, source ranges preserved.
    rows = repos.list_timeline_clips(db, out["source_timeline_id"])
    assert [(r["asset_id"], r["src_in_frame"], r["src_out_frame_exclusive"]) for r in rows] == [
        (a, 100, 400),
        (b, 500, 650),
    ]
    assert [(r["seq_in_frame"], r["seq_out_frame_exclusive"]) for r in rows] == [
        (0, 300),
        (300, 450),
    ]

    # One scene per clip, each materialized, in clip order.
    scenes = repos.list_scenes(db, out["source_timeline_id"])
    assert len(scenes) == 2
    assert out["scene_ids"] == [str(s["id"]) for s in scenes]
    assert all(s["scene_timeline_id"] for s in scenes)

    # The sequence references those scenes, in order.
    sequence = repos.get_timeline(db, out["sequence_id"])
    assert sequence is not None
    assert sequence["kind"] == "sequence"
    items = repos.list_sequence_items(db, out["sequence_id"])
    assert [str(i["scene_id"]) for i in items] == out["scene_ids"]


def test_the_project_sequence_is_left_alone(tmp_path: Path) -> None:
    """`get_or_create_project_sequence` returns the OLDEST sequence — the user's assembly."""
    db = _db(tmp_path)
    project_id, a, _b = _project_with_two_assets(db)
    existing = repos.get_or_create_project_sequence(db, project_id)

    out = build_overview(
        db, project_id=project_id, topic="mission",
        clips=[Candidate(a, "A", 1, 0, 300, "alpha")],
    )

    assert out["sequence_id"] != existing["id"]
    assert repos.list_sequence_items(db, existing["id"]) == []
    # And it is still the one the project sequence lookup returns.
    assert repos.get_or_create_project_sequence(db, project_id)["id"] == existing["id"]


def test_a_materialized_scene_holds_exactly_its_own_clip(tmp_path: Path) -> None:
    """materialize_scene re-offsets to 0 — the scene timeline is what flatten_sequence reads."""
    db = _db(tmp_path)
    project_id, a, b = _project_with_two_assets(db)
    clips = [
        Candidate(a, "A", 1, 100, 400, "alpha"),
        Candidate(b, "B", 2, 500, 650, "beta"),
    ]

    out = build_overview(db, project_id=project_id, topic="mission", clips=clips)

    scenes = repos.list_scenes(db, out["source_timeline_id"])
    second = repos.list_timeline_clips(db, str(scenes[1]["scene_timeline_id"]))
    assert len(second) == 1
    assert second[0]["asset_id"] == b
    assert (second[0]["src_in_frame"], second[0]["src_out_frame_exclusive"]) == (500, 650)
    assert (second[0]["seq_in_frame"], second[0]["seq_out_frame_exclusive"]) == (0, 150)


def test_empty_clips_is_a_programming_error(tmp_path: Path) -> None:
    db = _db(tmp_path)
    project_id, _a, _b = _project_with_two_assets(db)
    try:
        build_overview(db, project_id=project_id, topic="mission", clips=[])
    except ValueError as exc:
        assert "clips" in str(exc)
    else:  # pragma: no cover - the raise is the contract
        raise AssertionError("expected ValueError")
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_overview_build.py -q
```
Expected: FAIL with `ModuleNotFoundError: No module named 'laura.short_creator.overview_build'`.

- [ ] **Step 3: Implement the module**

Create `services/local-api/src/laura/short_creator/overview_build.py`:

```python
"""Write the overview montage into the existing timeline/scene/sequence tables (spec
2026-07-31-auto-overview-design.md §5).

Three levels, no migration and no new vessel:

1. one ``kind="overview"`` source timeline holding one clip per window,
2. one scene per clip over that timeline, materialized,
3. one NEW ``kind="sequence"`` timeline referencing those scenes in order.

Two properties this arrangement buys, both load-bearing:

* ``kind="overview"`` keeps the timeline out of ``repos.get_asset_rough_cut``
  (``kind='rough_cut' AND created_from=<asset_id>``), so an overview can never be mistaken for
  a video's rough cut.
* scenes are listed by ``source_timeline_id`` (:func:`repos.list_scenes`), so the videos' own
  scene lists — and the scene NUMBERS auto-short resolves through ``order_index + 1`` — stay
  exactly as they were.
"""

from __future__ import annotations

import logging
from typing import TypedDict

from ..db import repos
from ..db.database import Database
from ..editing.otio_sync import rebuild_otio
from ..scenes.materialize import materialize_scene
from .overview_windows import Candidate

logger = logging.getLogger(__name__)

# Timeline names are shown in the UI; keep the topic readable but bounded.
_NAME_TOPIC_CHARS = 60


class OverviewBuild(TypedDict):
    sequence_id: str
    source_timeline_id: str
    scene_ids: list[str]


def _name(topic: str) -> str:
    trimmed = topic.strip()[:_NAME_TOPIC_CHARS]
    return f"Überblick: {trimmed}"


def build_overview(
    db: Database, *, project_id: str, topic: str, clips: list[Candidate]
) -> OverviewBuild:
    """Write *clips* as a self-contained, editable montage and return its ids.

    *clips* empty is a programming error (the endpoint 422s before calling) -> ``ValueError``.
    """
    if not clips:
        raise ValueError("clips is empty — nothing to build an overview from")

    name = _name(topic)
    source = repos.create_timeline(db, project_id=project_id, name=name, kind="overview")
    source_id = str(source["id"])

    rows = []
    ranges: list[tuple[int, int]] = []
    offset = 0
    for clip in clips:
        length = clip.length_frames
        rows.append(
            {
                "asset_id": clip.asset_id,
                "src_in_frame": clip.start_frame,
                "src_out_frame_exclusive": clip.end_frame_exclusive,
                "seq_in_frame": offset,
                "seq_out_frame_exclusive": offset + length,
            }
        )
        ranges.append((offset, offset + length))
        offset += length
    repos.replace_timeline_clips(db, source_id, rows)
    rebuild_otio(db, source_id)

    # One scene per clip. replace_scenes assigns order_index positionally, so list_scenes
    # returns them in exactly the clip order built above.
    repos.replace_scenes(db, project_id, source_id, ranges)
    scene_ids: list[str] = []
    for scene in repos.list_scenes(db, source_id):
        materialize_scene(db, scene)
        scene_ids.append(str(scene["id"]))

    sequence = repos.create_timeline(
        db, project_id=project_id, name=name, kind="sequence", created_from=source_id
    )
    sequence_id = str(sequence["id"])
    repos.replace_sequence_items(db, sequence_id, scene_ids)
    rebuild_otio(db, sequence_id)

    logger.info(
        "auto-overview built: sequence=%s source=%s clips=%d", sequence_id, source_id, len(clips)
    )
    return {
        "sequence_id": sequence_id,
        "source_timeline_id": source_id,
        "scene_ids": scene_ids,
    }
```

- [ ] **Step 4: Run the tests**

```bash
uv run pytest tests/test_overview_build.py -q
```
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/laura/short_creator/overview_build.py tests/test_overview_build.py
git commit -m "feat(short-creator): write the overview as its own timeline, scenes and sequence"
```

---

### Task 5: The endpoint

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py` (add a request model near
  `ProjectAutoShortRequest` around line 61, imports at the top, the route after
  `create_project_auto_short` around line 462)
- Test: `services/local-api/tests/test_auto_overview_endpoint.py`

**Interfaces:**
- Consumes: `search_material` (Task 1), `build_candidates`/`trim_to_target` (Task 2),
  `run_overview_scout` (Task 3), `build_overview` (Task 4).
- Produces: `POST /projects/{project_id}/auto-overview` → 202 with `sequence_id`,
  `source_timeline_id`, `clips`, `rationale`, `fallback`, `ranking`, `warnings`, `job_id`,
  `export_id`.

- [ ] **Step 1: Write the failing tests**

Create `services/local-api/tests/test_auto_overview_endpoint.py`:

```python
"""POST /projects/{project_id}/auto-overview — topic in, a new sequence plus a render out
(spec 2026-07-31-auto-overview-design.md §1).

Mirrors test_auto_short_endpoint.py: app factory + token header, a real DB seed, and the
scout monkeypatched at ``laura.api.short_creator.run_overview_scout`` — imported at module
level exactly so this works — so no test ever touches an LLM.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.main import create_app
from laura.short_creator import discovery
from laura.short_creator.overview_scout import OverviewDecision
from laura.short_creator.overview_windows import Candidate

FPS = 30
_TOKEN = "test-token"
_H = {"X-Laura-Token": _TOKEN}


def _app(tmp_path: Path) -> tuple[TestClient, SqliteDatabase]:
    settings = Settings(workspace_root=tmp_path / "ws", token=_TOKEN, start_runner=False)
    app = create_app(settings)
    db: SqliteDatabase = app.state.db
    return TestClient(app), db


def _seed_asset_with_scenes(
    db: Database, project_id: str, name: str, *, segments: list[tuple[int, int, str]]
) -> str:
    """Asset + succeeded analysis run with *segments* + a rough cut over [0,600) with two
    scenes [0,300)/[300,600). Mirrors test_auto_short_endpoint.py's helper of the same name."""
    asset = repos.create_asset(
        db, project_id=project_id, type="video", display_name=name, source_path=f"/tmp/{name}"
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
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
        db, project_id=project_id, name="Rough Cut", kind="rough_cut", created_from=asset["id"]
    )
    repos.add_timeline_clip(
        db, timeline_id=timeline["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=600,
        seq_in_frame=0, seq_out_frame_exclusive=600,
    )
    repos.replace_scenes(db, project_id, timeline["id"], [(0, 300), (300, 600)])
    return str(asset["id"])


def _seed_two_assets(db: Database) -> tuple[str, str, str]:
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    a = _seed_asset_with_scenes(
        db, project["id"], "a.mp4", segments=[(10, 200, "the agent farm plans the mission")]
    )
    b = _seed_asset_with_scenes(
        db, project["id"], "b.mp4", segments=[(20, 220, "the mission handoff is executed")]
    )
    return str(project["id"]), a, b


def _decision(a: str, b: str) -> OverviewDecision:
    return {
        "clips": [
            Candidate(a, "a.mp4", 1, 0, 300, "the agent farm plans the mission"),
            Candidate(b, "b.mp4", 1, 0, 240, "the mission handoff is executed"),
        ],
        "rationale": "one video sets it up, the other shows it running",
        "fallback": False,
    }


def _counts(db: Database) -> tuple[int, int]:
    with db.connection() as conn:
        timelines = conn.execute("SELECT COUNT(*) FROM timelines").fetchone()[0]
        scenes = conn.execute("SELECT COUNT(*) FROM scenes").fetchone()[0]
    return int(timelines), int(scenes)


def test_happy_path_builds_a_sequence_and_enqueues_a_render(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical, deterministic
    client, db = _app(tmp_path)
    project_id, a, b = _seed_two_assets(db)
    monkeypatch.setattr(
        "laura.api.short_creator.run_overview_scout", lambda *_a, **_kw: _decision(a, b)
    )

    r = client.post(
        f"/projects/{project_id}/auto-overview", json={"topic": "mission"}, headers=_H
    )

    assert r.status_code == 202, r.text
    body = r.json()
    assert body["fallback"] is False
    assert body["rationale"].startswith("one video sets it up")
    assert [c["asset_id"] for c in body["clips"]] == [a, b]
    assert body["ranking"], "the ranking must be surfaced, not just the winner"
    assert isinstance(body["warnings"], list)

    # The sequence exists, references two scenes, and is NOT the project sequence.
    sequence = repos.get_timeline(db, body["sequence_id"])
    assert sequence is not None and sequence["kind"] == "sequence"
    assert len(repos.list_sequence_items(db, body["sequence_id"])) == 2
    assert repos.get_or_create_project_sequence(db, project_id)["id"] != body["sequence_id"]

    # The source timeline carries the protective kind.
    source = repos.get_timeline(db, body["source_timeline_id"])
    assert source is not None and source["kind"] == "overview"

    # A render job was enqueued for THIS sequence.
    job = repos.get_job(db, body["job_id"])
    assert job is not None and job["kind"] == "export.render"
    payload = json.loads(job["payload_json"])
    assert payload["export_id"] == body["export_id"]
    export = repos.get_export(db, body["export_id"])
    assert export is not None
    assert export["timeline_id"] == body["sequence_id"]


def test_unknown_project_is_404(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    client, _db = _app(tmp_path)
    r = client.post("/projects/nope/auto-overview", json={"topic": "mission"}, headers=_H)
    assert r.status_code == 404


def test_missing_extra_is_503(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: False)
    client, db = _app(tmp_path)
    project_id, _a, _b = _seed_two_assets(db)
    r = client.post(
        f"/projects/{project_id}/auto-overview", json={"topic": "mission"}, headers=_H
    )
    assert r.status_code == 503
    assert "autoshort" in r.json()["detail"]


def test_no_material_is_422_and_writes_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """The corpse rule: a topic nothing matches leaves no timeline and no scene behind."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project_id, _a, _b = _seed_two_assets(db)
    before = _counts(db)

    r = client.post(
        f"/projects/{project_id}/auto-overview",
        json={"topic": "quantum chromodynamics"},
        headers=_H,
    )

    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "no material found for topic"
    assert _counts(db) == before


def test_hits_too_short_for_a_window_are_422_not_an_empty_sequence(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Material exists but every window falls under the 4s floor -> 422, still no writes."""
    monkeypatch.setattr("laura.api.short_creator._autoshort_available", lambda: True)
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    client, db = _app(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    # A 2-frame segment padded by 1s each side is 62 frames ~ 2.1s — under the floor.
    _seed_asset_with_scenes(
        db, project["id"], "tiny.mp4", segments=[(10, 12, "the mission")]
    )
    before = _counts(db)

    r = client.post(
        f"/projects/{project['id']}/auto-overview", json={"topic": "mission"}, headers=_H
    )

    assert r.status_code == 422
    assert r.json()["detail"]["reason"] == "no usable windows for topic"
    assert _counts(db) == before
```

- [ ] **Step 2: Run to verify it fails**

```bash
uv run pytest tests/test_auto_overview_endpoint.py -q
```
Expected: FAIL — 404 on the route (it does not exist yet).

- [ ] **Step 3: Add the request model**

In `services/local-api/src/laura/api/short_creator.py`, after `ProjectAutoShortRequest`
(around line 73):

```python
class ProjectAutoOverviewRequest(BaseModel):
    """Body for ``POST /projects/{project_id}/auto-overview`` — topic in, montage out.

    ``target_seconds`` defaults to a 3-minute overview (Phase 1's short defaults to 60s; an
    overview covering several videos needs room). ``language`` is accepted and echoed for
    symmetry with the short endpoints but changes nothing in v1: the cut runs on the clips'
    ORIGINAL audio, so there is no script to write in any language.
    """

    topic: str = Field(min_length=1, max_length=2000)
    target_seconds: int = Field(default=180, gt=0, le=1800)
    language: str = Field(default="German", min_length=2, max_length=40)
```

- [ ] **Step 4: Add the imports**

In the same file, next to the existing `from ..short_creator.discovery import search_material`
(around line 35):

```python
from ..short_creator.overview_build import build_overview
from ..short_creator.overview_scout import OverviewDecision, run_overview_scout
from ..short_creator.overview_windows import build_candidates
```

`run_overview_scout` is imported at module level (like `run_scout`) precisely so tests can
monkeypatch `laura.api.short_creator.run_overview_scout`. None of these modules import
autogen at load time.

- [ ] **Step 5: Add the route**

After `create_project_auto_short` (around line 462):

```python
# --- v2 project-scoped auto-overview endpoint (spec 2026-07-31) ---------------------------------


def _overview_scene_bounds(
    db: Database, project_id: str, ranking: list[dict[str, Any]]
) -> dict[tuple[str, int], tuple[int, int]]:
    """``(asset_id, scene_number) -> (src_start, src_end_exclusive)`` for every ranked asset.

    Built on :func:`discovery._scene_ranges`, the same READ-ONLY lookup the ranking itself
    used — probing must never create a rough cut.
    """
    from ..short_creator import discovery

    bounds: dict[tuple[str, int], tuple[int, int]] = {}
    for entry in ranking:
        asset_id = str(entry["asset_id"])
        ranges = discovery._scene_ranges(db, project_id, asset_id)
        for scene_number, start, end_exclusive in ranges or []:
            bounds[(asset_id, scene_number)] = (start, end_exclusive)
    return bounds


def _overview_fps(
    db: Database, project_id: str, ranking: list[dict[str, Any]]
) -> dict[str, tuple[int, int]]:
    """``asset_id -> (rate_num, rate_den)``, falling back to the project's rate for an asset
    that has none (probe failures leave the columns empty)."""
    project = repos.get_project(db, project_id)
    fallback = (
        int((project or {}).get("rate_num") or 25),
        int((project or {}).get("rate_den") or 1),
    )
    out: dict[str, tuple[int, int]] = {}
    for entry in ranking:
        asset_id = str(entry["asset_id"])
        asset = repos.get_asset(db, asset_id)
        if asset is None:
            out[asset_id] = fallback
            continue
        out[asset_id] = (
            int(asset.get("rate_num") or fallback[0]),
            int(asset.get("rate_den") or fallback[1]),
        )
    return out


@router.post("/projects/{project_id}/auto-overview", status_code=status.HTTP_202_ACCEPTED)
def create_project_auto_overview(
    project_id: str,
    body: ProjectAutoOverviewRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Topic in, a watchable overview cut across several videos out.

    Phase 2 of the auto-short arc: where ``POST /projects/{id}/auto-short`` scouts ONE asset
    and runs the board production on it, this route mixes SEVERAL videos through the sequence
    machinery — a new sequence (never the project's own) plus an enqueued render.

    404 unknown project; 503 preflight (missing extra / unusable agent config); 422 when the
    topic finds no material or no window survives — both BEFORE anything is written.
    """
    db = _db(request)
    if repos.get_project(db, project_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "project not found")
    _require_autoshort()
    _require_usable_agent_config()

    material = search_material(db, project_id, body.topic)
    if not material["ranking"]:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "reason": "no material found for topic",
                "skipped": material["skipped"],
                "source": material["source"],
            },
        )

    fps_by_asset = _overview_fps(db, project_id, material["ranking"])
    candidates = build_candidates(
        material["ranking"],
        scene_bounds=_overview_scene_bounds(db, project_id, material["ranking"]),
        fps_by_asset=fps_by_asset,
    )
    if not candidates:
        # Material matched, but every hit was too short (or its scene had vanished) to become
        # a watchable clip. Still nothing written — same corpse rule as above.
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={
                "reason": "no usable windows for topic",
                "source": material["source"],
            },
        )

    from ..short_creator.providers import config_warnings, resolve_from_env

    config = resolve_from_env()
    decision: OverviewDecision = run_overview_scout(
        config,
        topic=body.topic,
        candidates=candidates,
        target_seconds=body.target_seconds,
        fps_by_asset=fps_by_asset,
    )

    built = build_overview(
        db, project_id=project_id, topic=body.topic, clips=decision["clips"]
    )

    export = repos.create_export(
        db,
        project_id=project_id,
        timeline_id=built["sequence_id"],
        format="mp4",
        options={"burn_captions": False, "source": "auto-overview"},
    )
    job_id = enqueue(
        db,
        queue=queue_for("export.render"),
        kind="export.render",
        payload={"export_id": export["id"]},
        idempotency_key=f"render:{export['id']}",
    )

    warnings = config_warnings(config)
    if len({c.asset_id for c in decision["clips"]}) < 2:
        names = sorted({c.display_name for c in decision["clips"]})
        warnings = [
            *warnings,
            f"overview covers a single source: only {', '.join(names)} matched the topic",
        ]

    return {
        "sequence_id": built["sequence_id"],
        "source_timeline_id": built["source_timeline_id"],
        "clips": [
            {
                "asset_id": c.asset_id,
                "display_name": c.display_name,
                "scene_number": c.scene_number,
                "start_frame": c.start_frame,
                "end_frame_exclusive": c.end_frame_exclusive,
                "snippet": c.snippet,
            }
            for c in decision["clips"]
        ],
        "rationale": decision["rationale"],
        "fallback": decision["fallback"],
        "ranking": material["ranking"],
        "warnings": warnings,
        "export_id": export["id"],
        "job_id": job_id,
    }
```

The response comprehension iterates `decision["clips"]` (already typed `list[Candidate]` via
`OverviewDecision`), so `Candidate` itself is never named here — do not add it to the imports.

- [ ] **Step 6: Run the endpoint tests**

```bash
uv run pytest tests/test_auto_overview_endpoint.py -q
```
Expected: PASS (5 tests).

- [ ] **Step 7: Commit**

```bash
git add src/laura/api/short_creator.py tests/test_auto_overview_endpoint.py
git commit -m "feat(short-creator): auto-overview endpoint - topic in, a watchable cut out"
```

---

### Task 6: Gates, documentation, todo tick

**Files:**
- Modify: `docs/agentic-short-creator.md`
- Modify: `tasks/todo.md`

**Interfaces:**
- Consumes: everything above.
- Produces: a green branch and a documented feature.

- [ ] **Step 1: Run the full gates**

From `services/local-api`:

```bash
uv run pytest -q
```
Expected: exit code 0. This suite reproducibly swallows the final "N passed" line on this
machine — judge by the exit code, and echo it explicitly if you run it in the background.

```bash
uv run mypy
```
Expected: `Success`. Bare, no `src` argument — CI type-checks the tests too, and `mypy src`
has hidden real errors here before.

```bash
uv run ruff check src tests
```
Expected: `All checks passed!`.

- [ ] **Step 2: Fix anything the gates report**

Likely candidates, from this repo's history: an unused import in `api/short_creator.py`
(ruff), and `dict[str, Any]` variance in the `warnings` list (mypy). Fix the cause, not the
symptom — do not add `# type: ignore` without a comment naming why.

- [ ] **Step 3: Document the endpoint**

Append to `docs/agentic-short-creator.md`:

```markdown
## Auto-Overview: ein Thema, mehrere Videos

`POST /projects/{project_id}/auto-overview` mit `{"topic": "...", "target_seconds": 180}`
baut aus den Transkript-Treffern **mehrerer** Videos eine Überblicks-Montage: kurze
Ausschnitte rund um die Fundstellen, vom Scout ausgewählt und geordnet, als **eigene** Sequenz
abgelegt (die Montage in „Zusammenfügen" bleibt unberührt) und sofort als MP4 gerendert.

Antwort (202): `sequence_id`, `source_timeline_id`, `clips`, `rationale`, `fallback`,
`ranking`, `warnings`, `export_id`, `job_id`. Den Render verfolgt man über `/jobs/{job_id}`,
das Ergebnis liegt unter `/exports/{export_id}`.

Voraussetzungen wie beim Auto-Short: das Extra `autoshort` und eine brauchbare
Agenten-Konfiguration (sonst 503). Ohne Treffer oder ohne brauchbares Fenster kommt ein 422 —
und es wird nichts angelegt. Der Ton ist der Originalton der Ausschnitte; eine Erzählstimme
ist bewusst ein eigener Zyklus.

Spec: `docs/superpowers/specs/2026-07-31-auto-overview-design.md`.
```

- [ ] **Step 4: Tick the todo**

In `tasks/todo.md`, append to the auto-short section:

```markdown
### Auto-Overview (Phase 2)  `[x]` (Spec 2026-07-31, Plan 2026-07-31-auto-overview)
- [x] `POST /projects/{pid}/auto-overview` — Discovery mit Segment-Frames, deterministische
      Kandidaten-Fenster, Scout wählt per Index, eigene Sequenz + Render
- [ ] **Live-Test offen:** ein echter Lauf gegen `workspace-livetest` (Semantik braucht den
      `laura-qdrant`-Container auf 127.0.0.1:6333)
- [ ] UI-Einstieg und ein Umschalter zwischen mehreren Sequenzen — eigener Zyklus
```

- [ ] **Step 5: Commit**

```bash
git add docs/agentic-short-creator.md tasks/todo.md
git commit -m "docs(short-creator): document the auto-overview endpoint"
```

---

## Manual verification (not headless-testable)

The render itself needs real media and ffmpeg, so the automated suite stops at "the job was
enqueued". A real run is the honest proof:

1. Start `laura-qdrant` (`127.0.0.1:6333`) and the backend against `workspace-livetest` with
   `LAURA_AGENT_PROVIDER=openai-compat`, `LAURA_AGENT_MODEL=gpt-4o`, `LAURA_AGENT_API_KEY`
   set — a key alone silently runs `qwen2.5:7b`.
2. `POST /projects/<livetest project>/auto-overview` with a topic that spans two videos.
3. Expect: 202, `fallback: false`, clips from at least two `asset_id`s, and — after the job
   finishes — a playable MP4 whose length is near `target_seconds`.
4. Check that the project sequence in "Zusammenfügen" is unchanged and the videos' scene
   numbers still match what auto-short resolves.
