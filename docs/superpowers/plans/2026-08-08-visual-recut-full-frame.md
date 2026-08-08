# Visual Recut Full-frame Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Einen bestehenden Auto-Short bei unverändertem Sprechertext und unveränderter Voice mit besser ausgewählten Bildfenstern, Full-frame + Blur-Fill und zwei persistenten User-Gates neu schneiden.

**Architecture:** Ein optionaler `visual_recut_request`/`visual_plan`-Zweig sitzt nach `voice` und vor `cutlist`; er verändert die bestehende Gate-S-/Storyline-/Script-/Voice-Kette nicht. Ein reiner Kandidatengenerator erzeugt pro Voice-Segment zeitlich verteilte Shot-Kandidaten, die serverseitig hashgebunden bestätigt werden. Die deterministische Pipeline stoppt nach Visual Plan und Kontaktbogen jeweils hart; erst eine aktuelle Kontaktbogen-Freigabe hebt den strukturellen Render-Guard auf.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, AutoGen AgentChat 0.7.5, SQLite, pytest, mypy, Ruff; React 18, TypeScript strict, Vitest, Electron.

## Global Constraints

- Sprechertext und Voice bleiben byte-, hash- und versionsidentisch; der Recutt darf `storyline`, `script` oder `voice` nicht speichern oder invalidieren.
- Alle Timeline- und Quellbereiche sind Ganzzahl-Frames und end-exclusive (`end_frame_exclusive`).
- `framing_mode="full_frame_blur"` erzwingt `zoom="off"`, `roi=None`, keine Zoom-Timings und Renderer-`fit="blur"`.
- `visual_selection` und `contact_sheet` sind persistierte terminale Zustände für den aktuellen Orchestrator-Run.
- Bestätigungen sind an die aktuelle Proposal-/Contact-Sheet-Identität gebunden; stale Bestätigungen liefern Konflikt und starten keinen Job.
- Alte Boards müssen ohne neue Artefakte oder Meta-Felder unverändert laden und ihren bisherigen Resume-Punkt behalten.
- AutoGen, VLM und schwere Video-Extras bleiben optional; der Backend-Default darf keine neue harte Dependency erhalten.
- Kein `print`/`console.log`, keine Secrets in Logs, Fixtures oder Commits.

---

## File Map

- `services/local-api/src/laura/short_creator/board_models.py`: persistierte Visual-Recut-Typen, Kontaktbogen-Metadaten und rückwärtskompatible Gate-Felder.
- `services/local-api/src/laura/short_creator/board.py`: optionale DAG-Invalidierung, Resume-/Statuslogik und hashgebundene Kontaktbogen-Freigabe.
- `services/local-api/src/laura/short_creator/visual_candidates.py`: reine Kandidatenfenster-, Scoring-, Diversitäts- und Proposal-ID-Logik.
- `services/local-api/src/laura/short_creator/production_tools.py`: `start_visual_recut`, Visual-Plan-Cutlist, Kontaktbogen-Anreicherung und Render-Guard.
- `services/local-api/src/laura/api/short_creator.py`: Visual-Selection- und Kontaktbogen-Confirmation-Services/Endpoints.
- `services/local-api/src/laura/chat/router.py`, `services/local-api/src/laura/chat/executor.py`: natürliche Chat-Bestätigung über dieselben Services.
- `services/local-api/src/laura/short_creator/production_agents.py`, `production_orchestrator.py`, `production_pipeline.py`: Agent-Gate, Hard Stops und deterministisches Resume.
- `services/local-api/src/laura/short_creator/handlers.py`, `production_tools.py`, `services/local-api/src/laura/jobs/runner.py`: kooperativer Cancel bis zum terminalen Jobstatus.
- `apps/desktop/src/api.ts`: strikt typisierte Status- und Confirmation-Verträge.
- `apps/desktop/src/components/chat/VisualSelectionCard.tsx`: Beat-für-Beat-Shot-Auswahl.
- `apps/desktop/src/components/chat/ContactSheetApprovalCard.tsx`: Kontaktbogen-Metadaten und Freigabe.
- `apps/desktop/src/components/chat/ActionCard.tsx`: Gate-Priorität, Statusrefresh und Job-Resume.
- `tasks/todo.md`: verifizierter Abschluss mit Live-Evidenz und expliziten Non-Claims.

---

### Task 1: Persistierter Visual-Recut-Zweig und Gate-State

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board_models.py:342-482,542-586`
- Modify: `services/local-api/src/laura/short_creator/board.py:91-169,315-366,461-521,523-674`
- Create: `services/local-api/tests/test_board_visual_recut.py`

**Interfaces:**
- Produces: `VisualRecutRequest`, `VisualShotCandidate`, `VisualBeatPlan`, `VisualPlan`.
- Produces: `Board.set_contact_sheet_approved(approved_utc: str, sheet_hash: str) -> None` and `Board.clear_contact_sheet_approval(*, enable_gate: bool) -> None`.
- Produces status blocks `visual_selection_gate` and `contact_sheet_gate`; later API/UI tasks consume them unchanged.
- Preserves: existing `_CHAIN` for boards without `visual_recut_request`.

- [ ] **Step 1: Write model and board RED tests**

In this test module, `board_with_finished_film` seeds all existing artifacts through `qa_report`; `visual_request(script, voice)` stamps their current versions/content hashes; `request_fixture`, `plan_fixture`, `cutlist_fixture`, and `sheet_fixture` return the smallest valid Task-1 model instances. The plain `board` fixture contains complete reviews, confirmed Gate S, storyline, script and voice but no optional recut artifacts.

```python
def test_visual_request_invalidates_only_visual_downstream(board_with_finished_film: Board) -> None:
    script = board_with_finished_film.load("script")
    voice = board_with_finished_film.load("voice")
    assert isinstance(script, Script)
    assert isinstance(voice, VoiceArtifact)
    script_version, voice_version = script.version, voice.version

    board_with_finished_film.save("visual_recut_request", visual_request(script, voice))

    assert board_with_finished_film.load("storyline") is not None
    assert board_with_finished_film.load("script").version == script_version
    assert board_with_finished_film.load("voice").version == voice_version
    assert board_with_finished_film.load("cutlist") is None
    assert board_with_finished_film.load("contact_sheet") is None
    assert board_with_finished_film.load("render_report") is None
    assert board_with_finished_film.load("qa_report") is None


