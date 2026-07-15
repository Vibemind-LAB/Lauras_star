# Multi-Window Scene Reviews Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A scene review can carry 1–4 strong windows (each with its own optional ROI), the storyline can reference a specific window per scene entry, and `build_cutlist` cuts the referenced window — so long scenes contribute several segments and a 180s target becomes reachable.

**Architecture:** Extend the board schema (`SceneReview.windows`, `BestWindow.roi`, `SceneWindowRef` storyline entries) backwards-compatibly via pydantic defaults + normalizing validators; extend the VLM review prompt/parser to 1–4 non-overlapping windows; resolve `(scene, window)` refs in `save_storyline` (validation) and `build_cutlist` (cutting). Builds ON TOP of 13da9d3 (chapter audio-window coupling) — the audio-window scaling machinery is untouched; only the per-segment source window/cap changes.

**Tech Stack:** Python 3.11, pydantic v2, pytest, mypy (strict), ruff. All inside `services/local-api`.

## Global Constraints

- Timeline edits in integer frames, ranges end-exclusive; windows are float seconds *relative to the scene* (a projection, clamped inside the scene) — the frame invariant is enforced where frames are produced (`CutSegment`).
- Old review JSONs (no `windows` field, no `roi` inside `best_window`) MUST keep validating (pydantic defaults).
- `best_window` stays required and MUST equal `windows[0]`.
- Same scene with different windows in a storyline: allowed. Same `(scene, window)` pair twice: `ValidationError` with agent-correctable `"loc: msg"` text (existing `_validation_errors` pattern).
- Do not touch `services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py` (Codex territory). No `print`. Gates: `uv run pytest`, `uv run mypy` (src+tests), `uv run ruff check`/`format --check`. Conventional commits, explicit `git add` paths.

## Documented design decisions

1. **ROI per window lives ON `BestWindow` (`roi: Roi | None = None`)**, not as a list-aligned `rois` field on `SceneReview`. A window and "where to look during it" are one atomic judgment; parallel lists can desync on merge/discard. `SceneReview.roi` stays as the scene-level ROI (old reviews keep working; it is also the fallback when a chosen window has no own ROI).
2. **Overlapping VLM windows are DISCARDED (strongest-first greedy), not merged.** Windows arrive strongest-first; merging two moments would blend two distinct beats and make their per-window ROIs ill-defined. Touching windows (end == next start, end-exclusive analog) are fine.
3. **Storyline window notation is the dict form `{"scene": 13, "window": 2}`** (pydantic model `SceneWindowRef`, `window` 0-based, default 0), not a `"13#2"` string — natural JSON for the agent, no custom string parsing, self-documenting in `get_storyline` dumps. Plain `13` stays valid and means window 0.
4. **`(scene, window)` uniqueness is storyline-wide** (across chapters). NOTE: a storyline that repeats the same plain scene number (previously technically possible) now fails validation on load — boards are per-session artifacts; an affected old board needs a storyline re-save, reviews survive.
5. **`build_cutlist` keeps the 13da9d3 stretch semantics** — the chosen window's `offset_s`/`duration_s` replace `best_window`'s everywhere (base-duration cap, stretch cap `scene_end - offset`, start frame), and a segment may still stretch past its window's end (never past the scene) to fill the chapter's audio window.

---

### Task 1: Board schema — `windows`, per-window ROI, `SceneWindowRef`, duplicate validator

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board_models.py`
- Test: `services/local-api/tests/test_production_board_models.py`

**Interfaces:**
- Produces: `BestWindow.roi: Roi | None`; `SceneReview.windows: list[BestWindow]` (never empty after validation, `windows[0] == best_window`, non-overlapping); `SceneWindowRef(scene: int, window: int = 0)`; `Chapter.scene_numbers: list[int | SceneWindowRef]`; helper `as_scene_window(entry: int | SceneWindowRef) -> tuple[int, int]`.

- [ ] **Step 1: Write the failing tests** (append to `test_production_board_models.py`; add `SceneWindowRef` to the import)

```python
_OLD_REVIEW_JSON = """
{
  "scene_number": 13,
  "src_start_frame": 4980,
  "src_end_frame_exclusive": 7260,
  "description": "terminal with running agents",
  "whats_happening": "logs scroll while a job finishes",
  "hook_score": 7,
  "best_window": {"offset_s": 12.0, "duration_s": 6.0},
  "roi": {"x": 0.05, "y": 0.4, "w": 0.55, "h": 0.3},
  "legibility_notes": "small mono font",
  "degraded": false,
  "model": "OllamaDescribeBackend",
  "version": 2,
  "created_utc": "2026-07-14T18:22:31Z"
}
"""


