# Rough-Cut Visual Selection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the two-beat visual proposal with one ordered choice per current Rough-Cut scene, selectable 1–10 second durations, complete decision metadata, exact Voice-length allocation, and a verified new render path.

**Architecture:** Persist a backward-compatible Visual Plan v2 whose primary units are Rough-Cut scene choices rather than Voice beats. Keep candidate coverage and duration resolution pure, then reuse those contracts in Production Tools, the shared HTTP/Chat confirmation service, the existing hard-stop orchestrator, and one Electron card. Legacy v1 beat plans continue to load and render through their current branch.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, SQLite repositories, pytest, mypy, Ruff, TypeScript strict, React, Vitest, Electron, ffmpeg/ffprobe.

## Global Constraints

- Every current Rough-Cut scene transition appears exactly once and in Rough-Cut order.
- The default includes every scene when one second per scene fits the Voice; otherwise it includes a relevant subset of at least three scenes.
- Per-scene requested duration is an integer from 1 through 10 seconds.
- Undercoverage blocks confirmation; overcoverage trims only the final included shot, frame-exactly.
- Storyline, Script and Voice remain byte-, hash- and version-identical.
- Source and timeline ranges are integer frames and end-exclusive.
- Full-frame recuts always use `roi=None`, no zoom timing, and renderer `fit="blur"`.
- Visual Selection and Contact Sheet remain persisted terminal states for the current orchestrator run.
- Proposal and Contact-Sheet confirmations are identity-bound; stale or duplicate concurrent confirmation starts no second job.
- Legacy v1 beat plans and boards without Visual Recutt artifacts remain loadable and keep their prior resume behavior.
- AutoGen, VLM and heavy video extras remain optional; no new default dependency.
- No `print`, `console.log`, secret value, workspace database, generated PNG, MP3 or video enters Git.
- The live cloud model is exactly `gpt-5.6-luna`; no silent model substitution.

---

### Task 1: Persist Visual Plan v2 and Rough-Cut gate state

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board_models.py:419-510,560-620`
- Modify: `services/local-api/src/laura/short_creator/board.py:105-160,600-775`
- Modify: `services/local-api/tests/test_board_visual_recut.py`
- Modify: `services/local-api/tests/test_production_board_models.py`

**Interfaces:**
- Consumes: existing `VisualRecutRequest`, v1 `VisualShotCandidate`, `VisualBeatPlan`, `VisualPlan`, `ContactSheetTile` and Board artifact DAG.
- Produces: `VisualSceneCandidate`, `VisualSceneSelection`, `VisualSceneChoice`; v2-compatible `VisualPlan.scene_choices`; status fields `scene_choices`, `voice_total_frames`, `fps`, while retaining v1 `beats`.

- [ ] **Step 1: Write model and legacy-load RED tests**

```python
def test_v2_plan_has_one_choice_per_rough_cut_order() -> None:
    plan = v2_plan(scene_orders=[0, 1, 2, 3])
    assert [choice.rough_cut_order for choice in plan.scene_choices] == [0, 1, 2, 3]

    with pytest.raises(ValidationError, match="duplicate rough_cut_order"):
        v2_plan(scene_orders=[0, 1, 1])


def test_v1_beat_plan_still_loads_without_scene_choices() -> None:
    payload = legacy_visual_plan_payload()
    plan = VisualPlan.model_validate(payload)
    assert plan.version == 1
    assert len(plan.beats) == 2
    assert plan.scene_choices == []
```

Also pin: candidate Source ranges are end-exclusive; `max_duration_s` is 1–10; scene selections require `requested_duration_s` 1–10; v2 plans require non-empty `scene_choices` and empty `beats`; v1 plans require non-empty `beats`; a confirmed v2 plan has one selected candidate/duration decision for every scene row.

- [ ] **Step 2: Run RED**

```bash
cd services/local-api
uv run pytest tests/test_board_visual_recut.py tests/test_production_board_models.py -q
```

Expected: import/field failures for the v2 classes and `scene_choices`.

- [ ] **Step 3: Add the v2 contracts without weakening v1**

```python
class VisualSceneCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    rough_cut_order: int = Field(ge=0)
    scene_number: int = Field(ge=1)
    window_index: int = Field(ge=0)
    src_start_frame: int = Field(ge=0)
    src_end_frame_exclusive: int
    thumb_frame: int = Field(ge=0)
    max_duration_s: int = Field(ge=1, le=10)
    description: str
    transcript_snippet: str
    rationale: str
    score: float


class VisualSceneSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rough_cut_order: int = Field(ge=0)
    candidate_id: str
    included: bool
    requested_duration_s: int = Field(ge=1, le=10)


class VisualSceneChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    rough_cut_order: int = Field(ge=0)
    scene_number: int = Field(ge=1)
    description: str
    transcript: str
    rationale: str
    candidates: list[VisualSceneCandidate] = Field(min_length=1, max_length=4)
    recommended_candidate_id: str
    recommended_included: bool
    recommended_duration_s: int = Field(ge=1, le=10)
    selected_candidate_id: str | None = None
    included: bool | None = None
    requested_duration_s: int | None = Field(default=None, ge=1, le=10)
```

Extend `VisualPlan` with `scene_choices: list[VisualSceneChoice] = Field(default_factory=list)` and `selection_hash: str | None = None`. Its validator accepts exactly one representation: v1 `beats` or v2 `scene_choices`.

- [ ] **Step 4: Expose both representations safely from Board status**

```python
visual_selection_gate = {
    "enabled": isinstance(visual_request, VisualRecutRequest),
    "approved": visual_approved,
    "pending": isinstance(visual_request, VisualRecutRequest) and not visual_approved,
    "proposal_id": visual_plan.proposal_hash if isinstance(visual_plan, VisualPlan) else None,
    "beats": [beat.model_dump() for beat in visual_plan.beats]
    if isinstance(visual_plan, VisualPlan)
    else [],
    "scene_choices": [choice.model_dump() for choice in visual_plan.scene_choices]
    if isinstance(visual_plan, VisualPlan)
    else [],
    "voice_total_frames": visual_plan.voice_total_frames
    if isinstance(visual_plan, VisualPlan)
    else None,
    "fps": visual_plan.fps if isinstance(visual_plan, VisualPlan) else None,
}
```

Add `voice_total_frames: int | None` and `fps: float | None` to `VisualPlan`; require both for v2 and permit `None` for v1. Keep the existing artifact chain and resume points unchanged.

- [ ] **Step 5: Verify Task 1**

```bash
uv run pytest tests/test_board_visual_recut.py tests/test_production_board.py tests/test_production_board_models.py -q
uv run mypy src/laura/short_creator/board_models.py src/laura/short_creator/board.py tests/test_board_visual_recut.py
uv run ruff check src/laura/short_creator/board_models.py src/laura/short_creator/board.py tests/test_board_visual_recut.py
git diff --check
```

- [ ] **Step 6: Commit Task 1**

```bash
git add services/local-api/src/laura/short_creator/board_models.py services/local-api/src/laura/short_creator/board.py services/local-api/tests/test_board_visual_recut.py services/local-api/tests/test_production_board_models.py
git commit -m "feat(shorts): persist rough-cut visual choices"
```

---

### Task 2: Generate complete Rough-Cut coverage and resolve durations

**Files:**
- Modify: `services/local-api/src/laura/short_creator/visual_candidates.py`
- Modify: `services/local-api/src/laura/short_creator/voice_concat.py`
- Create: `services/local-api/src/laura/short_creator/visual_timeline.py`
- Modify: `services/local-api/tests/test_visual_candidates.py`
- Create: `services/local-api/tests/test_visual_timeline.py`

**Interfaces:**
- Consumes: Task 1 `VisualSceneCandidate`, `VisualSceneChoice`, `VisualSceneSelection`, `VisualPlan`; existing `SceneMaterial`, `TranscriptSpan`, `VisualRecutRequest`, `VoiceArtifact`.
- Produces: public `LAST_SEGMENT_CUSHION_S = 0.3`; `build_rough_cut_visual_plan(*, request: VisualRecutRequest, scenes: list[SceneMaterial], narration_text: str, voice_total_frames: int, fps: float) -> VisualPlan`; `voice_total_frames(voice: VoiceArtifact, fps: float) -> int`; `apply_scene_selections(plan: VisualPlan, selections: list[VisualSceneSelection], confirmed_utc: str) -> VisualPlan`; `resolve_selected_shots(plan: VisualPlan) -> tuple[ResolvedVisualShot, ...]`.

- [ ] **Step 1: Write complete-coverage RED tests**

```python
def test_every_rough_cut_scene_appears_once_in_order() -> None:
    plan = build_rough_cut_visual_plan(
        request=request(), scenes=scenes(8), narration_text="organize files and draft mail",
        voice_total_frames=1350, fps=30.0,
    )
    assert [choice.rough_cut_order for choice in plan.scene_choices] == list(range(8))
    assert [choice.scene_number for choice in plan.scene_choices] == list(range(1, 9))


def test_long_degraded_scene_offers_distributed_windows() -> None:
    plan = build_rough_cut_visual_plan(
        request=request(), scenes=[degraded_scene(end_exclusive=3533)],
        narration_text="Rowboat UI", voice_total_frames=300, fps=30.0,
    )
    starts = [candidate.src_start_frame for candidate in plan.scene_choices[0].candidates]
    assert len(starts) == 4
    assert starts[0] == 0
    assert any(start >= 1766 for start in starts)