def test_pending_visual_plan_and_sheet_approval_are_resume_points(board: Board) -> None:
    board.save("visual_recut_request", request_fixture())
    board.save("visual_plan", plan_fixture(confirmed_utc=None))
    assert board.resume_point([1, 2]) == "visual_selection"
    board.save("visual_plan", plan_fixture(confirmed_utc="2026-08-08T10:00:00+00:00"))
    board.save("cutlist", cutlist_fixture())
    board.save("contact_sheet", sheet_fixture())
    assert board.resume_point([1, 2]) == "contact_sheet_approval"
```

Also pin: duplicate candidate IDs fail validation; every beat has exactly one recommended candidate; selected IDs must belong to their beat; old meta JSON without new fields loads; a board without a request never gains a new resume requirement.

- [ ] **Step 2: Run the RED tests**

Run from `services/local-api`:

```bash
uv run pytest tests/test_board_visual_recut.py -q
```

Expected: collection fails because the four new models/artifact names and gate methods do not exist.

- [ ] **Step 3: Add the persistable models**

Add strict Pydantic models with these exact fields:

```python
FramingMode = Literal["full_frame_blur"]

class VisualRecutRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=1, ge=1)
    user_request: str = Field(min_length=1, max_length=2000)
    framing_mode: FramingMode = "full_frame_blur"
    script_version: int = Field(ge=1)
    script_hash: str = Field(min_length=64, max_length=64)
    voice_version: int = Field(ge=1)
    voice_hash: str = Field(min_length=64, max_length=64)
    parents: dict[str, str] = Field(default_factory=dict)

class VisualShotCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    candidate_id: str
    beat_id: str
    voice_segment_index: int = Field(ge=0)
    scene_number: int = Field(ge=1)
    window_index: int = Field(ge=0)
    src_start_frame: int = Field(ge=0)
    src_end_frame_exclusive: int
    thumb_frame: int = Field(ge=0)
    description: str
    transcript_snippet: str
    rationale: str
    score: float

class VisualBeatPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    beat_id: str
    voice_segment_index: int = Field(ge=0)
    narration_text: str = Field(min_length=1)
    duration_s: float = Field(gt=0.0)
    candidates: list[VisualShotCandidate] = Field(min_length=1, max_length=4)
    recommended_candidate_id: str
    selected_candidate_id: str | None = None

class VisualPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(default=1, ge=1)
    proposal_hash: str = Field(min_length=64, max_length=64)
    request_hash: str = Field(min_length=64, max_length=64)
    beats: list[VisualBeatPlan] = Field(min_length=1)
    confirmed_utc: str | None = None
    parents: dict[str, str] = Field(default_factory=dict)
```

Extend `ContactSheetTile` with optional/defaulted `src_start_frame`, `src_end_frame_exclusive`, `narration_excerpt`, and `rationale`. Extend `BoardMeta` with `contact_sheet_gate: bool = False`, `contact_sheet_approved_utc: str | None = None`, and `contact_sheet_approved_hash: str | None = None`.

- [ ] **Step 4: Implement the optional artifact DAG and status**

Register `visual_recut_request` and `visual_plan` in `_SINGLETONS`, but do not append them unconditionally to `_CHAIN`. Add explicit dependency cases:

```python
_DOWNSTREAM: dict[str, tuple[str, ...]] = {
    "scene_selection": ("storyline", "script", "voice", "visual_recut_request",
                        "visual_plan", "cutlist", "contact_sheet", "render_report", "qa_report"),
    "storyline": ("script", "voice", "visual_recut_request", "visual_plan", "cutlist",
                  "contact_sheet", "render_report", "qa_report"),
    "script": ("voice", "visual_recut_request", "visual_plan", "cutlist",
               "contact_sheet", "render_report", "qa_report"),
    "voice": ("visual_recut_request", "visual_plan", "cutlist", "contact_sheet",
              "render_report", "qa_report"),
    "visual_recut_request": ("visual_plan", "cutlist", "contact_sheet",
                             "render_report", "qa_report"),
    "visual_plan": ("cutlist", "contact_sheet", "render_report", "qa_report"),
    "cutlist": ("contact_sheet", "render_report", "qa_report"),
    "contact_sheet": ("render_report", "qa_report"),
    "render_report": ("qa_report",),
    "qa_report": (),
}

def downstream_of(name: str) -> tuple[str, ...]:
    if name == "scene_reviews":
        return ("scene_selection",) + _DOWNSTREAM["scene_selection"]
    return _DOWNSTREAM[name]