def test_scene_review_windows_default_to_best_window() -> None:
    r = _review()
    assert r.windows == [r.best_window]
    again = SceneReview.model_validate_json(r.model_dump_json())
    assert again.windows == [again.best_window]


def test_old_review_json_without_windows_still_validates() -> None:
    r = SceneReview.model_validate_json(_OLD_REVIEW_JSON)
    assert r.windows == [BestWindow(offset_s=12.0, duration_s=6.0)]
    assert r.best_window.roi is None
    assert r.roi is not None and r.roi.w == 0.55


def test_scene_review_windows_first_must_equal_best_window() -> None:
    with pytest.raises(ValidationError):
        _review(windows=[BestWindow(offset_s=9.0, duration_s=1.0)])


def test_scene_review_windows_must_not_overlap() -> None:
    w0 = BestWindow(offset_s=1.0, duration_s=4.0)
    with pytest.raises(ValidationError):
        _review(best_window=w0, windows=[w0, BestWindow(offset_s=3.0, duration_s=2.0)])
    ok = _review(best_window=w0, windows=[w0, BestWindow(offset_s=5.0, duration_s=2.0)])
    assert len(ok.windows) == 2  # touching (end == next start) is fine, end-exclusive analog


def test_best_window_roi_optional_and_validated() -> None:
    w = BestWindow(offset_s=0.0, duration_s=2.0, roi=Roi(x=0.1, y=0.1, w=0.3, h=0.3))
    assert w.roi is not None
    with pytest.raises(ValidationError):
        BestWindow(offset_s=0.0, duration_s=2.0, roi=Roi(x=0.8, y=0.0, w=0.5, h=0.5))


def test_chapter_scene_numbers_accept_window_refs() -> None:
    c = Chapter(
        chapter=1,
        role="hook",
        message="m",
        scene_numbers=[1, {"scene": 1, "window": 1}],  # type: ignore[list-item]
        target_seconds=3.0,
    )
    assert c.scene_numbers[0] == 1
    assert c.scene_numbers[1] == SceneWindowRef(scene=1, window=1)
    assert as_scene_window(c.scene_numbers[0]) == (1, 0)
    assert as_scene_window(c.scene_numbers[1]) == (1, 1)


def test_storyline_rejects_duplicate_scene_window_pairs() -> None:
    def chapter(n: int, scenes: list[object]) -> Chapter:
        return Chapter(
            chapter=n,
            role="hook" if n == 1 else "payoff_cta",
            message="m",
            scene_numbers=scenes,  # type: ignore[arg-type]
            target_seconds=3.0,
        )

    with pytest.raises(ValidationError) as exc:
        Storyline(red_thread="rt", arc=[chapter(1, [1, {"scene": 1, "window": 0}])])
    assert "scene 1 window 0" in str(exc.value)

    with pytest.raises(ValidationError):
        Storyline(red_thread="rt", arc=[chapter(1, [2]), chapter(2, [2])])

    ok = Storyline(
        red_thread="rt", arc=[chapter(1, [1, {"scene": 1, "window": 1}]), chapter(2, [2])]
    )
    assert len(ok.arc) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_production_board_models.py -v` (cwd `services/local-api`)
Expected: FAIL — `ImportError: cannot import name 'SceneWindowRef'`.

- [ ] **Step 3: Implement in `board_models.py`**

`BestWindow` gets `roi`; `SceneReview` gets `windows` + validator; new `SceneWindowRef` + `as_scene_window`; `Chapter.scene_numbers` union; `Storyline` duplicate validator (`field_validator("arc")` so the error loc is `arc`).

```python
class BestWindow(BaseModel):
    """One strong moment inside a scene, relative to the scene start.

    ``roi`` is this window's own region of interest; ``None`` defers to the review's
    scene-level ``roi`` (which is also what every pre-windows review JSON has).
    """

    model_config = ConfigDict(extra="forbid")

    offset_s: float = Field(ge=0.0)
    duration_s: float = Field(gt=0.0)
    roi: Roi | None = None