```

Also assert every candidate carries non-empty description/rationale, transcript text when available, and `max_duration_s == min(10, floor(window_frames / fps))`.
When neither Review nor transcript provides a description, pin the deterministic fallback
`"Rough-Cut scene {scene_number}"`; the UI must never receive an empty decision label.

- [ ] **Step 2: Write recommendation and duration RED tests**

```python
def test_default_includes_all_scenes_when_one_second_each_fits() -> None:
    plan = plan_for_duration(scene_count=8, voice_frames=1350, fps=30.0)
    assert all(choice.recommended_included for choice in plan.scene_choices)
    assert sum(choice.recommended_duration_s for choice in plan.scene_choices) >= 45


def test_overcoverage_trims_only_final_included_shot() -> None:
    plan = confirmed_plan(durations=[10, 10, 10, 10, 10], voice_frames=1350, fps=30.0)
    shots = resolve_selected_shots(plan)
    assert [shot.final_frames for shot in shots] == [300, 300, 300, 300, 150]


def test_undercoverage_is_rejected() -> None:
    plan = pending_plan(scene_count=4, voice_frames=1350, fps=30.0)
    with pytest.raises(VisualSelectionError, match="does not cover the Voice"):
        apply_scene_selections(plan, selections(durations=[10, 10, 10, 10]), "2026-08-09T10:00:00Z")
```

Pin fewer-than-three included scenes, duplicate/missing Rough-Cut rows, candidate/row mismatch, duration above candidate capacity, and a last-shot trim below one second.

- [ ] **Step 3: Run RED**

```bash
uv run pytest tests/test_visual_candidates.py tests/test_visual_timeline.py -q
```

Expected: missing v2 builder and timeline module.

- [ ] **Step 4: Implement scene-first candidate construction**

```python
def build_rough_cut_visual_plan(
    *, request: VisualRecutRequest, scenes: list[SceneMaterial], narration_text: str,
    voice_total_frames: int, fps: float,
) -> VisualPlan:
    choices = [
        _scene_choice(
            rough_cut_order=order, scene=scene, narration_text=narration_text,
            fps=fps,
        )
        for order, scene in enumerate(scenes)
    ]
    recommended = _recommend_scene_coverage(
        choices, voice_total_frames=voice_total_frames, fps=fps,
    )
    return VisualPlan(
        version=2, proposal_hash=_proposal_hash(request, recommended),
        request_hash=_request_hash(request), beats=[], scene_choices=recommended,
        voice_total_frames=voice_total_frames, fps=fps,
    )
```

Generate up to four candidates per scene from healthy review windows or distributed anchors `(0.0, 0.33, 0.67, 1.0)`. Transcript overlap ranks candidates inside a scene but never removes the scene row.

- [ ] **Step 5: Implement pure duration resolution**

```python
@dataclass(frozen=True)
class ResolvedVisualShot:
    rough_cut_order: int
    scene_number: int
    candidate_id: str
    src_start_frame: int
    src_end_frame_exclusive: int
    requested_frames: int
    final_frames: int


def voice_total_frames(voice: VoiceArtifact, fps: float) -> int:
    if voice.segments is None or not voice.segments:
        raise VisualSelectionError("segmented Voice required")
    return sum(
        max(1, round((segment.duration_s + (
            LAST_SEGMENT_CUSHION_S if index == len(voice.segments) - 1
            else INTER_SCENE_GAP_S
        )) * fps))
        for index, segment in enumerate(voice.segments)
    )
```

Move the existing `0.3` value from the private Production-Tools constant into
`voice_concat.py` as `LAST_SEGMENT_CUSHION_S = 0.3`. Task 3 updates Production Tools to import
the public constant, so legacy and v2 frame totals cannot drift.

`apply_scene_selections` requires every scene row exactly once, at least three included rows, and requested capacity at least `voice_total_frames`. It sets `selected_candidate_id`, `included`, `requested_duration_s`, `confirmed_utc`, and a stable `selection_hash`. `resolve_selected_shots` preserves Rough-Cut order and reduces only the last included shot by the exact excess frame count.

- [ ] **Step 6: Verify Task 2**

```bash
uv run pytest tests/test_visual_candidates.py tests/test_visual_timeline.py -q
uv run mypy src/laura/short_creator/visual_candidates.py src/laura/short_creator/visual_timeline.py src/laura/short_creator/voice_concat.py tests/test_visual_timeline.py
uv run ruff check src/laura/short_creator/visual_candidates.py src/laura/short_creator/visual_timeline.py src/laura/short_creator/voice_concat.py tests/test_visual_candidates.py tests/test_visual_timeline.py
git diff --check
```

- [ ] **Step 7: Commit Task 2**

```bash
git add services/local-api/src/laura/short_creator/visual_candidates.py services/local-api/src/laura/short_creator/visual_timeline.py services/local-api/src/laura/short_creator/voice_concat.py services/local-api/tests/test_visual_candidates.py services/local-api/tests/test_visual_timeline.py
git commit -m "feat(shorts): cover every rough-cut scene"
```

---

### Task 3: Build v2 Cutlists, Contact Sheets and fresh downstream state

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board_models.py:513-560`
- Modify: `services/local-api/src/laura/short_creator/production_tools.py:695-742,2611-2705,2791-2919,3180-3225`
- Modify: `services/local-api/tests/test_production_tools_visual_recut.py`
- Modify: `services/local-api/tests/test_production_tools_cutlist.py`
- Modify: `services/local-api/tests/test_production_tools_contact_sheet.py`