```

Make `resume_point()` branch after a current voice: pending/missing visual plan returns `visual_selection`; an approved plan continues at `cutlist`; a current contact sheet under an enabled, hash-mismatched gate returns `contact_sheet_approval`. Status exposes the proposal ID, beats/candidates/selections, current sheet hash, approval/pending flags and enriched tiles. Contact-sheet approval compares `content_hash(current_sheet)`, not just timestamps.

- [ ] **Step 5: Run focused and neighboring board tests**

```bash
uv run pytest tests/test_board_visual_recut.py tests/test_board.py tests/test_board_scene_selection.py -q
uv run mypy src/laura/short_creator/board.py src/laura/short_creator/board_models.py
uv run ruff check src/laura/short_creator/board.py src/laura/short_creator/board_models.py tests/test_board_visual_recut.py
```

Expected: all pass; old-board resume tests remain byte/behavior compatible.

- [ ] **Step 6: Commit Task 1**

```bash
git add services/local-api/src/laura/short_creator/board.py services/local-api/src/laura/short_creator/board_models.py services/local-api/tests/test_board_visual_recut.py
git commit -m "feat(shorts): add visual recut board state"
```

---

### Task 2: Zeitlich verteilte, beatbezogene Kandidaten

**Files:**
- Create: `services/local-api/src/laura/short_creator/visual_candidates.py`
- Create: `services/local-api/tests/test_visual_candidates.py`

**Interfaces:**
- Consumes: Task 1 models and existing `ScriptLine`, `VoiceArtifact`, `SceneReview`, `BestWindow`.
- Produces: `TranscriptSpan`, `SceneMaterial`, `coverage_windows(...)`, `build_visual_plan(...)`.
- Produces stable SHA-256 `candidate_id` and `proposal_hash`; Task 3 persists them without alteration.

- [ ] **Step 1: Write candidate RED tests**

Define local factories with fixed contracts: `degraded_scene(...) -> SceneMaterial` has `review.degraded=True`; `scene_with_span(...) -> SceneMaterial` embeds one timestamped `TranscriptSpan`; `request() -> VisualRecutRequest`; `lines() -> list[ScriptLine]`; `voice() -> VoiceArtifact` has one `VoiceSegment` per line; `scenes() -> list[SceneMaterial]` offers at least two viable sources for every beat.

```python
def test_degraded_117_second_scene_covers_late_material() -> None:
    scene = degraded_scene(start=0, end_exclusive=3533, fps=30.0)
    windows = coverage_windows(scene, beat_text="organizing files", beat_duration_s=3.2)
    assert len(windows) == 4
    assert windows[0].start_frame == 0
    assert any(w.start_frame >= 1766 for w in windows)
    assert all(w.start_frame < w.end_frame_exclusive <= 3533 for w in windows)


def test_relevant_transcript_anchor_beats_uniform_fallback() -> None:
    scene = scene_with_span("drafting emails", start_frame=2100, end_frame_exclusive=2220)
    windows = coverage_windows(scene, beat_text="drafting emails", beat_duration_s=3.0)
    assert windows[0].start_frame <= 2100 < windows[0].end_frame_exclusive


def test_recommendations_avoid_adjacent_duplicate_visuals() -> None:
    plan = build_visual_plan(request=request(), ordered_lines=lines(), voice=voice(), scenes=scenes(), fps=30.0)
    chosen = [
        next(c for c in beat.candidates if c.candidate_id == beat.recommended_candidate_id)
        for beat in plan.beats
    ]
    assert all(
        (left.scene_number, left.src_start_frame, left.src_end_frame_exclusive)
        != (right.scene_number, right.src_start_frame, right.src_end_frame_exclusive)
        for left, right in zip(chosen, chosen[1:], strict=False)
    )
```

Also pin deterministic IDs, at most four candidates per beat, required window capacity for the Voice duration, and a clear `InsufficientVisualCandidates` exception naming the beat that cannot be covered.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/test_visual_candidates.py -q
```

Expected: import failure for `laura.short_creator.visual_candidates`.

- [ ] **Step 3: Implement pure material and window types**

```python
@dataclass(frozen=True)
class TranscriptSpan:
    start_frame: int
    end_frame_exclusive: int
    text: str

@dataclass(frozen=True)
class SceneMaterial:
    scene_number: int
    src_start_frame: int
    src_end_frame_exclusive: int
    description: str
    transcript: str
    transcript_spans: tuple[TranscriptSpan, ...]
    review: SceneReview | None

@dataclass(frozen=True)
class CandidateWindow:
    start_frame: int
    end_frame_exclusive: int
    window_index: int
    transcript_snippet: str
```

`coverage_windows` uses healthy saved review windows first. For degraded/missing reviews it ranks timestamped transcript anchors by normalized token overlap, then fills remaining slots with uniform anchors at 0%, 33%, 67% and the latest legal start. Window capacity is `max(4.0, beat_duration_s + INTER_SCENE_GAP_S)` clamped to the scene. Deduplicate by frame range.

- [ ] **Step 4: Implement scoring and stable plan construction**

Score semantic overlap against narration, review hook score, non-degraded evidence, capacity and adjacent diversity. Candidate IDs hash canonical JSON of beat index + scene + end-exclusive range. Proposal ID hashes canonical request hash plus ordered beat/candidate IDs. The second recommendation pass must choose a non-repeated scene/window when its score is within 15% of the top candidate.

- [ ] **Step 5: Verify Task 2**

```bash
uv run pytest tests/test_visual_candidates.py -q
uv run mypy src/laura/short_creator/visual_candidates.py
uv run ruff check src/laura/short_creator/visual_candidates.py tests/test_visual_candidates.py
```

- [ ] **Step 6: Commit Task 2**

```bash
git add services/local-api/src/laura/short_creator/visual_candidates.py services/local-api/tests/test_visual_candidates.py
git commit -m "feat(shorts): generate distributed visual candidates"
```

---