class SceneReview(BaseModel):
    scene_number: int = Field(ge=1)
    ...  # existing fields unchanged
    best_window: BestWindow
    windows: list[BestWindow] = Field(default_factory=list)
    ...

    @model_validator(mode="after")
    def _windows_consistent(self) -> SceneReview:
        """Normalize + guard the windows list: old JSONs (no ``windows``) get
        ``[best_window]``; a provided list must lead with ``best_window`` and its
        windows must not overlap (touching is fine — end-exclusive analog)."""
        if not self.windows:
            self.windows = [self.best_window]
        if self.windows[0] != self.best_window:
            raise ValueError("windows[0] must equal best_window")
        spans = sorted((w.offset_s, w.offset_s + w.duration_s) for w in self.windows)
        for (_s1, e1), (s2, _e2) in zip(spans, spans[1:], strict=False):
            if s2 < e1 - 1e-9:
                raise ValueError("windows must not overlap")
        return self


class SceneWindowRef(BaseModel):
    """Storyline reference to one review window of a scene (0-based; 0 = best_window)."""

    model_config = ConfigDict(extra="forbid")

    scene: int = Field(ge=1)
    window: int = Field(default=0, ge=0)


def as_scene_window(entry: int | SceneWindowRef) -> tuple[int, int]:
    """A storyline scene entry as ``(scene_number, window_index)``; ints are window 0."""
    if isinstance(entry, SceneWindowRef):
        return entry.scene, entry.window
    return entry, 0


class Chapter(BaseModel):
    ...
    scene_numbers: list[int | SceneWindowRef] = Field(min_length=1)
    ...


class Storyline(BaseModel):
    ...

    @field_validator("arc")
    @classmethod
    def _no_duplicate_scene_windows(cls, arc: list[Chapter]) -> list[Chapter]:
        """The same (scene, window) pair may appear only once in the whole storyline —
        reusing a scene requires a different window."""
        seen: dict[tuple[int, int], int] = {}
        for chapter in arc:
            for entry in chapter.scene_numbers:
                key = as_scene_window(entry)
                first = seen.get(key)
                if first is not None:
                    scene, window = key
                    raise ValueError(
                        f"scene {scene} window {window} is referenced more than once "
                        f"(chapters {first} and {chapter.chapter}); reuse a scene only "
                        "with a different window"
                    )
                seen[key] = chapter.chapter
        return arc
```

(Import `field_validator` from pydantic. `SceneReview.model_config` keeps `extra="forbid"`.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_production_board_models.py -v` → PASS (old tests included).

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/board_models.py services/local-api/tests/test_production_board_models.py
git commit -m "feat(short-creator): multi-window scene reviews + storyline window refs in board schema"
```

### Task 2: VLM prompt + parser — 1–4 non-overlapping windows

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (`_REVIEW_PROMPT`, `_clamp_best_window`, new `_clamp_windows`, `review_scene`, `get_reviews`)
- Test: `services/local-api/tests/test_production_tools_review.py`

**Interfaces:**
- Produces: `_clamp_windows(raw: Any, scene_duration_s: float) -> list[BestWindow]` (never empty, ≤4, non-overlapping, strongest-first preserved); `_clamp_best_window` additionally reads a per-window `roi`.
- `review_scene` result gains `"windows": <count>`; `get_reviews` entries gain `"windows": [{"window", "offset_s", "duration_s", "has_roi"}]` and `has_roi` means "review-level OR any window ROI".

- [ ] **Step 1: Write the failing tests** (append; update `test_board_status_and_get_reviews`' expected dict with the new `windows` key)

```python
_MULTI_REPLY = (
    '{"description": "agent dashboard", "whats_happening": "list scrolls", '
    '"hook_score": 8, "windows": ['
    '{"offset_s": 0.5, "duration_s": 2.0, "roi": {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4}}, '
    '{"offset_s": 3.0, "duration_s": 1.5, "roi": null}, '
    '{"offset_s": 0.0, "duration_s": 1.0}], '
    '"legibility_notes": ""}'
)


