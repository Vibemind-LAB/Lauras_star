# Window-Bias Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Held/static screens stop losing screen time to motion: the review prompt gets a window-length rubric, the cutlist stops using window durations as weights, and a report script pins the before/after evidence.

**Architecture:** Two root causes, one lever each (spec `docs/superpowers/specs/2026-07-20-window-bias-design.md`): (1) the `windows` instruction in `_REVIEW_PROMPT` gets the anti-reel rule the score already has; (2) `_segment_duration_s` drops `window` entirely — a window marks WHERE a segment starts and WHICH beat is cut, never how much weight it carries. A standalone script `scripts/measure_window_bias.py` reproduces the baseline table from the livetest boards and reruns after the next live run.

**Tech Stack:** Python 3.11 + uv, pydantic v2, pytest, mypy strict, ruff (line length 100).

## Global Constraints

- Python via `uv` in `services/local-api/`; run tests as `uv run pytest …`, gates are `uv run mypy src` (must print "Success") and `uv run ruff check src tests` (must print "All checks passed!").
- **Never run two `uv run` commands in parallel in this repo**; judge pytest by exit code, not by grepping "passed" (the suite reproducibly swallows the final line on this machine). Run all commands in the FOREGROUND; raise the Bash timeout parameter (max 600000) for the full suite instead of backgrounding.
- mypy is strict: annotate everything, no `Any` leaks in signatures. `print` is allowed ONLY in `scripts/` (precedent: `scripts/smoke_server.py`); everywhere else module logger.
- Unchanged by design (spec §2/§Nicht-im-Scope): `segment_capacity_seconds`, `_scale_chapter_durations`, `storyline_material_seconds`, the v1 `visual_hook` in `laura/analysis/visual_query.py`. Do not touch them.
- The decoupling applies uniformly — ALSO in the sidecar-less fallback (a window cap only there would re-import the bias through the back door).
- Commits: conventional commits, English, explicit `git add <paths>` (never `-A` — the branch can carry unrelated WIP), commit trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- All commands below run from `services/local-api/` unless a path says otherwise.

---

### Task 1: Window rubric in `_REVIEW_PROMPT`

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (constant `_REVIEW_PROMPT`, the `"windows"` line, ~line 171)
- Test: `services/local-api/tests/test_production_tools_review.py` (append)

**Interfaces:**
- Consumes: existing constant `_REVIEW_PROMPT` (module-level string in `production_tools.py`).
- Produces: nothing later tasks depend on (prompt text only).

- [ ] **Step 1: Write the failing test**

Append to `services/local-api/tests/test_production_tools_review.py` (directly after the existing score-rubric test, `test_review_prompt_hook_rubric…` around line 448 — same pattern):

```python
def test_review_prompt_window_rubric_counters_reel_logic() -> None:
    """Pins the WINDOW rubric against reel logic — the score rubric's sibling.

    Baseline (spec 2026-07-20-window-bias-design.md): 8 of 36 static-scene reviews carried
    sub-second windows; the shipped films cut a 45s org chart from three 0.5s windows while
    other runs gave the SAME scene 15s or 45s. The prompt must say a held readable screen is
    ONE long window, or the VLM defaults to its social-video prior.
    """
    from laura.short_creator.production_tools import _REVIEW_PROMPT

    assert "ONE window spanning the whole readable stretch" in _REVIEW_PROMPT
    assert "sub-second" in _REVIEW_PROMPT
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_production_tools_review.py::test_review_prompt_window_rubric_counters_reel_logic -v`
Expected: FAIL — `assert "ONE window spanning the whole readable stretch" in _REVIEW_PROMPT` is False.

- [ ] **Step 3: Extend the windows instruction**

In `services/local-api/src/laura/short_creator/production_tools.py`, inside `_REVIEW_PROMPT`, replace the windows line:

```python
    "  \"windows\": [{{\"offset_s\": float, \"duration_s\": float, \"roi\": {{\"x\": float, "
    "\"y\": float, \"w\": float, \"h\": float}} | null}}] (1-4 strong moments, STRONGEST "
    "FIRST, non-overlapping, offsets relative to scene start; a long scene with several "
    "distinct beats should list each beat as its own window; {roi_rule}),\n"
```

with:

```python
    "  \"windows\": [{{\"offset_s\": float, \"duration_s\": float, \"roi\": {{\"x\": float, "
    "\"y\": float, \"w\": float, \"h\": float}} | null}}] (1-4 strong moments, STRONGEST "
    "FIRST, non-overlapping, offsets relative to scene start; a long scene with several "
    "distinct beats should list each beat as its own window; a held/static screen whose "
    "content stays readable is ONE window spanning the whole readable stretch — never chop "
    "stillness into sub-second beats; {roi_rule}),\n"
```

- [ ] **Step 4: Run the review test file**

Run: `uv run pytest tests/test_production_tools_review.py -q`
Expected: exit 0 (new test passes, existing prompt/review tests stay green).

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_review.py
git commit -m "feat(short-creator): the window rubric counters reel logic like the score rubric

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `_segment_duration_s` stops weighing windows

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (function `_segment_duration_s` ~line 934, its single call site in `build_cutlist` ~lines 1821–1828)
- Test: `services/local-api/tests/test_production_tools_cutlist.py` (append)

**Interfaces:**
- Consumes: `BestWindow` (pydantic model, `laura.short_creator.board_models`), test helpers `_seed_two_scenes` / `_board` / `_review` / `_storyline` / `_script` / `_save_voice` and constants `FPS` (30) / `SCENE_FRAMES` (300) already defined in `test_production_tools_cutlist.py`.
- Produces: new signature `_segment_duration_s(*, target_seconds: float, n_scenes: int, scene_duration_s: float) -> float` — the `window` parameter is REMOVED (windows keep supplying only the segment's start offset and the storyline's beat reference in `build_cutlist`).

- [ ] **Step 1: Write the failing unit tests**

Append to `services/local-api/tests/test_production_tools_cutlist.py` (the file already imports `_scale_chapter_durations` from `laura.short_creator.production_tools` at line 38 — extend that import with `_segment_duration_s`):

```python
def test_segment_duration_ignores_window_length() -> None:
    """The decoupling (spec 2026-07-20-window-bias-design.md §2): a reel-trained reviewer's
    0.5s window must not shrink a held screen's weight below the chapter budget. Before the
    fix the window's duration capped the base (0.5s window -> 2.0s weight vs 8.0s for a long
    window in the same chapter)."""
    assert (
        _segment_duration_s(target_seconds=16.0, n_scenes=2, scene_duration_s=10.0) == 8.0
    )


def test_segment_duration_keeps_floor_and_scene_clamp() -> None:
    assert _segment_duration_s(target_seconds=1.0, n_scenes=2, scene_duration_s=10.0) == 2.0
    assert _segment_duration_s(target_seconds=60.0, n_scenes=2, scene_duration_s=4.5) == 4.5
```

- [ ] **Step 2: Run the unit tests to verify they fail**

Run: `uv run pytest tests/test_production_tools_cutlist.py::test_segment_duration_ignores_window_length -v`
Expected: FAIL — `TypeError` (current signature requires the `window` kwarg). That TypeError IS the red assertion for the signature change.

- [ ] **Step 3: Write the failing end-to-end test**

Append to the same file:

```python
def test_build_cutlist_gives_short_and_long_windows_equal_time(tmp_path: Path) -> None:
    """One chapter, two scenes: a 0.5s window (held screen, reel-scored) and an 8.0s window
    (motion). The chapter's 6.0s audio window must split EQUALLY (3.0s each) — before the
    decoupling the bases were [2.0, 8.0] and the split came out [2.0, 4.0]."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1, best_window=BestWindow(offset_s=0.0, duration_s=0.5))
    _review(board, 2, best_window=BestWindow(offset_s=0.0, duration_s=8.0))
    board.save("storyline", _storyline(scene_numbers=[1, 2], target_seconds=16.0))
    board.save("script", _script())
    # All six words belong to chapter 1; the voice ends at 5.4s -> one chapter audio window
    # [0.0, 6.0) incl. the 0.6s tail. Bases [8.0, 8.0] scale by one factor to [3.0, 3.0].
    _save_voice(
        board,
        tmp_path,
        words=[
            {"text": "Stopp", "start_s": 0.0, "end_s": 0.3},
            {"text": "dein", "start_s": 0.3, "end_s": 0.6},
            {"text": "Team", "start_s": 0.6, "end_s": 1.0},
            {"text": "Ein", "start_s": 1.2, "end_s": 1.4},
            {"text": "Klick", "start_s": 1.4, "end_s": 1.7},
            {"text": "genügt", "start_s": 1.7, "end_s": 5.4},
        ],
    )
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out == {"ok": True, "segments": 2, "total_seconds": 6.0, "with_zoom": 0}
    cutlist = board.load("cutlist")
    assert isinstance(cutlist, Cutlist)
    seg0, seg1 = cutlist.segments
    # 3.0s each (90 frames @ 30fps), starting at each window's offset.
    assert (seg0.start_frame, seg0.end_frame_exclusive) == (0, 90)
    assert (seg1.start_frame, seg1.end_frame_exclusive) == (300, 390)
```

- [ ] **Step 4: Run the e2e test to verify it fails**

Run: `uv run pytest tests/test_production_tools_cutlist.py::test_build_cutlist_gives_short_and_long_windows_equal_time -v`
Expected: FAIL — with the old code the split is `[2.0, 4.0]`: `seg0` ends at frame 60 (not 90) and `seg1` at 420 (not 390).

- [ ] **Step 5: Implement the decoupling**

In `services/local-api/src/laura/short_creator/production_tools.py`, replace the whole function:

```python
def _segment_duration_s(
    *, target_seconds: float, n_scenes: int, window: BestWindow, scene_duration_s: float
) -> float:
    """One segment's BASE cutlist length: the chapter's per-segment time budget, floored at
    2s and capped at the chosen review window's own length (itself floored at 2s, so a short
    highlight doesn't shrink the cap below the floor) — then clamped inside the scene's own
    duration.

    With a usable voice sidecar these are only the WEIGHTS that ``_scale_chapter_durations``
    rescales to fill the chapter's audio window (:func:`chapter_audio_windows`); without one
    they are the segment durations themselves (the pre-coupling behavior)."""
    budget = target_seconds / n_scenes
    upper = max(window.duration_s, _SEGMENT_FLOOR_S)
    return min(max(_SEGMENT_FLOOR_S, min(budget, upper)), scene_duration_s)
```

with:

```python
def _segment_duration_s(
    *, target_seconds: float, n_scenes: int, scene_duration_s: float
) -> float:
    """One segment's BASE cutlist length: the chapter's per-segment time budget, floored at
    2s and clamped inside the scene's own duration.

    The review window is deliberately NOT a length input — it marks WHERE the segment starts
    and WHICH beat is cut. Using its duration as a weight let a reel-trained reviewer's 0.5s
    windows starve held screens of screen time (spec 2026-07-20-window-bias-design.md §2;
    baseline: the shipped films cut a 45s org chart from three 0.5s windows).

    With a usable voice sidecar these are only the WEIGHTS that ``_scale_chapter_durations``
    rescales to fill the chapter's audio window (:func:`chapter_audio_windows`); without one
    they are the segment durations themselves — decoupled there too, a cap only in one path
    would re-import the bias."""
    budget = target_seconds / n_scenes
    return min(max(_SEGMENT_FLOOR_S, budget), scene_duration_s)
```

At the single call site in `build_cutlist` (~lines 1821–1828), drop the `window=window` argument:

```python
                    base_durations.append(
                        _segment_duration_s(
                            target_seconds=chapter.target_seconds,
                            n_scenes=n_scenes,
                            scene_duration_s=scene_duration_s,
                        )
                    )
```

- [ ] **Step 6: Run the cutlist test file**

Run: `uv run pytest tests/test_production_tools_cutlist.py -q`
Expected: exit 0. The pre-existing coupling tests (`…couples_segment_durations_to_chapter_audio`, `…floors_when_chapter_audio_shorter_than_scenes`, `…stretch_stops_at_scene_end_keeping_offset`) use windows ≥ the per-scene budget, so their expected values are unchanged by design — if ANY of them fails, STOP and report DONE_WITH_CONCERNS instead of adjusting their numbers (that would mean the analysis in the spec is wrong).

- [ ] **Step 7: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_cutlist.py
git commit -m "feat(short-creator): a window marks where to cut, not how much time it weighs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: report script, gates, docs tick

**Files:**
- Create: `services/local-api/scripts/measure_window_bias.py`
- Test: `services/local-api/tests/test_measure_window_bias.py` (create)
- Modify: `tasks/todo.md` (repo root — section 20.E)

**Interfaces:**
- Consumes: nothing from Tasks 1–2 (pure stdlib; reads board JSON files).
- Produces: `scan_reviews(root: Path, fps: float = 30.0) -> list[dict[str, Any]]` and `summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]` in the script module (loaded via importlib in the test); CLI `uv run python scripts/measure_window_bias.py [AGENT_RUNS_DIR]`.

- [ ] **Step 1: Write the failing test**

Create `services/local-api/tests/test_measure_window_bias.py`:

```python
"""The window-bias report script, run against a synthetic board tree.

Spec 2026-07-20-window-bias-design.md §3: the same script that produced the baseline table
must run again after the next live run — this test pins its reading of the board layout
(current scene_reviews only, degraded rows excluded from the summary, unreadable files
skipped) so that rerun stays comparable.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType
from typing import Any

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "measure_window_bias.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("measure_window_bias", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_review(root: Path, run: str, scene: int, payload: dict[str, Any]) -> None:
    reviews = root / run / "board" / "scene_reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / f"scene_{scene}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_scan_and_summary_over_fixture_boards(tmp_path: Path) -> None:
    mod = _load()
    _write_review(
        tmp_path,
        "run-a",
        1,
        {
            "scene_number": 1,
            "src_start_frame": 0,
            "src_end_frame_exclusive": 1350,
            "description": "an org chart",
            "whats_happening": "no significant changes",
            "hook_score": 3,
            "best_window": {"offset_s": 0.0, "duration_s": 0.5},
            "windows": [
                {"offset_s": 0.0, "duration_s": 0.5},
                {"offset_s": 1.0, "duration_s": 0.5},
            ],
        },
    )
    _write_review(
        tmp_path,
        "run-a",
        2,
        {
            "scene_number": 2,
            "src_start_frame": 0,
            "src_end_frame_exclusive": 900,
            "description": "the cursor drags a node",
            "whats_happening": "a panel opens",
            "hook_score": 7,
            "best_window": {"offset_s": 2.0, "duration_s": 8.0},
            "windows": [],
        },
    )
    _write_review(
        tmp_path,
        "run-a",
        3,
        {
            "scene_number": 3,
            "src_start_frame": 0,
            "src_end_frame_exclusive": 300,
            "description": "transcript only",
            "whats_happening": "",
            "hook_score": 5,
            "best_window": {"offset_s": 0.0, "duration_s": 4.0},
            "windows": [],
            "degraded": True,
        },
    )
    (tmp_path / "run-a" / "board" / "scene_reviews" / "scene_9.json").write_text(
        "{not json", encoding="utf-8"
    )

    rows = mod.scan_reviews(tmp_path)

    assert [r["scene"] for r in rows] == [1, 2, 3]  # scene_9 skipped, order stable
    static_row, moving_row, degraded_row = rows
    assert static_row["static"] is True
    assert static_row["scene_s"] == 45.0
    assert static_row["n_windows"] == 2
    assert static_row["min_window_s"] == 0.5
    assert moving_row["static"] is False
    assert moving_row["median_window_s"] == 8.0  # best_window fallback for windows == []
    assert degraded_row["degraded"] is True

    summary = mod.summarize(rows)
    assert summary["static"] == {
        "n": 1,
        "median_window_median_s": 0.5,
        "median_hook": 3,
        "reviews_with_subsecond_window": 1,
    }
    assert summary["moving"] == {
        "n": 1,
        "median_window_median_s": 8.0,
        "median_hook": 7,
        "reviews_with_subsecond_window": 0,
    }


def test_main_reports_missing_root(tmp_path: Path) -> None:
    mod = _load()
    assert mod.main([str(tmp_path / "nope")]) == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `uv run pytest tests/test_measure_window_bias.py -v`
Expected: FAIL — `_load()` errors because `scripts/measure_window_bias.py` does not exist.

- [ ] **Step 3: Write the script**

Create `services/local-api/scripts/measure_window_bias.py`:

```python
"""Window-bias report over production-board scene reviews.

    uv run python scripts/measure_window_bias.py [AGENT_RUNS_DIR]

Reads every board's CURRENT scene reviews under ``AGENT_RUNS_DIR`` (default
``workspace-livetest/agent-runs`` relative to the CWD; archived ``versions/`` stay out of
scope) and prints one row per review — scene length, window count, min/median window
duration, hook_score, a static-content indicator — plus a static-vs-moving summary over the
live (non-degraded) rows. Baseline table and purpose:
docs/superpowers/specs/2026-07-20-window-bias-design.md.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
import sys
from pathlib import Path
from typing import Any

_DEFAULT_ROOT = Path("workspace-livetest") / "agent-runs"
_FPS = 30.0
_STATIC_RX = re.compile(
    r"no significant change|static|remains|unchanged|slightly|stationary|little change",
    re.IGNORECASE,
)


def scan_reviews(root: Path, fps: float = _FPS) -> list[dict[str, Any]]:
    """One row per readable scene-review JSON under ``root``; unreadable files are skipped."""
    rows: list[dict[str, Any]] = []
    for review_path in sorted(root.glob("*/board/scene_reviews/scene_*.json")):
        try:
            data = json.loads(review_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        try:
            scene_s = (data["src_end_frame_exclusive"] - data["src_start_frame"]) / fps
            windows = [float(w["duration_s"]) for w in data.get("windows") or []] or [
                float(data["best_window"]["duration_s"])
            ]
        except (KeyError, TypeError, ValueError):
            continue
        text = f"{data.get('description', '')} {data.get('whats_happening', '')}"
        rows.append(
            {
                "run": review_path.parts[-4][:8],
                "scene": data.get("scene_number"),
                "scene_s": round(scene_s, 1),
                "n_windows": len(windows),
                "min_window_s": round(min(windows), 2),
                "median_window_s": round(statistics.median(windows), 2),
                "hook_score": data.get("hook_score"),
                "static": bool(_STATIC_RX.search(text)),
                "degraded": bool(data.get("degraded", False)),
            }
        )
    return rows


def summarize(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Static-vs-moving summary over the LIVE (non-degraded) rows."""
    live = [r for r in rows if not r["degraded"]]
    out: dict[str, dict[str, Any]] = {}
    for name in ("static", "moving"):
        group = [r for r in live if r["static"] is (name == "static")]
        if not group:
            continue
        hooks = [r["hook_score"] for r in group if isinstance(r["hook_score"], int)]
        out[name] = {
            "n": len(group),
            "median_window_median_s": round(
                statistics.median([r["median_window_s"] for r in group]), 2
            ),
            "median_hook": statistics.median(hooks) if hooks else None,
            "reviews_with_subsecond_window": sum(
                1 for r in group if r["min_window_s"] < 1.0
            ),
        }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", nargs="?", type=Path, default=_DEFAULT_ROOT)
    args = parser.parse_args(argv)
    root: Path = args.root
    if not root.is_dir():
        print(f"not a directory: {root}", file=sys.stderr)
        return 2
    rows = scan_reviews(root)
    live = [r for r in rows if not r["degraded"]]
    print(f"reviews total={len(rows)} live(VLM)={len(live)}")
    print(
        f"{'run':<10}{'scene':<7}{'scene_s':<9}{'n':<4}"
        f"{'min_w':<8}{'med_w':<8}{'hook':<6}static"
    )
    for r in live:
        print(
            f"{r['run']:<10}{str(r['scene']):<7}{r['scene_s']:<9}{r['n_windows']:<4}"
            f"{r['min_window_s']:<8}{r['median_window_s']:<8}{str(r['hook_score']):<6}"
            f"{r['static']}"
        )
    for name, stats in summarize(rows).items():
        print(
            f"{name}: n={stats['n']} "
            f"median_window_median={stats['median_window_median_s']}s "
            f"median_hook={stats['median_hook']} "
            f"reviews_with_subsecond_window={stats['reviews_with_subsecond_window']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `uv run pytest tests/test_measure_window_bias.py -v`
Expected: PASS (2/2).

- [ ] **Step 5: Reproduce the baseline against the real boards**

Run (from `services/local-api/`, the livetest dir sits at the repo root):
`uv run python scripts/measure_window_bias.py ../../workspace-livetest/agent-runs`
Expected final two lines (the spec's baseline table — reviews predate Tasks 1–2, so the numbers must MATCH, not improve):

```
static: n=36 median_window_median=2.0s median_hook=5.0 reviews_with_subsecond_window=8
moving: n=52 median_window_median=2.38s median_hook=6.5 reviews_with_subsecond_window=5
```

If the numbers differ, STOP — the script reads the boards differently than the baseline run did; report DONE_WITH_CONCERNS with the diff.

- [ ] **Step 6: Lint the script (not covered by the src/tests gate)**

Run: `uv run ruff check scripts/measure_window_bias.py tests/test_measure_window_bias.py`
Expected: "All checks passed!"

- [ ] **Step 7: Full gates**

Run sequentially (never in parallel), judging pytest by exit code:
1. `uv run pytest -q` → exit 0 (raise the Bash timeout to 600000 ms; the suite runs >10 min on this machine)
2. `uv run mypy src` → "Success: no issues found"
3. `uv run ruff check src tests` → "All checks passed!"

- [ ] **Step 8: Tick 20.E in the todo**

In `tasks/todo.md` (repo root), section `### 20.E — Bekannt, belegt, noch offen` (~line 466): change the section header's ``[ ]`` to ``[x]`` and replace the first list item

```markdown
- [ ] `hook_score` belohnt Bewegung → gehaltene Screens bekommen kurze Fenster (Reel-Logik;
      mildert sich durch Kapazitäts-Budget, Fenster sind nur noch Gewichte)
```

with:

```markdown
- [x] `hook_score` belohnt Bewegung → Fenster-Rubrik im Review-Prompt + Cutlist-Gewichte von
      Fensterlängen entkoppelt (Fenster = Startmarke, nicht Gewicht); Baseline 88 Reviews
      (static: Hook 5,0, 8× Sub-Sekunden-Fenster vs. moving: 6,5, 5×) via
      scripts/measure_window_bias.py — Spec 2026-07-20-window-bias-design.md
```

- [ ] **Step 9: Commit**

```bash
git add services/local-api/scripts/measure_window_bias.py services/local-api/tests/test_measure_window_bias.py tasks/todo.md
git commit -m "feat(short-creator): window-bias report pins the baseline; 20.E closed

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