### Task 3: Production-Tools für Recutt, Full-frame und Render-Guard

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py:655-690,2551-2860,2875-3125,3335-3365`
- Modify: `services/local-api/src/laura/short_creator/production_agents.py:215-260`
- Create: `services/local-api/tests/test_production_tools_visual_recut.py`
- Modify: `services/local-api/tests/test_production_tools_cutlist.py`

**Interfaces:**
- Consumes: `build_visual_plan` and Task 1 Board artifacts.
- Produces tool `start_visual_recut(user_request: str, framing_mode: str = "full_frame_blur") -> dict[str, Any]`.
- Extends `build_cutlist(...)`: a confirmed `visual_plan` selects a separate beat-indexed path; normal storyline path is unchanged.
- Enforces contact-sheet hash before `deps.render_segments` can be called.

- [ ] **Step 1: Write tool RED tests with mutation guards**

Use one local `Counter` dataclass with `value: int`, `raise_on_call(*args, **kwargs)` (increments then raises), and a non-raising fake renderer for the approved path. `versions(board, *names)` returns the artifact version tuple; `tool(...)` looks up the named `ToolSpec.func`; `confirm_visual_plan(board)` copies every recommended candidate into `selected_candidate_id` and stamps `confirmed_utc` through `board.save`.

```python
def test_start_visual_recut_preserves_script_and_voice_versions(finished_board: Board) -> None:
    before = versions(finished_board, "script", "voice")
    result = tool(finished_board, "start_visual_recut")(
        user_request="better pictures, keep voice", framing_mode="full_frame_blur"
    )
    assert result["ok"] is True
    assert result["status"] == "awaiting_user_input"
    assert versions(finished_board, "script", "voice") == before
    assert isinstance(finished_board.load("visual_plan"), VisualPlan)


def test_visual_cutlist_is_full_frame_and_uses_voice_segment_durations(board: Board) -> None:
    confirm_visual_plan(board)
    result = tool(board, "build_cutlist")()
    cutlist = board.load("cutlist")
    assert result["ok"] is True
    assert all(s.roi is None and s.zoom_start_s is None for s in cutlist.segments)
    assert cutlist.parents["visual_plan"] == content_hash(board.load("visual_plan"))


def test_render_refuses_before_current_sheet_approval(board: Board) -> None:
    calls = Counter()
    result = tool(board, "render_production", render_segments=calls.raise_on_call)()
    assert result == {"ok": False, "reason": "contact sheet approval required"}
    assert calls.value == 0
```

Also assert: stale request hashes refuse; legacy voice with `segments=None` returns an actionable non-mutating error; a second pending request is idempotent; `save_contact_sheet` emits enriched tile metadata.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/test_production_tools_visual_recut.py tests/test_production_tools_cutlist.py -q
```

Expected: `start_visual_recut` is absent and render currently reaches the fake.

- [ ] **Step 3: Implement `start_visual_recut`**

Load current `Storyline`, `Script`, and segmented `VoiceArtifact`; order script lines with `lines_in_storyline_order`; gather every rough-cut scene, review and timestamped transcript span into `SceneMaterial`; save a `VisualRecutRequest` stamped with current content hashes/versions; call `build_visual_plan`; save its pending proposal. On any precondition/candidate failure, return `ok=False` without modifying narrative artifacts.

Expose only the exact framing vocabulary:

```python
if framing_mode != "full_frame_blur":
    return {"ok": False, "reason": 'framing_mode must be "full_frame_blur"'}
```

- [ ] **Step 4: Add the visual-plan cutlist branch**

When a confirmed, provenance-current `VisualPlan` exists, pair each beat by `voice_segment_index`, never by the old `(chapter, scene_number)` identity. Segment duration is the corresponding voice clip plus the existing gap/cushion rules. Resolve the selected candidate's source range, pull the start back if necessary to fit, and always create:

```python
CutSegment(
    order=order,
    scene_number=candidate.scene_number,
    start_frame=start_frame,
    end_frame_exclusive=end_frame_exclusive,
    roi=None,
    zoom_start_s=None,
)
```

Stamp parents `script`, `voice`, and `visual_plan`. Preserve the existing storyline-derived branch byte-for-byte when no visual request exists.

- [ ] **Step 5: Enrich the sheet and gate the renderer**

Map Visual Plan beats back into `ContactSheetTile` metadata. Before render-cycle counting or `deps.render_segments`, require `meta.contact_sheet_gate` approval to match `content_hash(current_contact_sheet)`. Keep the existing render call's `fit="blur"` and assert it in the fake-render test.

- [ ] **Step 6: Update the coding-agent charter**

Give `coding_agent` the `start_visual_recut` tool. State: visual-selection changes with preserved narration call this tool once and stop; framing-only changes still use `build_cutlist(zoom="off")`; neither path may rewrite storyline/script/voice.

- [ ] **Step 7: Verify Task 3**

```bash
uv run pytest tests/test_production_tools_visual_recut.py tests/test_production_tools_cutlist.py tests/test_production_tools.py -q
uv run mypy src/laura/short_creator/production_tools.py src/laura/short_creator/production_agents.py
uv run ruff check src/laura/short_creator/production_tools.py src/laura/short_creator/production_agents.py tests/test_production_tools_visual_recut.py
```

- [ ] **Step 8: Commit Task 3**

```bash
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/src/laura/short_creator/production_agents.py services/local-api/tests/test_production_tools_visual_recut.py services/local-api/tests/test_production_tools_cutlist.py
git commit -m "feat(shorts): build full-frame visual recuts"
```

---