def test_review_scene_parses_multiple_windows(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=_Vlm(_MULTI_REPLY), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["review_scene"].func(scene_number=1)

    assert out["ok"] and out["degraded"] is False
    assert out["windows"] == 2  # third window overlaps the first (strongest) -> discarded
    review = board.scene_reviews()[0]
    assert [w.offset_s for w in review.windows] == [0.5, 3.0]
    assert review.best_window == review.windows[0]
    assert review.windows[0].roi is not None and review.windows[0].roi.w == 0.5
    assert review.windows[1].roi is None
    assert review.roi is None  # no scene-level roi in the reply

    reviews = specs["get_reviews"].func()
    entry = reviews["reviews"][0]
    assert entry["has_roi"] is True  # window 0 has one
    assert entry["windows"] == [
        {"window": 0, "offset_s": 0.5, "duration_s": 2.0, "has_roi": True},
        {"window": 1, "offset_s": 3.0, "duration_s": 1.5, "has_roi": False},
    ]


def test_review_scene_legacy_best_window_reply_still_parses(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    deps = ProductionDeps(describe_backend=_Vlm(_GOOD_REPLY), frame_extract=_extract_stub)
    specs = {
        s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id, deps=deps)
    }

    out = specs["review_scene"].func(scene_number=1)

    assert out["ok"] and out["windows"] == 1
    review = board.scene_reviews()[0]
    assert review.windows == [review.best_window]
    assert review.best_window.offset_s == 1.0 and review.best_window.duration_s == 3.0
    assert review.roi is not None and review.roi.w == 0.5  # top-level roi kept


def test_clamp_windows_clamps_discards_and_caps() -> None:
    # 5 proposals: [0] fine, [1] overlaps [0] -> discarded, [2] runs past the scene end ->
    # offset pulled back (still non-overlapping), [3] garbage -> skipped, [4] fine -> 4th kept.
    raw = [
        {"offset_s": 0.0, "duration_s": 2.0},
        {"offset_s": 1.0, "duration_s": 1.0},
        {"offset_s": 4.5, "duration_s": 1.0, "roi": {"x": 0.1, "y": 0.1, "w": 0.2, "h": 0.2}},
        "garbage",
        {"offset_s": 2.5, "duration_s": 1.0},
    ]
    windows = _clamp_windows(raw, 5.0)
    assert [(w.offset_s, w.duration_s) for w in windows] == [(0.0, 2.0), (4.0, 1.0), (2.5, 1.0)]
    assert windows[1].roi is not None

    assert _clamp_windows(None, 5.0) == [BestWindow(offset_s=0.0, duration_s=4.0)]
    assert _clamp_windows([], 5.0) == [BestWindow(offset_s=0.0, duration_s=4.0)]

    five = [{"offset_s": float(i * 2), "duration_s": 1.0} for i in range(5)]
    assert len(_clamp_windows(five, 20.0)) == 4  # hard cap
```

(Import `_clamp_windows` and `BestWindow` in the test module. Extend the existing degraded-path test with `assert review.windows == [review.best_window]`.)

- [ ] **Step 2: Run to verify failure** — `uv run pytest tests/test_production_tools_review.py -v` → ImportError `_clamp_windows`.

- [ ] **Step 3: Implement**

New prompt (replaces the `best_window`/`roi` lines):

```python
_REVIEW_PROMPT = (
    "You are reviewing {n} frames (start/middle/end) of scene {scene} from a screen "
    "recording ({duration_s:.1f}s). Transcript of the scene: \"{snippet}\".\n"
    "Reply ONLY with a JSON object, no prose, no code fences:\n"
    "{{\"description\": str (what is on screen),\n"
    "  \"whats_happening\": str (what changes across the frames),\n"
    "  \"hook_score\": int 0-10 (how visually gripping for a cold viewer),\n"
    "  \"windows\": [{{\"offset_s\": float, \"duration_s\": float, \"roi\": {{\"x\": float, "
    "\"y\": float, \"w\": float, \"h\": float}} | null}}] (1-4 strong moments, STRONGEST "
    "FIRST, non-overlapping, offsets relative to scene start; a long scene with several "
    "distinct beats should list each beat as its own window; roi is the normalized 0-1 box "
    "a viewer must read DURING that window, null if the whole frame matters),\n"
    "  \"legibility_notes\": str}}"
)
```

`_clamp_best_window` reads the per-window roi (signature unchanged):

```python
def _clamp_best_window(raw: Any, scene_duration_s: float) -> BestWindow:
    """Clamp a proposed window inside ``[0, scene_duration_s]`` (+ its own optional roi).

    Length is preserved where possible — only the offset gives way when the requested window
    would run past the end of the scene — and length itself is capped to the scene's own
    duration, with a small floor so the window is always positive (a pydantic requirement).
    """
    offset_raw, length_raw, roi = 0.0, min(_DEFAULT_WINDOW_S, scene_duration_s), None
    if isinstance(raw, dict):
        offset_raw = _as_float(raw.get("offset_s"), offset_raw)
        length_raw = _as_float(raw.get("duration_s"), length_raw)
        roi = _clamp_roi(raw.get("roi"))
    length = max(_MIN_WINDOW_S, min(length_raw, scene_duration_s))
    max_offset = max(0.0, scene_duration_s - length)
    offset = max(0.0, min(offset_raw, max_offset))
    return BestWindow(offset_s=offset, duration_s=length, roi=roi)


_MAX_WINDOWS = 4


def _clamp_windows(raw: Any, scene_duration_s: float) -> list[BestWindow]:
    """1-{_MAX_WINDOWS} clamped, non-overlapping windows out of a VLM reply, strongest-first.

    Each dict item is clamped like :func:`_clamp_best_window`; an item that (after clamping)
    overlaps an earlier, stronger accepted window is DISCARDED — not merged — so every kept
    window stays one distinct moment with its own roi (touching windows are fine). Anything
    unusable degrades to the single default window, mirroring ``_clamp_best_window(None)``.
    """
    items = raw if isinstance(raw, list) else [raw]
    accepted: list[BestWindow] = []
    for item in items:
        if len(accepted) == _MAX_WINDOWS:
            break
        if not isinstance(item, dict):
            continue
        window = _clamp_best_window(item, scene_duration_s)
        end = window.offset_s + window.duration_s
        if any(
            window.offset_s < w.offset_s + w.duration_s - 1e-9 and w.offset_s < end - 1e-9
            for w in accepted
        ):
            continue
        accepted.append(window)
    if not accepted:
        return [_clamp_best_window(None, scene_duration_s)]
    return accepted
```

`review_scene`'s parsed branch (legacy replies fall back to the old single `best_window` key):

```python
            else:
                raw_windows = parsed.get("windows")
                if raw_windows is None:  # legacy single-window reply shape
                    raw_windows = [parsed.get("best_window")]
                windows = _clamp_windows(raw_windows, duration_s)
                review = SceneReview(
                    scene_number=scene_number,
                    src_start_frame=src_start,
                    src_end_frame_exclusive=src_end_exclusive,
                    description=str(parsed.get("description") or ""),
                    whats_happening=str(parsed.get("whats_happening") or ""),
                    hook_score=_clamp_hook_score(parsed.get("hook_score")),
                    best_window=windows[0],
                    windows=windows,
                    roi=_clamp_roi(parsed.get("roi")),
                    legibility_notes=str(parsed.get("legibility_notes") or ""),
                    degraded=False,
                    model=model_name,
                    created_utc=utcnow_iso(),
                )
```

`review_scene` return dict: add `"windows": len(review.windows),`. `review_scene` docstring: mention 1–4 windows. `get_reviews` entry becomes:

```python
                    {
                        "scene_number": r.scene_number,
                        "hook_score": r.hook_score,
                        "degraded": r.degraded,
                        "has_roi": r.roi is not None
                        or any(w.roi is not None for w in r.windows),
                        "windows": [
                            {
                                "window": i,
                                "offset_s": w.offset_s,
                                "duration_s": w.duration_s,
                                "has_roi": w.roi is not None,
                            }
                            for i, w in enumerate(r.windows)
                        ],
                        "description": r.description[:_DESCRIPTION_PREVIEW_CHARS],
                    }
```

- [ ] **Step 4: Run** `uv run pytest tests/test_production_tools_review.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_review.py
git commit -m "feat(short-creator): VLM scene review yields 1-4 non-overlapping windows"
```

### Task 3: `save_storyline` — validate window refs (validate-first, range check)

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (`save_storyline`, import `as_scene_window`)
- Test: `services/local-api/tests/test_production_tools_write.py`

- [ ] **Step 1: Failing tests** (the `_review` helper gains an optional `windows` param writing 2 windows)

```python
def test_save_storyline_accepts_window_refs(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1, n_windows=2)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_storyline"].func(
        red_thread="rt",
        chapters=[_chapter(scene_numbers=[1, {"scene": 1, "window": 1}])],
    )

    assert out == {"ok": True, "version": 1}
    got = specs["get_storyline"].func()
    assert got["storyline"]["arc"][0]["scene_numbers"] == [1, {"scene": 1, "window": 1}]