**Interfaces:**
- Consumes: Task 2 `build_rough_cut_visual_plan`, `voice_total_frames`, `resolve_selected_shots`.
- Produces: v2 `start_visual_recut` output with `scene_choices`; v2 Cutlist branch; enriched Contact-Sheet tiles; explicit downstream invalidation on a new or newly confirmed Visual revision.

- [ ] **Step 1: Write production RED tests**

```python
def test_start_visual_recut_proposes_every_rough_cut_scene(finished_board: Board) -> None:
    before = versions_and_hashes(finished_board, "storyline", "script", "voice")
    result = tool(finished_board, "start_visual_recut")(
        user_request="all Rough-Cut scenes, keep Voice", framing_mode="full_frame_blur",
    )
    assert result["status"] == "awaiting_user_input"
    assert len(result["scene_choices"]) == rough_cut_scene_count(finished_board)
    assert versions_and_hashes(finished_board, "storyline", "script", "voice") == before


def test_v2_cutlist_uses_selected_lengths_and_exact_voice_frames(board: Board) -> None:
    confirm_v2_plan(board, durations=[10, 10, 10, 10, 10])
    assert tool(board, "build_cutlist")()["ok"] is True
    cutlist = cast(Cutlist, board.load("cutlist"))
    assert sum(s.end_frame_exclusive - s.start_frame for s in cutlist.segments) == 1350
    assert all(s.roi is None and s.zoom_start_s is None for s in cutlist.segments)
```

Add the live regression: begin with a Board that already has cutlist/contact/render/QA and `status="complete"`; save a new request and confirm a different v2 selection; assert all downstream versions are invalidated, resume point is `cutlist`, and the next deterministic tail saves a new Contact Sheet before stopping.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/test_production_tools_visual_recut.py tests/test_production_tools_cutlist.py tests/test_production_tools_contact_sheet.py -q
```

- [ ] **Step 3: Switch new requests to the v2 builder**

```python
total_frames = voice_total_frames(voice, fps)
plan = build_rough_cut_visual_plan(
    request=request,
    scenes=materials,
    narration_text=" ".join(line.text for line in ordered_lines),
    voice_total_frames=total_frames,
    fps=fps,
).model_copy(update={"parents": expected_parents})
board.save("visual_recut_request", request)
board.save("visual_plan", plan)
board.clear_contact_sheet_approval(enable_gate=True)
```

Return `scene_choices` in new tool responses. Keep returning `beats` for an idempotent pending v1 proposal.
Replace Production Tools' private `_LAST_SEGMENT_CUSHION_S` with the public
`voice_concat.LAST_SEGMENT_CUSHION_S`; legacy timing tests must remain byte-for-byte equivalent.

- [ ] **Step 4: Add a separate v2 Cutlist branch**

```python
if visual_plan.scene_choices:
    resolved = resolve_selected_shots(visual_plan)
    visual_segments = [
        CutSegment(
            order=order,
            scene_number=shot.scene_number,
            start_frame=shot.src_start_frame,
            end_frame_exclusive=shot.src_start_frame + shot.final_frames,
            roi=None,
            zoom_start_s=None,
        )
        for order, shot in enumerate(resolved)
    ]
else:
    visual_segments = _legacy_beat_visual_segments(visual_plan, voice, fps)