### Task 4: Hashgebundene HTTP- und Chat-Bestätigungen

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py:92-103,880-1081`
- Modify: `services/local-api/src/laura/chat/router.py:33-105,215-255,431-449`
- Modify: `services/local-api/src/laura/chat/executor.py:25-40,570-630,1085-1110`
- Create: `services/local-api/tests/test_api_visual_recut.py`
- Modify: `services/local-api/tests/test_chat_router.py`
- Modify: `services/local-api/tests/test_chat_executor.py`

**Interfaces:**
- Produces `confirm_visual_selection(db, session_id, proposal_hash, selected_candidate_ids)`.
- Produces `confirm_contact_sheet(db, session_id, contact_sheet_hash)`.
- Produces endpoints `POST /production/{session_id}/visual-selection:confirm` and `POST /production/{session_id}/contact-sheet:confirm`.
- Both chat tools call the same service functions; no second confirmation implementation.

- [ ] **Step 1: Write API RED tests**

`pending_session` and `sheet_session` are session IDs seeded through the real DB/repository helpers; `latest_job_id(session_id)`, `board(session_id)`, and `current_sheet_hash(session_id)` read that same database and never mock the state being asserted.

```python
def test_confirm_visual_selection_rejects_stale_proposal(client, pending_session) -> None:
    response = client.post(
        f"/production/{pending_session}/visual-selection:confirm",
        json={"proposal_hash": "0" * 64, "selected_candidate_ids": ["candidate-a"]},
    )
    assert response.status_code == 409
    assert "stale visual proposal" in response.text
    assert latest_job_id(pending_session) is None


def test_confirm_contact_sheet_stamps_current_hash_and_enqueues_resume(client, sheet_session) -> None:
    response = client.post(
        f"/production/{sheet_session}/contact-sheet:confirm",
        json={"contact_sheet_hash": current_sheet_hash(sheet_session)},
    )
    assert response.status_code == 202
    assert response.json()["job_id"]
    assert board(sheet_session).status()["contact_sheet_gate"]["approved"] is True
```

Pin busy-job conflicts, invalid candidate IDs, exactly one selection per beat, idempotent re-confirm that heals a missing resume, and rollback/forward-heal parity with existing Gate S.

- [ ] **Step 2: Write chat-router/executor RED tests**

Add router fixtures for context lines `Visual-Auswahl offen` and `Kontaktbogen-Freigabe offen`. Assert “deine Auswahl passt” routes to `select_visuals` with `{"proposal_hash": <current hash>, "selected_candidate_ids": <current recommendations>}`, while “Kontaktbogen freigeben” routes to `approve_contact_sheet` with `{"contact_sheet_hash": <current hash>}`. Validators require the 64-character hashes and a non-empty candidate-ID list. Executor tests monkeypatch the two API service functions and verify the action card carries the resumed `job_id`.

- [ ] **Step 3: Run RED**

```bash
uv run pytest tests/test_api_visual_recut.py tests/test_chat_router.py tests/test_chat_executor.py -q
```

- [ ] **Step 4: Implement request models, services and endpoints**

```python
class VisualSelectionConfirmRequest(BaseModel):
    proposal_hash: str = Field(min_length=64, max_length=64)
    selected_candidate_ids: list[str] = Field(min_length=1)

class ContactSheetConfirmRequest(BaseModel):
    contact_sheet_hash: str = Field(min_length=64, max_length=64)
```

Use the existing session lookup, busy guard and `run_production_resume` path. Visual confirmation copies `selected_candidate_id` into each beat and stamps `confirmed_utc`; contact-sheet confirmation calls `Board.set_contact_sheet_approved`. Neither endpoint accepts a task string or starts an agent-message run.

- [ ] **Step 5: Expose gate context and chat actions**

Add `select_visuals` and `approve_contact_sheet` to `TOOLS`, validation and the router prompt. `compose_context` includes current proposal ID/recommendations or current sheet hash only while pending. Executor handlers resolve the active session, call the shared service, preserve exact HTTP error details and append one resumed action card.

- [ ] **Step 6: Verify Task 4**

```bash
uv run pytest tests/test_api_visual_recut.py tests/test_api_scene_selection.py tests/test_chat_router.py tests/test_chat_executor.py -q
uv run mypy src/laura/api/short_creator.py src/laura/chat/router.py src/laura/chat/executor.py
uv run ruff check src/laura/api/short_creator.py src/laura/chat/router.py src/laura/chat/executor.py tests/test_api_visual_recut.py
```

- [ ] **Step 7: Commit Task 4**

```bash
git add services/local-api/src/laura/api/short_creator.py services/local-api/src/laura/chat/router.py services/local-api/src/laura/chat/executor.py services/local-api/tests/test_api_visual_recut.py services/local-api/tests/test_chat_router.py services/local-api/tests/test_chat_executor.py
git commit -m "feat(api): add visual recut approval gates"
```

---

### Task 5: Orchestrator- und Pipeline-Hard-Stops

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_agents.py:35-52,340-382`
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py:121-327,536-628,741-917`
- Modify: `services/local-api/src/laura/short_creator/production_pipeline.py:27-113,166-209`
- Create: `services/local-api/tests/test_production_orchestrator_visual_recut.py`
- Modify: `services/local-api/tests/test_production_pipeline.py`
- Modify: `services/local-api/tests/test_production_agents.py`

**Interfaces:**
- Consumes board resume points `visual_selection` and `contact_sheet_approval`.
- Produces one gate classifier `pending_user_gate(board, expected_scenes) -> Literal["scene_selection", "visual_selection", "contact_sheet"] | None`.
- Produces `awaiting_user_input` results with `gate`, `proposal_hash`/`contact_sheet_hash`, and `required_action`.

- [ ] **Step 1: Write hard-stop RED tests**

The new test module defines separate `agent_model` and `orchestrator_model` fakes using the same AutoGen test client already used in `test_production_agents.py`; `run_follow_up` calls real `run_production`; `specs_with_render_guard` supplies real `ToolSpec` names with only render replaced by a local call counter.

```python
def test_follow_up_stops_after_visual_plan_without_second_model_call(agent_model, finished_board) -> None:
    agent_model.enqueue(tool_call("start_visual_recut", {"user_request": "better shots"}))
    agent_model.raise_on_next_call()
    result = run_follow_up(finished_board, agent_model)
    assert result["status"] == "awaiting_user_input"
    assert result["gate"] == "visual_selection"
    assert agent_model.calls == 1