def test_save_storyline_rejects_out_of_range_window(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)  # single window
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_storyline"].func(
        red_thread="rt", chapters=[_chapter(scene_numbers=[{"scene": 1, "window": 3}])]
    )

    assert out["ok"] is False
    assert "window 3" in out["reason"] and "scene 1" in out["reason"]
    assert board.load("storyline") is None


def test_save_storyline_rejects_duplicate_scene_window(tmp_path: Path) -> None:
    db, asset_id = _seed_scene(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["save_storyline"].func(
        red_thread="rt", chapters=[_chapter(scene_numbers=[1, 1])]
    )

    assert out["ok"] is False
    assert any("scene 1 window 0" in err for err in out["errors"])
    assert any(err.startswith("arc") for err in out["errors"])
    assert board.load("storyline") is None
```

`_chapter`'s `scene_numbers` param type widens to `list[object] | None`; `_review` helper:

```python
def _review(board: Board, scene_number: int, *, n_windows: int = 1) -> None:
    windows = [
        BestWindow(offset_s=float(i * 2), duration_s=1.0) for i in range(n_windows)
    ]
    board.save_scene_review(
        SceneReview(
            scene_number=scene_number,
            src_start_frame=0,
            src_end_frame_exclusive=SCENE_FRAMES,
            description="d",
            whats_happening="h",
            hook_score=5,
            best_window=windows[0],
            windows=windows,
        )
    )
```

- [ ] **Step 2: Run** → FAIL (dict entries break the current `int(n)` precheck / no range check).

- [ ] **Step 3: Implement** — restructure `save_storyline`: pydantic-validate FIRST (malformed input → `errors`, including the new duplicate validator), THEN review-existence + window-range checks on the typed model:

```python
    def save_storyline(red_thread: str, chapters: list[dict[str, Any]]) -> dict[str, Any]:
        """Validate and save the short's storyline (red thread + chapter arc) to the board.
        A scene_numbers entry is a plain scene number (= that review's primary window 0) or
        {"scene": N, "window": K} to play review window K (0-based, see get_reviews); the
        same scene may appear several times with DIFFERENT windows, the same (scene, window)
        pair only once. Every referenced scene needs a review on the board and every
        referenced window must exist in that review — rejected with the exact scenes/refs to
        fix. A malformed chapter is rejected with field-level validation errors instead of
        raising."""
        try:
            try:
                storyline = Storyline(red_thread=red_thread, arc=[Chapter(**c) for c in chapters])
            except ValidationError as exc:
                return {"ok": False, "errors": _validation_errors(exc)}
            refs = [
                as_scene_window(entry)
                for chapter in storyline.arc
                for entry in chapter.scene_numbers
            ]
            reviews = {r.scene_number: r for r in board.scene_reviews()}
            missing = sorted({scene for scene, _window in refs if scene not in reviews})
            if missing:
                return {"ok": False, "reason": f"scenes without review: {missing}"}
            bad_refs = sorted(
                {(s, w) for s, w in refs if w >= len(reviews[s].windows)}
            )
            if bad_refs:
                detail = "; ".join(
                    f"scene {s} has {len(reviews[s].windows)} windows "
                    f"(0..{len(reviews[s].windows) - 1}) but window {w} is referenced"
                    for s, w in bad_refs
                )
                return {"ok": False, "reason": detail}
            version = board.save("storyline", storyline)
            return {"ok": True, "version": version}
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}
```

- [ ] **Step 4: Run** `uv run pytest tests/test_production_tools_write.py -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_write.py
git commit -m "feat(short-creator): save_storyline validates (scene, window) refs"
```

### Task 4: `build_cutlist` cuts the referenced window

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (`build_cutlist`, `_segment_duration_s`, `_lines_in_storyline_order`, module docstring)
- Test: `services/local-api/tests/test_production_tools_cutlist.py`

- [ ] **Step 1: Failing tests** (the `_review` helper gains `windows: list[BestWindow] | None = None`)

```python
def test_build_cutlist_uses_referenced_window(tmp_path: Path) -> None:
    """Storyline references window 1 of scene 1: the segment starts at THAT window's offset,
    its stretch cap runs from that offset to the scene end, and the segment's roi is the
    window's own roi (falling back to the review-level roi only when the window has none)."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    w0 = BestWindow(offset_s=0.0, duration_s=2.0)
    w1 = BestWindow(offset_s=5.0, duration_s=3.0, roi=Roi(x=0.3, y=0.3, w=0.2, h=0.2))
    _review(board, 1, best_window=w0, windows=[w0, w1], roi=Roi(x=0.1, y=0.1, w=0.2, h=0.2))
    board.save(
        "storyline",
        Storyline(
            red_thread="rt",
            arc=[
                Chapter(
                    chapter=1,
                    role="hook",
                    message="m",
                    scene_numbers=[SceneWindowRef(scene=1, window=1)],
                    target_seconds=4.0,
                )
            ],
        ),
    )
    board.save(
        "script",
        Script(language="de", lines=[ScriptLine(chapter=1, scene_number=1, text="Stopp dein Team")]),
    )
    # chapter audio window [0, 9.5): longer than the 5s the scene can host from offset 5.0 ->
    # the segment stretches from the WINDOW's offset to the scene end (frames 150..300).
    _save_voice(
        board,
        tmp_path,
        words=[
            {"text": "Stopp", "start_s": 0.2, "end_s": 0.5},
            {"text": "dein", "start_s": 0.6, "end_s": 1.0},
            {"text": "Team", "start_s": 1.2, "end_s": 8.9},
        ],
    )
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is True
    cutlist = board.load("cutlist")
    assert isinstance(cutlist, Cutlist)
    seg = cutlist.segments[0]
    assert (seg.start_frame, seg.end_frame_exclusive) == (150, 300)
    assert seg.roi is not None and seg.roi.x == 0.3  # window roi wins over review roi


def test_build_cutlist_same_scene_twice_with_different_windows(tmp_path: Path) -> None:
    """One chapter plays scene 1 twice — window 0 then window 1 — as two separate segments."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    w0 = BestWindow(offset_s=1.0, duration_s=2.0)
    w1 = BestWindow(offset_s=6.0, duration_s=2.0)
    _review(board, 1, best_window=w0, windows=[w0, w1])
    board.save(
        "storyline",
        Storyline(
            red_thread="rt",
            arc=[
                Chapter(
                    chapter=1,
                    role="hook",
                    message="m",
                    scene_numbers=[1, SceneWindowRef(scene=1, window=1)],
                    target_seconds=4.0,
                )
            ],
        ),
    )
    board.save(
        "script",
        Script(language="de", lines=[ScriptLine(chapter=1, scene_number=1, text="Stopp dein Team")]),
    )
    _save_voice(board, tmp_path, words=[])  # no sidecar -> plain target_seconds budget
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    # 4.0s/2 refs = 2.0s each -> 60 frames from each window's own offset.
    assert out == {"ok": True, "segments": 2, "total_seconds": 4.0, "with_zoom": 0}
    cutlist = board.load("cutlist")
    assert isinstance(cutlist, Cutlist)
    seg0, seg1 = cutlist.segments
    assert (seg0.scene_number, seg0.start_frame, seg0.end_frame_exclusive) == (1, 30, 90)
    assert (seg1.scene_number, seg1.start_frame, seg1.end_frame_exclusive) == (1, 180, 240)