```

If the candidate's selected start plus final frames exceeds its range, pull the start backward within the candidate range; never cross the candidate's end-exclusive source bound. Stamp parents `script`, `voice`, and content hash of the confirmed v2 plan.

- [ ] **Step 5: Enrich Contact-Sheet metadata**

Extend `ContactSheetTile` with optional `rough_cut_order`, `description`, `requested_duration_s`, and `final_duration_frames`. Populate them for v2 plans; preserve legacy serialization for non-visual and v1 paths.

- [ ] **Step 6: Verify Task 3**

```bash
uv run pytest tests/test_production_tools_visual_recut.py tests/test_production_tools_cutlist.py tests/test_production_tools_contact_sheet.py tests/test_production_tools_render.py -q
uv run mypy src/laura/short_creator/production_tools.py src/laura/short_creator/board_models.py
uv run ruff check src/laura/short_creator/production_tools.py src/laura/short_creator/board_models.py tests/test_production_tools_visual_recut.py
git diff --check
```

- [ ] **Step 7: Commit Task 3**

```bash
git add services/local-api/src/laura/short_creator/board_models.py services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_visual_recut.py services/local-api/tests/test_production_tools_cutlist.py services/local-api/tests/test_production_tools_contact_sheet.py
git commit -m "feat(shorts): cut rough-cut scene selections"
```

---

### Task 4: Confirm scene choices through one HTTP and Chat service

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py:108-118,1057-1152,1210-1235`
- Modify: `services/local-api/src/laura/chat/router.py:240-290`
- Modify: `services/local-api/src/laura/chat/executor.py:1080-1130`
- Modify: `services/local-api/src/laura/api/chat.py:100-155`
- Modify: `services/local-api/tests/test_api_visual_recut.py`
- Modify: `services/local-api/tests/test_chat_router.py`
- Modify: `services/local-api/tests/test_chat_executor.py`
- Modify: `services/local-api/tests/test_chat_api.py`

**Interfaces:**
- Consumes: Task 1 `VisualSceneSelection`; Task 2 `apply_scene_selections`.
- Produces: backward-compatible `VisualSelectionConfirmRequest`; shared `confirm_visual_selection(db: Database, session_id: str, proposal_hash: str, *, selections: list[VisualSceneSelection] | None = None, selected_candidate_ids: list[str] | None = None) -> dict[str, Any]`; Chat `select_visuals` arguments containing all ordered scene decisions.

- [ ] **Step 1: Write v2 API RED tests**

```python
def test_confirm_v2_visual_selection_binds_candidate_inclusion_and_duration(client, session) -> None:
    payload = {
        "proposal_hash": current_proposal_hash(session),
        "selections": [
            {"rough_cut_order": i, "candidate_id": candidate_id(session, i),
             "included": True, "requested_duration_s": 5}
            for i in range(8)
        ],
    }
    response = client.post(f"/production/{session}/visual-selection:confirm", json=payload)
    assert response.status_code == 202
    plan = cast(VisualPlan, board(session).load("visual_plan"))
    assert plan.selection_hash is not None
    assert [choice.requested_duration_s for choice in plan.scene_choices] == [5] * 8
```

Pin `409` stale hash with zero jobs, missing/duplicate rows, fewer than three included rows, undercoverage, duration beyond candidate capacity, idempotent same confirmation, changed confirmation after a completed Board, and the existing parallel double-submit test.

- [ ] **Step 2: Write Chat RED tests**

```python
def test_router_uses_current_recommended_scene_selections() -> None:
    result = route("die Auswahl passt", context=visual_v2_context())
    assert result.tool == "select_visuals"
    assert result.arguments == {
        "proposal_hash": "a" * 64,
        "selections": recommended_scene_selection_payload(),
    }
```

Executor tests monkeypatch only `confirm_visual_selection` and assert the resulting action card carries the resumed `job_id`; no confirmation logic may be duplicated in Chat.

- [ ] **Step 3: Run RED**

```bash
uv run pytest tests/test_api_visual_recut.py tests/test_chat_router.py tests/test_chat_executor.py tests/test_chat_api.py -q
```

- [ ] **Step 4: Add a dual-version request contract**

```python
class VisualSelectionConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    proposal_hash: str = Field(min_length=64, max_length=64)
    selections: list[VisualSceneSelection] | None = None
    selected_candidate_ids: list[str] | None = None

    @model_validator(mode="after")
    def _exactly_one_payload_shape(self) -> Self:
        if (self.selections is None) == (self.selected_candidate_ids is None):
            raise ValueError("provide selections for v2 or selected_candidate_ids for v1")
        return self
```

The shared service branches on `plan.scene_choices`. For v2 it calls `apply_scene_selections`, saves the updated `visual_plan` inside the existing Board transaction, and enqueues one pure resume. Preserve the v1 beat confirmation branch unchanged.

- [ ] **Step 5: Update pending Chat context and tool schema**

Expose ordered recommendations containing `rough_cut_order`, candidate ID, included flag and duration only while the v2 gate is pending. Require a 64-character hash and a non-empty, strictly typed selection list. Natural Chat approval and the Electron card must call the same service.