def test_tail_stops_after_contact_sheet_without_render_or_qa(board, specs) -> None:
    calls = Counter()
    outcome, qa = run_tail_with_qa(
        None, board, None, asset_id="asset", deps=None, event_sink=None,
        expected_scenes=[1, 2], specs=specs_with_render_guard(calls), qa_execute=calls.raise_on_call,
    )
    assert outcome.ok is True
    assert board.resume_point([1, 2]) == "contact_sheet_approval"
    assert qa is None
    assert calls.value == 0
```

Add a real AutoGen 0.7.5 fake-model contract mirroring the existing Gate-S test: after the coding agent's `start_visual_recut` tool response, a forbidden next model call raises. Pin plain resume while either gate is pending to zero team constructions.

Also add `test_agent_claimed_render_without_receipt_is_hard_fail`: inject a final response claiming that render/contact-sheet saves completed with zero tool calls, then assert `status == "hard_fail"`, `export_id is None`, and no missing artifact is synthesized from prose.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/test_production_orchestrator_visual_recut.py tests/test_production_pipeline.py tests/test_production_agents.py -q
```

- [ ] **Step 3: Generalize the pending-gate termination condition**

Capture initial SceneSelection and VisualPlan versions when constructing the team. `FunctionalTermination` returns true for a newly created unconfirmed proposal of either kind. For any follow-up message, cap `coding_agent.max_tool_iterations` at one so AutoGen returns to the team boundary immediately after a mutating tool; keep the existing four-iteration budget for non-follow-up/gate-off initial production.

- [ ] **Step 4: Add pre-run and post-run gate classification**

Replace scene-only branches with the shared gate classifier. `_completed_result` carries optional gate metadata. A pending gate suppresses Stage-B escalation even if the agent's textual outcome looks like a hard fail. Summary text is derived from the persisted gate, never from the agent response.

- [ ] **Step 5: Stop deterministic tail before render and QA**

Leave `_STEP_BY_RESUME_POINT` without a `contact_sheet_approval` tool. After `run_deterministic_tail`, call QA only when `board.resume_point(expected_scenes)` is `qa_report` or `done`; otherwise return `(tail, None)`. `run_production` maps `contact_sheet_approval` to `awaiting_user_input` instead of declaring a hollow success.

- [ ] **Step 6: Update the production task contract**

Replace the “Kontaktbogen purely via follow-up, no state” prose with the structural Visual Plan and Contact Sheet gates. Explicitly forbid re-saving script/voice during an active recutt and tell the team to stop after `start_visual_recut`.

- [ ] **Step 7: Verify Task 5**

```bash
uv run pytest tests/test_production_orchestrator_visual_recut.py tests/test_production_orchestrator_scene_gate.py tests/test_production_pipeline.py tests/test_production_agents.py -q
uv run mypy src/laura/short_creator/production_agents.py src/laura/short_creator/production_orchestrator.py src/laura/short_creator/production_pipeline.py
uv run ruff check src/laura/short_creator/production_agents.py src/laura/short_creator/production_orchestrator.py src/laura/short_creator/production_pipeline.py tests/test_production_orchestrator_visual_recut.py
```

- [ ] **Step 8: Commit Task 5**

```bash
git add services/local-api/src/laura/short_creator/production_agents.py services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/src/laura/short_creator/production_pipeline.py services/local-api/tests/test_production_orchestrator_visual_recut.py services/local-api/tests/test_production_pipeline.py services/local-api/tests/test_production_agents.py
git commit -m "fix(shorts): hard-stop visual recut gates"
```

---

### Task 6: Kooperativer Cancel bis zum terminalen Jobstatus

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board_models.py:542-562`
- Modify: `services/local-api/src/laura/short_creator/production_tools.py:306-319,3335-3365`
- Modify: `services/local-api/src/laura/short_creator/handlers.py:102-170`
- Modify: `services/local-api/src/laura/short_creator/production_agents.py:330-382`
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py:53,631-647,864-917`
- Modify: `services/local-api/src/laura/jobs/runner.py:261-315,362-383`
- Create: `services/local-api/tests/test_production_cancel.py`
- Modify: `services/local-api/tests/test_job_runner.py`

**Interfaces:**
- Extends `ProductionDeps` with `cancel_requested: Callable[[], bool] | None = None`.
- Extends `ProductionRunStatus`/`BoardStatus` with `cancelled`.
- JobRunner persists handler result `status="cancelled"` as job status `cancelled`, not `succeeded` or `failed`.

- [ ] **Step 1: Write cancellation RED tests**

`run_production_with_two_turn_fake` uses the real orchestrator with an injected two-turn execute seam. `board_write_counter` wraps the board's `save`/meta-write methods and exposes `after_cancel`; `runner_with_handler` and `enqueue_test_job` use the existing `test_job_runner.py` database fixture rather than an in-memory fake.

```python
def test_cancel_between_agent_turns_stops_before_next_tool(board, deps) -> None:
    cancelled = iter([False, True])
    deps.cancel_requested = lambda: next(cancelled, True)
    writes = board_write_counter(board)
    result = run_production_with_two_turn_fake(board, deps)
    assert result["status"] == "cancelled"
    assert writes.after_cancel == 0


def test_runner_marks_cancelled_result_cancelled(db) -> None:
    runner = runner_with_handler(db, lambda _ctx: {"status": "cancelled", "reason": "user"})
    job_id = enqueue_test_job(db)
    runner.run_once()
    assert repos.get_job(db, job_id)["status"] == "cancelled"
```