def test_build_cutlist_rejects_out_of_range_window_ref(tmp_path: Path) -> None:
    """A storyline saved straight to the board (bypassing save_storyline's gate, e.g. written
    before a scene was re-reviewed down to fewer windows) must fail loud and correctable."""
    db, asset_id = _seed_two_scenes(tmp_path)
    board = _board(tmp_path, asset_id)
    _review(board, 1)  # single window
    board.save(
        "storyline",
        Storyline(
            red_thread="rt",
            arc=[
                Chapter(
                    chapter=1,
                    role="hook",
                    message="m",
                    scene_numbers=[SceneWindowRef(scene=1, window=2)],
                    target_seconds=4.0,
                )
            ],
        ),
    )
    board.save(
        "script",
        Script(language="de", lines=[ScriptLine(chapter=1, scene_number=1, text="Stopp dein Team")]),
    )
    _save_voice(board, tmp_path, words=[])
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=asset_id)}

    out = specs["build_cutlist"].func()

    assert out["ok"] is False
    assert "window 2" in out["reason"] and "scene 1" in out["reason"]
    assert board.load("cutlist") is None
```

(Test imports gain `SceneWindowRef`. `_review` helper signature: `def _review(board, scene_number, *, best_window=None, roi=None, windows=None)` passing `windows=windows if windows is not None else []`.)

- [ ] **Step 2: Run** → FAIL (segments cut from `best_window`, no range check).

- [ ] **Step 3: Implement** — in `build_cutlist`: rename locals `windows`→`audio_windows`, `window`→`audio_window`; resolve `(scene_number, window_idx)` via `as_scene_window`; pick `review.windows[window_idx]`; roi = window roi, else review roi; out-of-range → `{"ok": False, "reason": ...}`; unreviewed scene + `window_idx > 0` → same. `_segment_duration_s` keyword `best_window` renamed to `window`. Docstrings updated (`build_cutlist`, module header §build_cutlist paragraph, `_lines_in_storyline_order` — which now iterates `as_scene_window(entry)[0]`).

Key hunk (first pass over a chapter's refs):

```python
                for entry in chapter.scene_numbers:
                    scene_number, window_idx = as_scene_window(entry)
                    resolved = _resolve_scene(db, asset_id, scene_number)
                    if resolved is None:
                        continue
                    src_start, src_end, _text = resolved
                    scene_duration_s = (src_end - src_start) / fps

                    review = reviews_by_scene.get(scene_number)
                    if review is not None:
                        if window_idx >= len(review.windows):
                            return {
                                "ok": False,
                                "reason": (
                                    f"scene {scene_number} has {len(review.windows)} windows "
                                    f"(0..{len(review.windows) - 1}) but the storyline "
                                    f"references window {window_idx}; fix the storyline or "
                                    "re-review the scene"
                                ),
                            }
                        window = review.windows[window_idx]
                        roi = window.roi if window.roi is not None else review.roi
                    else:
                        if window_idx > 0:
                            return {
                                "ok": False,
                                "reason": (
                                    f"scene {scene_number} has no review; window "
                                    f"{window_idx} can only be cut from a reviewed scene"
                                ),
                            }
                        window = BestWindow(
                            offset_s=0.0, duration_s=min(_DEFAULT_WINDOW_S, scene_duration_s)
                        )
                        roi = None

                    resolved_scenes.append((scene_number, src_start, src_end, window, roi))
                    base_durations.append(
                        _segment_duration_s(
                            target_seconds=chapter.target_seconds,
                            n_scenes=n_scenes,
                            window=window,
                            scene_duration_s=scene_duration_s,
                        )
                    )
                    stretch_caps.append(
                        min(
                            scene_duration_s,
                            max(_SEGMENT_FLOOR_S, scene_duration_s - window.offset_s),
                        )
                    )