- [ ] **Step 6: Verify Task 4**

```bash
uv run pytest tests/test_api_visual_recut.py tests/test_api_scene_selection.py tests/test_chat_router.py tests/test_chat_executor.py tests/test_chat_api.py -q
uv run mypy src/laura/api/short_creator.py src/laura/api/chat.py src/laura/chat/router.py src/laura/chat/executor.py
uv run ruff check src/laura/api/short_creator.py src/laura/api/chat.py src/laura/chat/router.py src/laura/chat/executor.py tests/test_api_visual_recut.py
git diff --check
```

- [ ] **Step 7: Commit Task 4**

```bash
git add services/local-api/src/laura/api/short_creator.py services/local-api/src/laura/api/chat.py services/local-api/src/laura/chat/router.py services/local-api/src/laura/chat/executor.py services/local-api/tests/test_api_visual_recut.py services/local-api/tests/test_chat_router.py services/local-api/tests/test_chat_executor.py services/local-api/tests/test_chat_api.py
git commit -m "feat(api): confirm rough-cut visual choices"
```

---

### Task 5: Preserve orchestrator hard-stops for v2 proposals

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_agents.py:215-245,410-440`
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py:540-595,820-930`
- Modify: `services/local-api/src/laura/short_creator/production_pipeline.py:150-215`
- Modify: `services/local-api/tests/test_production_orchestrator_visual_recut.py`
- Modify: `services/local-api/tests/test_production_agents.py`
- Modify: `services/local-api/tests/test_production_pipeline.py`

**Interfaces:**
- Consumes: v2 Visual Plan revisions and unchanged `pending_user_gate(...)`.
- Produces: identical `awaiting_user_input` result shape with `gate="visual_selection"`; agent contract describing Rough-Cut coverage and 1–10 second lengths.

- [ ] **Step 1: Write v2 hard-stop RED tests**

```python
def test_luna_follow_up_stops_after_one_v2_visual_proposal(fake_model, board) -> None:
    fake_model.enqueue(tool_call("start_visual_recut", {"user_request": "all Rough-Cut scenes"}))
    fake_model.raise_on_next_call()
    result = run_follow_up(board, fake_model)
    assert result["status"] == "awaiting_user_input"
    assert result["gate"] == "visual_selection"
    assert len(result["scene_choices"]) == expected_scene_count(board)
    assert fake_model.calls == 1
```

Also pin: a plain resume at an unconfirmed v2 plan constructs no team; contact-sheet creation returns `awaiting_user_input`; a second proposal only occurs after a distinct user request; prose claiming a selection or render without tool receipts remains `hard_fail`.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/test_production_orchestrator_visual_recut.py tests/test_production_agents.py tests/test_production_pipeline.py -q
```

- [ ] **Step 3: Update structural task and agent contracts**

The coding agent instruction must say:

```text
For a visual-only recut, call start_visual_recut exactly once. The persisted proposal must
contain every current Rough-Cut scene in Rough-Cut order, with 1-10 second recommendations.
Never call synthesize_script_voice. Stop immediately after the Visual Plan tool receipt.
```

Do not add a second gate classifier. Continue deriving terminal status and summaries from Board state, not model prose. `FunctionalTermination` remains based on a newly persisted unconfirmed `VisualPlan`, regardless of v1 or v2 representation.

- [ ] **Step 4: Verify Task 5**

```bash
uv run pytest tests/test_production_orchestrator_visual_recut.py tests/test_production_orchestrator_scene_gate.py tests/test_production_agents.py tests/test_production_pipeline.py tests/test_production_cancel.py -q
uv run mypy src/laura/short_creator/production_agents.py src/laura/short_creator/production_orchestrator.py src/laura/short_creator/production_pipeline.py
uv run ruff check src/laura/short_creator/production_agents.py src/laura/short_creator/production_orchestrator.py src/laura/short_creator/production_pipeline.py tests/test_production_orchestrator_visual_recut.py
git diff --check
```

- [ ] **Step 5: Commit Task 5**

```bash
git add services/local-api/src/laura/short_creator/production_agents.py services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/src/laura/short_creator/production_pipeline.py services/local-api/tests/test_production_orchestrator_visual_recut.py services/local-api/tests/test_production_agents.py services/local-api/tests/test_production_pipeline.py
git commit -m "fix(shorts): hard-stop rough-cut proposals"
```

---

### Task 6: Render every Rough-Cut choice in Electron

**Files:**
- Modify: `apps/desktop/src/api.ts:803-859,1081-1097`
- Modify: `apps/desktop/src/api.production.test.ts`
- Modify: `apps/desktop/src/components/chat/VisualSelectionCard.tsx`
- Modify: `apps/desktop/src/components/chat/VisualSelectionCard.test.tsx`
- Modify: `apps/desktop/src/components/chat/ActionCard.tsx:210-250`
- Modify: `apps/desktop/src/components/chat/ActionCard.test.tsx`

**Interfaces:**
- Consumes: Task 1 gate fields and Task 4 `selections` request.
- Produces: strict TS types `VisualSceneCandidate`, `VisualSceneChoice`, `VisualSceneSelection`; fixed-order scene rows; duration balance; one shared confirmation refresh.

- [ ] **Step 1: Write strict API RED tests**

```typescript
it("confirms ordered rough-cut decisions with the exact proposal hash", async () => {
  const selections: VisualSceneSelection[] = [
    { rough_cut_order: 0, candidate_id: "c0", included: true, requested_duration_s: 5 },
    { rough_cut_order: 1, candidate_id: "c1", included: true, requested_duration_s: 6 },
    { rough_cut_order: 2, candidate_id: "c2", included: true, requested_duration_s: 4 },
  ];
  await client.confirmVisualSelection("s1", "a".repeat(64), selections);
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/production/s1/visual-selection:confirm"),
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ proposal_hash: "a".repeat(64), selections }),
    }),
  );
});
```

The status parser test carries both legacy `beats` and optional v2 `scene_choices`, `voice_total_frames`, and `fps`; old payloads still narrow safely.

- [ ] **Step 2: Write scene-row and duration RED tests**

```typescript
it("shows every rough-cut scene once with decision metadata", async () => {
  renderCard(gateWithEightScenes());
  expect(screen.getAllByRole("group", { name: /Szene/ })).toHaveLength(8);
  expect(screen.getByText("Rowboat dashboard and file organizer")).toBeVisible();
  expect(screen.getByText("recognized UI: Draft an email")).toBeVisible();
  expect(screen.getByText("frames 1766–2066")).toBeVisible();
});