Also assert a cancel requested immediately before a mutating tool returns a cancelled tool result and does not call the underlying function.

- [ ] **Step 2: Run RED**

```bash
uv run pytest tests/test_production_cancel.py tests/test_job_runner.py tests/test_job_cancel_cooperative.py -q
```

- [ ] **Step 3: Thread the cancel callback through production**

`handle_production_run` creates/replaces `ProductionDeps.cancel_requested` with `lambda: repos.is_job_cancel_requested(ctx.db, ctx.job_id)`. Wrap exactly these mutating ToolSpecs so they check the callback immediately before invoking the underlying function: `review_scene`, `propose_scene_selection`, `save_storyline`, `save_script_chapter`, `set_board_language`, `synthesize_script_voice`, `start_visual_recut`, `build_cutlist`, `save_contact_sheet`, `render_production`, `save_qa_report`, and `revert_artifact`. Add the same predicate to the team's termination condition and a post-team override that returns terminal `cancelled` without Stage-B escalation.

- [ ] **Step 4: Persist honest board/job cancellation**

Add `BoardStatus="cancelled"`; set it when production stops cooperatively. Add `_finish_cancelled(job_id, result)` to JobRunner and route any handler result with `status == "cancelled"` there before `job_failure_from_result`. Store the result JSON, finish time and metrics label `cancelled`.

- [ ] **Step 5: Verify Task 6**

```bash
uv run pytest tests/test_production_cancel.py tests/test_job_runner.py tests/test_job_cancel_cooperative.py tests/test_jobs_api.py -q
uv run mypy src/laura/short_creator/handlers.py src/laura/short_creator/production_tools.py src/laura/short_creator/production_agents.py src/laura/short_creator/production_orchestrator.py src/laura/jobs/runner.py
uv run ruff check src/laura/short_creator/handlers.py src/laura/short_creator/production_tools.py src/laura/short_creator/production_agents.py src/laura/short_creator/production_orchestrator.py src/laura/jobs/runner.py tests/test_production_cancel.py
```

- [ ] **Step 6: Commit Task 6**

```bash
git add services/local-api/src/laura/short_creator/handlers.py services/local-api/src/laura/short_creator/production_tools.py services/local-api/src/laura/short_creator/production_agents.py services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/src/laura/short_creator/board_models.py services/local-api/src/laura/jobs/runner.py services/local-api/tests/test_production_cancel.py services/local-api/tests/test_job_runner.py
git commit -m "fix(jobs): honor production cancellation"
```

---

### Task 7: Electron-Auswahl und Kontaktbogen-Freigabe

**Files:**
- Modify: `apps/desktop/src/api.ts:740-857,992-1016`
- Modify: `apps/desktop/src/api.production.test.ts`
- Create: `apps/desktop/src/components/chat/VisualSelectionCard.tsx`
- Create: `apps/desktop/src/components/chat/VisualSelectionCard.test.tsx`
- Create: `apps/desktop/src/components/chat/ContactSheetApprovalCard.tsx`
- Create: `apps/desktop/src/components/chat/ContactSheetApprovalCard.test.tsx`
- Modify: `apps/desktop/src/components/chat/ActionCard.tsx:175-207,233-329,406-516`
- Modify: `apps/desktop/src/components/chat/ActionCard.test.tsx`

**Interfaces:**
- Consumes backend status blocks exactly as Task 1/4 define them.
- Produces `LauraClient.confirmVisualSelection(...)` and `confirmContactSheet(...)`.
- Produces cards with `onConfirmed(): Promise<void> | void`; ActionCard reuses one status/job refresh path.

- [ ] **Step 1: Write strict API RED tests**

Reuse the file's existing `fetchMock` and authenticated `LauraClient` fixture. Add strongly typed status builders in the test file; do not cast payloads through `any`.

```typescript
it("confirms a visual proposal with its exact id and selected candidates", async () => {
  await client.confirmVisualSelection("session-1", "a".repeat(64), ["candidate-1"]);
  expect(fetchMock).toHaveBeenCalledWith(
    expect.stringContaining("/production/session-1/visual-selection:confirm"),
    expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ proposal_hash: "a".repeat(64), selected_candidate_ids: ["candidate-1"] }),
    }),
  );
});
```

Add equivalent contact-sheet hash coverage and a status parse fixture carrying both optional gates; older payloads must still narrow safely.

- [ ] **Step 2: Write card RED tests**

Each new card test creates a `LauraClient` stub with only the card's typed methods and `assetFrameUrl`; the `onConfirmed` spy resolves `Promise<void>`. Reuse the existing blob URL setup from `SceneSelectionCard.test.tsx` for thumbnails.

Visual card: recommended radio is preselected per beat; one selection per beat; confirm disabled while incomplete/in-flight; candidate thumbnail URL uses the candidate `thumb_frame`; successful response calls `onConfirmed` once. Contact-sheet card: renders In/Out, narration excerpt and rationale; “Kontaktbogen freigeben” posts the displayed hash; stale error stays visible and does not call refresh.

- [ ] **Step 3: Run RED**

Run from `apps/desktop`:

```bash
npm test -- api.production.test.ts VisualSelectionCard.test.tsx ContactSheetApprovalCard.test.tsx ActionCard.test.tsx
```

- [ ] **Step 4: Add typed API contracts**