```

- [ ] **Step 4: Run** `uv run pytest tests/test_production_tools_cutlist.py -v` → PASS (all pre-existing coupling tests must stay green).

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_cutlist.py
git commit -m "feat(short-creator): build_cutlist cuts the storyline-referenced review window"
```

### Task 5: Teach the team + docs + full gates

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_agents.py` (story_architect + scene_author prompts)
- Modify: `docs/agentic-short-creator.md` (new German subsection under "v2")
- Test: existing suites (prompt content is data; roster tests must stay green)

- [ ] **Step 1: Prompts.** story_architect, after "…never a scene number that has no review yet.": `"A review may list several strong windows (get_reviews shows them, 0-based). A plain scene number plays window 0; {\"scene\": N, \"window\": K} plays window K — reuse the SAME scene with DIFFERENT windows to fill long targets, never the same (scene, window) pair twice. "` scene_author, after the SAME-scene-order sentence: `"A chapter may list the same scene several times with different windows — write that scene's line(s) once for the chapter. "`

- [ ] **Step 2: Docs** — `docs/agentic-short-creator.md`, new `###` block after the v2 bash block (German, ~10 lines): windows pro Review (1–4, `windows[0]` = `best_window`, ROI pro Fenster), Storyline-Notation, Duplikat-Regel, Rückwärtskompatibilität, Motivation (180s).