it("blocks undercoverage and previews the final trim", async () => {
  renderCard(gate({ voice_total_frames: 1350, fps: 30 }));
  chooseDurations([10, 10, 10, 10]);
  expect(screen.getByRole("button", { name: "Bildauswahl übernehmen" })).toBeDisabled();
  includeScene(4);
  chooseDuration(4, 10);
  expect(screen.getByText("Letzte Szene final: 5,0 s")).toBeVisible();
});
```

Pin use/skip, minimum three included rows, 1–10 second buttons bounded by candidate capacity, recommendation defaults, fixed DOM order after changes, authenticated thumbnails, in-flight lock, stale visible error and no refresh.

- [ ] **Step 3: Run RED**

```bash
cd apps/desktop
pnpm test -- api.production.test.ts VisualSelectionCard.test.tsx ActionCard.test.tsx
```

- [ ] **Step 4: Add strict v2 API contracts**

```typescript
export interface VisualSceneSelection {
  rough_cut_order: number;
  candidate_id: string;
  included: boolean;
  requested_duration_s: number;
}

confirmVisualSelection(
  sessionId: string,
  proposalId: string,
  selections: VisualSceneSelection[],
): Promise<ProductionCreated>
```

Keep `VisualBeatPlan` and `beats` optional for legacy payloads. Do not use `any` or unsafe casts.

- [ ] **Step 5: Replace beat fieldsets with fixed Rough-Cut rows**

The card state is keyed by `rough_cut_order` and stores candidate, included and duration. Render rows in the received order and never sort them client-side. Show:

```tsx
<span>Szene {choice.scene_number} · Rough Cut {choice.rough_cut_order + 1}</span>
<p>{choice.description}</p>
<p>{selectedCandidate.transcript_snippet}</p>
<p>{selectedCandidate.rationale}</p>
<p>Frames {selectedCandidate.src_start_frame}–{selectedCandidate.src_end_frame_exclusive}</p>
```

Render buttons `1..selectedCandidate.max_duration_s`. Compute requested frames with `Math.round(seconds * gate.fps)`. Disable confirmation until at least three rows are included and total requested frames cover `voice_total_frames`; display the frame-exact last-shot result.

- [ ] **Step 6: Preserve the shared ActionCard refresh**

On success call the existing `refreshAfterConfirm(created)` once. Natural `select_visuals` actions continue through `ProductionActionCard`; do not introduce a second polling loop.

- [ ] **Step 7: Verify Task 6**

```bash
pnpm test -- api.production.test.ts VisualSelectionCard.test.tsx ActionCard.test.tsx ContactSheetApprovalCard.test.tsx ChatPreview.test.tsx ChatStage.test.tsx
pnpm test
pnpm run typecheck
pnpm run lint:tokens
git diff --check
```

Run `pnpm run lint` and record the existing infrastructure result separately if the repository still has no ESLint dependency/configuration; do not alter the lockfile solely to hide that baseline condition.

- [ ] **Step 8: Commit Task 6**

```bash
git add apps/desktop/src/api.ts apps/desktop/src/api.production.test.ts apps/desktop/src/components/chat/VisualSelectionCard.tsx apps/desktop/src/components/chat/VisualSelectionCard.test.tsx apps/desktop/src/components/chat/ActionCard.tsx apps/desktop/src/components/chat/ActionCard.test.tsx
git commit -m "feat(desktop): choose every rough-cut scene"
```

---

### Task 7: Cross-stack verification and `gpt-5.6-luna` live acceptance

**Files:**
- Modify: `tasks/todo.md`
- Modify: `lessons.md`
- Test evidence only: no runtime artifact or secret enters Git.

**Interfaces:**
- Consumes: Tasks 1–6.
- Produces: verified automated/live evidence and one documentation-only commit.

- [ ] **Step 1: Run focused changed-path gates**

```bash
cd services/local-api
uv run pytest tests/test_board_visual_recut.py tests/test_visual_candidates.py tests/test_visual_timeline.py tests/test_production_tools_visual_recut.py tests/test_api_visual_recut.py tests/test_production_orchestrator_visual_recut.py tests/test_production_cancel.py -q
uv run mypy
uv run ruff check src tests