Define `VisualShotCandidate`, `VisualBeatPlan`, `VisualSelectionGateStatus`, `ContactSheetTileStatus`, and `ContactSheetGateStatus` without `any`. Extend the artifact-name union with `visual_recut_request` and `visual_plan`. Add:

```typescript
confirmVisualSelection(
  sessionId: string,
  proposalId: string,
  selectedCandidateIds: string[],
): Promise<ProductionCreated>

confirmContactSheet(sessionId: string, contactSheetHash: string): Promise<ProductionCreated>
```

- [ ] **Step 5: Implement the two focused cards**

Reuse `LauraClient.assetFrameUrl`/authenticated blob loading patterns from `SceneSelectionCard`; never expose filesystem paths. Keep the contact-sheet PNG in the existing `ChatPreview`; the new card renders only approval metadata/action beside the normal session content.

- [ ] **Step 6: Integrate gate priority and resume refresh**

Generalize `refreshAfterConfirm` so all three confirmation cards can hand back the newly queued job. Render priority while `phase === "done"` is: pending visual selection, pending original Gate S, pending script, pending contact sheet, export result. A confirmed contact sheet disappears immediately and the same card resumes event/job polling.

- [ ] **Step 7: Verify Task 7**

```bash
npm test -- api.production.test.ts VisualSelectionCard.test.tsx ContactSheetApprovalCard.test.tsx ActionCard.test.tsx ChatPreview.test.tsx ChatStage.test.tsx
npm run typecheck
npm run lint
```

- [ ] **Step 8: Commit Task 7**

```bash
git add apps/desktop/src/api.ts apps/desktop/src/api.production.test.ts apps/desktop/src/components/chat/VisualSelectionCard.tsx apps/desktop/src/components/chat/VisualSelectionCard.test.tsx apps/desktop/src/components/chat/ContactSheetApprovalCard.tsx apps/desktop/src/components/chat/ContactSheetApprovalCard.test.tsx apps/desktop/src/components/chat/ActionCard.tsx apps/desktop/src/components/chat/ActionCard.test.tsx
git commit -m "feat(desktop): review visual recuts in chat"
```

---

### Task 8: Gesamtabnahme und echter Rowboat-Lauf

**Files:**
- Modify: `tasks/todo.md:649-end`
- Test evidence only: no production artifact, key, workspace database or video enters Git.

**Interfaces:**
- Consumes all prior tasks.
- Produces verified test/live evidence and one documentation-only commit.

- [ ] **Step 1: Run the focused cross-stack suite**

From `services/local-api`:

```bash
uv run pytest tests/test_board_visual_recut.py tests/test_visual_candidates.py tests/test_production_tools_visual_recut.py tests/test_api_visual_recut.py tests/test_production_orchestrator_visual_recut.py tests/test_production_cancel.py -q
uv run mypy src tests
uv run ruff check src tests
```

From `apps/desktop`:

```bash
npm test -- api.production.test.ts VisualSelectionCard.test.tsx ContactSheetApprovalCard.test.tsx ActionCard.test.tsx
npm run typecheck
npm run lint
```

- [ ] **Step 2: Run full repository gates**

```bash
# services/local-api
uv run pytest -p no:cacheprovider

# apps/desktop
npm test
npm run typecheck
npm run lint
```

Record exact pass/skip/warning counts and durations. Any optional-extra failure must be isolated and classified; do not mark the task complete while a changed-path gate is red.

- [ ] **Step 3: Start the branch build without exposing secrets**

Start FastAPI from this worktree on `127.0.0.1:8765`, Electron via `npm run dev`, and reuse the already configured 9Router environment. Verify the provider preflight without printing the router key. Confirm Electron is connected to this worktree's backend before sending the production message.

- [ ] **Step 4: Execute the Rowboat acceptance flow**

In the existing `Drive VibeMind` project, request: keep current English script and Voice, rebuild only picture selection, use Full-frame + Blur-Fill, and stop at each gate. Capture Board status before the request and assert script/voice versions and hashes are identical at every later checkpoint.

Acceptance sequence:

1. exactly one `visual_selection` proposal appears;
2. candidates include materially later windows from the 117-second degraded scene;
3. confirm the recommended per-beat choices once;
4. exactly one contact sheet appears and no export exists yet;
5. visually inspect complete Rowboat UI and source diversity in Electron;
6. confirm the contact sheet once;
7. wait for terminal render/QA without repeated wait/fact-sheet/proposal turns.

- [ ] **Step 5: Verify the delivered media**

Use ffprobe to assert 1080×1920, one audio stream, positive duration and a successful decode. Sample at least three distributed frames; require non-black, differing hashes. Visually inspect each sample for complete UI within the foreground frame and Blur-Fill outside it. Check the session event log contains exactly one Visual Plan save, one Contact Sheet save and one render call.

- [ ] **Step 6: Verify cancellation separately**

Start a disposable recutt, request cancel while the agent team is active, and assert the job and board both become `cancelled`, no post-cancel artifact version changes, and Electron stops showing “läuft …”. Do not cancel or overwrite the accepted Rowboat render.

- [ ] **Step 7: Record verified evidence in `tasks/todo.md`**

Add a new `## Visual Recutt mit Full-frame + Blur-Fill [x]` section only after every required gate is green. Include commit IDs, test counts, exact live gate transitions, preserved Script/Voice versions, export ID/path, media probe summary and explicit non-claims (no push/merge/deployment; no secret logged).

- [ ] **Step 8: Check scope and commit evidence**

```bash
git diff --check
git status --short
git diff -- tasks/todo.md
git add tasks/todo.md
git commit -m "docs(tasks): record visual recut verification"
git status --short
```

Expected final status: clean worktree. Do not push or merge without a separate user request.