- [ ] **Step 3: Full gates** (cwd `services/local-api`):

```bash
uv run pytest
uv run mypy
uv run ruff check .
uv run ruff format --check .
```

Expected: all green. Fix anything that isn't.

- [ ] **Step 4: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_agents.py docs/agentic-short-creator.md docs/superpowers/plans/2026-07-15-multi-window-scene-reviews.md
git commit -m "feat(short-creator): teach team the window notation + docs"
```

### Task 6: Land on feat/generate-ui

- [ ] Fast-forward-push the session branch onto `feat/generate-ui` (13da9d3 is the merge base — already its tip): `git push origin HEAD:feat/generate-ui`. No force. If origin moved meanwhile: stop and report instead.

## Self-Review notes

- Spec coverage: (1) Task 1, (2) Task 2, (3) Tasks 1+3, (4) Task 4, (5) Tasks 1+3; tests per area incl. old-JSON fixture (Task 1) built from the livetest schema shape, no workspace access; gates Task 5.
- Old `get_reviews` exact-equality assertion updated in Task 2 (new `windows` key).
- `n_scenes` now counts window refs (unchanged variable) — budget per segment, intended.
- Renames contained: `_segment_duration_s(window=...)` (only caller is `build_cutlist`); locals `audio_windows`/`audio_window` avoid shadowing review windows.