cd ../../../apps/desktop
pnpm test -- api.production.test.ts VisualSelectionCard.test.tsx ContactSheetApprovalCard.test.tsx ActionCard.test.tsx
pnpm run typecheck
pnpm run lint:tokens
```

- [ ] **Step 2: Run full repository gates**

```bash
cd services/local-api
uv run pytest -p no:cacheprovider

cd ../../../apps/desktop
pnpm test
pnpm run typecheck
pnpm run lint:tokens
```

Record exact pass/skip/warning counts and durations. No live run while a changed-path or full code gate is red.

- [ ] **Step 3: Start an isolated branch live stack**

Start FastAPI from this worktree on `127.0.0.1:8765` and Electron from this worktree. Use a temporary process environment only:

```text
LAURA_AGENT_PROVIDER=openai-compat
LAURA_AGENT_MODEL=gpt-5.6-luna
LAURA_AGENT_API_KEY=<existing LAURA_OPENAI_API_KEY, never printed>
```

Run a one-token provider preflight and assert HTTP 200 plus response model exactly `gpt-5.6-luna`. Do not persist the secret or model into Git, 9Router, a database, logs or the task report.

- [ ] **Step 4: Execute the Drive VibeMind acceptance flow**

Capture Script and Voice versions plus content/file/timing hashes before the request. Request a visual-only recut with Rough-Cut order, all scenes offered, Full-frame plus Blur-Fill, and gate stops.

Assert:

1. exactly one v2 proposal for this user revision;
2. every current Rough-Cut scene appears exactly once and in order;
3. long Scene 1 contains later windows from the 117-second source;
4. descriptions, transcript/UI text, rationales, In/Out and 1–10 second choices are visible in Electron;
5. the default includes all scenes if they fit, otherwise at least three;
6. confirm Visual Selection once;
7. exactly one new Cutlist and Contact Sheet appear, with no new render yet;
8. confirm Contact Sheet once;
9. exactly one new render and QA complete without repeated waiting/proposal turns;
10. Script and Voice hashes/versions never change.

- [ ] **Step 5: Verify the delivered media and event log**

Use ffprobe and a decode pass to assert 1080x1920, one audio stream and positive duration. Sample at least three distributed frames; require non-black, differing hashes, complete foreground UI and Blur-Fill. The event log for this revision must contain exactly one Visual Plan save, one Contact Sheet save and one render tool receipt.

- [ ] **Step 6: Verify cancellation separately**

Start a disposable v2 recut, cancel while the agent team is active, and assert Board and Job both become `cancelled`, no post-cancel artifact version changes, and Electron no longer shows `läuft ...`. Do not modify the accepted export.

- [ ] **Step 7: Record and commit evidence only after every required check passes**

Add `## Rough-Cut Visual-Auswahl [x]` to `tasks/todo.md` with commit IDs, exact automated counts, live gate transitions, preserved Script/Voice hashes, export ID/path, media summary and explicit non-claims. Include the already recorded User correction in `lessons.md`.

```bash
git diff --check
git status --short
git diff -- tasks/todo.md lessons.md
git add tasks/todo.md lessons.md
git commit -m "docs(tasks): verify rough-cut visual selection" -- tasks/todo.md lessons.md
git status --short
```

Expected final status: clean worktree. Do not push, merge or deploy without a separate request.
