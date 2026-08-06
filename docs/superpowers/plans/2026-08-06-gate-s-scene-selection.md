# Gate S — Szenen-Auswahl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ein Pflicht-Gate zwischen Transkript-Bestätigung und Script: Laura schlägt Szenen-Kandidaten vor, der User wählt per Kachel-Karte oder Chat, Storyline/Script sind strukturell auf die Auswahl beschränkt.

**Architecture:** Neues Board-Artefakt `scene_selection` als Ketten-Wurzel vor `storyline` (Gate-abhängig aktiv, damit Alt-Boards unverändert laufen). Der Vorschlag kommt vom Team-Tool `propose_scene_selection`, die Bestätigung ausschließlich vom Server (Confirm-Endpoint bzw. Chat-Tool `select_scenes` → derselbe Service). Guards sitzen an den Schreib-Tools, nicht im Prompt.

**Tech Stack:** Python 3.11 (pydantic v2, FastAPI), TypeScript/React (Tailwind), pytest, vitest.

**Spec:** `docs/superpowers/specs/2026-08-06-scene-selection-per-scene-voice-design.md` §4.

## Global Constraints

- Timeline-Invarianten: Ganzzahl-Frames, Ranges end-exclusive (`src_end_frame_exclusive`).
- Tool-Funktionen im Production-Toolset dürfen NIE raisen — Fehler als `{"ok": False, "reason": str(exc)[:200]}`.
- Strukturelle Guards statt Prompt-Verträgen (I2-Lektion): Ablehnung am Schreib-Tool.
- Alt-Boards (ohne `scene_selection.json`, `meta.scene_gate` fehlt/False) müssen unverändert laden, resumen, restoren und „done" erreichen.
- Python-Gates aus `services/local-api`: `uv run pytest` (NIE ein zusätzliches `-q` anhängen — addopts hat schon `-q`), `uv run mypy` (bare, deckt tests mit ab), `uv run ruff check .`.
- Frontend-Gates aus `apps/desktop`: `pnpm typecheck`, `pnpm test`, `pnpm build` (CI-Job „desktop" fährt vitest mit!).
- Git: explizite `git add <pfade>` (nie `-A`), Conventional Commits, Commit-Message endet mit `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- Der Confirm-Schreiber ist der Server; Agenten-Tools schreiben `confirmed_utc` NIEMALS.
- Jeder String-Leser eines neuen Tool-Namens im Frontend wird explizit mitgezogen („zweiter Leser"-Bug-Klasse: `deriveTarget`, ActionCard-Dispatch).

---

### Task GS1: SceneSelection-Modelle + Gate-abhängige Ketten-Wurzel

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board_models.py` (nach `SceneWindowRef`/`as_scene_window`, vor `Chapter`; plus `BoardMeta`)
- Modify: `services/local-api/src/laura/short_creator/board.py` (`_CHAIN` :90, `_SINGLETONS` :151, `resume_point` :514, `restore_coherent_suffix` :402, `status()` :537, Imports)
- Test: `services/local-api/tests/test_board_scene_selection.py` (neu)

**Interfaces:**
- Consumes: bestehende `content_hash`, `_parents_stale`, `Board.save/load/invalidate`.
- Produces: `SceneCandidate`, `SceneSelection` (board_models), `"scene_selection"` in `_CHAIN`/`_SINGLETONS`, `BoardMeta.scene_gate: bool = False`, `Storyline.parents: dict[str, str]`, `Board._active_chain()`, `status()["scene_gate"]`-Block. GS2–GS4 verlassen sich auf exakt diese Namen.

- [ ] **Step 1: Failing Tests schreiben**

```python
"""Gate-S board mechanics: scene_selection as gate-dependent chain root."""

from laura.short_creator.board import Board, downstream_of
from laura.short_creator.board_models import (
    BoardMeta,
    SceneCandidate,
    SceneSelection,
    Storyline,
    Chapter,
)


def _meta(scene_gate: bool) -> BoardMeta:
    return BoardMeta(
        session_id="s1",
        asset_id="a1",
        created_utc="2026-08-06T00:00:00Z",
        task="test",
        target_seconds=60.0,
        script_gate=True,
        scene_gate=scene_gate,
    )


def _selection(confirmed: bool) -> SceneSelection:
    return SceneSelection(
        candidates=[
            SceneCandidate(
                scene_number=2,
                src_start_frame=0,
                src_end_frame_exclusive=300,
                thumb_frame=150,
                description="screen recording of n8n",
                transcript_snippet="wir bauen den flow",
                rationale="starker hook",
                recommended=True,
            )
        ],
        selected_scene_numbers=[2] if confirmed else [],
        confirmed_utc="2026-08-06T00:01:00Z" if confirmed else None,
    )


def test_downstream_of_scene_selection_is_whole_rest() -> None:
    assert downstream_of("scene_selection") == (
        "storyline", "script", "voice", "cutlist",
        "contact_sheet", "render_report", "qa_report",
    )


def test_resume_point_gate_on_requires_selection(tmp_path) -> None:
    board = Board.create(tmp_path / "b", _meta(scene_gate=True))
    assert board.resume_point([]) == "scene_selection"
    board.save("scene_selection", _selection(confirmed=False))
    # present but UNCONFIRMED still parks the run at the gate
    assert board.resume_point([]) == "scene_selection"
    board.save("scene_selection", _selection(confirmed=True))
    assert board.resume_point([]) == "storyline"


def test_resume_point_gate_off_skips_scene_selection(tmp_path) -> None:
    board = Board.create(tmp_path / "b", _meta(scene_gate=False))
    assert board.resume_point([]) == "storyline"


def test_save_selection_invalidates_storyline(tmp_path) -> None:
    board = Board.create(tmp_path / "b", _meta(scene_gate=True))
    board.save("scene_selection", _selection(confirmed=True))
    board.save(
        "storyline",
        Storyline(
            red_thread="x",
            arc=[Chapter(chapter=1, role="hook", message="m",
                         scene_numbers=[2], target_seconds=10.0)],
        ),
    )
    board.save("scene_selection", _selection(confirmed=True).model_copy(
        update={"selected_scene_numbers": [2], "confirmed_utc": "2026-08-06T00:09:00Z"}
    ))
    assert board.load("storyline") is None


def test_restore_brings_back_storyline_matching_selection(tmp_path) -> None:
    from laura.short_creator.board_models import content_hash

    board = Board.create(tmp_path / "b", _meta(scene_gate=True))
    selection = _selection(confirmed=True)
    board.save("scene_selection", selection)
    board.save(
        "storyline",
        Storyline(
            red_thread="x",
            arc=[Chapter(chapter=1, role="hook", message="m",
                         scene_numbers=[2], target_seconds=10.0)],
            parents={"scene_selection": content_hash(board.load("scene_selection"))},
        ),
    )
    board.invalidate("scene_selection")  # archives + removes storyline, selection stays
    assert board.load("storyline") is None
    restored = board.restore_coherent_suffix()
    assert "storyline" in restored


def test_status_reports_scene_gate_block(tmp_path) -> None:
    board = Board.create(tmp_path / "b", _meta(scene_gate=True))
    status = board.status()
    assert status["scene_gate"] == {"enabled": True, "pending": False, "confirmed": False}
    board.save("scene_selection", _selection(confirmed=False))
    status = board.status()
    assert status["scene_gate"]["pending"] is True
    assert status["scene_gate"]["candidates"][0]["scene_number"] == 2
    assert status["scene_gate"]["recommended"] == [2]
    board.save("scene_selection", _selection(confirmed=True))
    status = board.status()
    assert status["scene_gate"]["confirmed"] is True
    assert status["scene_gate"]["selected"] == [2]
```

- [ ] **Step 2: Fail verifizieren**

Run: `uv run pytest tests/test_board_scene_selection.py`
Expected: FAIL — `ImportError: cannot import name 'SceneCandidate'`.

- [ ] **Step 3: Modelle in board_models.py**

Nach `as_scene_window` einfügen:

```python
class SceneCandidate(BaseModel):
    """One proposed scene for the user's Gate-S pick (spec 2026-08-06 §4.1)."""

    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1)
    src_start_frame: int = Field(ge=0)
    src_end_frame_exclusive: int
    thumb_frame: int = Field(ge=0)  # scene middle; frontend renders it via assetFrameUrl
    description: str  # VLM view; "(keine Bildanalyse verfügbar)" when degraded
    transcript_snippet: str = Field(min_length=1)
    rationale: str
    recommended: bool = False

    @model_validator(mode="after")
    def _frames_end_exclusive(self) -> SceneCandidate:
        if self.src_end_frame_exclusive <= self.src_start_frame:
            raise ValueError("src_end_frame_exclusive must be > src_start_frame")
        if not (self.src_start_frame <= self.thumb_frame < self.src_end_frame_exclusive):
            raise ValueError("thumb_frame must lie inside the scene's frame range")
        return self


class SceneSelection(BaseModel):
    """Gate-S root artifact: the proposal (agent-written) plus the user's confirmed pick
    (server-written ONLY — no agent tool ever sets ``confirmed_utc``)."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(default=1, ge=1)
    candidates: list[SceneCandidate] = Field(min_length=1)
    selected_scene_numbers: list[int] = Field(default_factory=list)
    confirmed_utc: str | None = None
    parents: dict[str, str] = Field(default_factory=dict)  # chain root: stays empty

    @model_validator(mode="after")
    def _selection_consistent(self) -> SceneSelection:
        pool = {c.scene_number for c in self.candidates}
        if len(pool) != len(self.candidates):
            raise ValueError("duplicate candidate scene_numbers")
        stray = sorted(set(self.selected_scene_numbers) - pool)
        if stray:
            raise ValueError(f"selected scenes not among candidates: {stray}")
        if self.confirmed_utc is not None and not self.selected_scene_numbers:
            raise ValueError("a confirmed selection must select at least one scene")
        return self
```

In `Storyline` ergänzen (nach `arc`):

```python
    # Which parent artifact instances this was built from (Gate-S boards stamp
    # {"scene_selection": hash}); empty = gate-off or pre-Gate-S board.
    parents: dict[str, str] = Field(default_factory=dict)
```

In `BoardMeta` nach `script_gate` ergänzen:

```python
    # Gate S (scene selection, 2026-08-06): when True, save_storyline/save_script_chapter
    # refuse until the user confirmed a scene selection. Default False so every meta.json
    # written before this gate loads unchanged; only NEW auto-short sessions turn it on.
    scene_gate: bool = False
```

- [ ] **Step 4: board.py — Kette + Gate-Logik**

`_CHAIN` (:90) wird:

```python
_CHAIN: tuple[str, ...] = (
    "scene_selection",
    "storyline",
    "script",
    "voice",
    "cutlist",
    "contact_sheet",
    "render_report",
    "qa_report",
)
```

`_SINGLETONS` (:151) um `"scene_selection": SceneSelection,` ergänzen (Import aus board_models mitziehen; `Storyline` ist schon importiert).

Neuen Helper direkt über `resume_point` einfügen und in `resume_point` UND `restore_coherent_suffix` verwenden — Alt-Boards (gate off) dürfen `scene_selection` nie als fehlendes Glied sehen:

```python
    def _active_chain(self) -> tuple[str, ...]:
        """The chain THIS board actually walks: gate-off boards (every board written
        before Gate S) skip the scene_selection root — otherwise no old board could
        ever read "done" again and restore_coherent_suffix would stop at link one."""
        return _CHAIN if self.meta().scene_gate else _CHAIN[1:]
```

In `resume_point` (:531) die Schleife auf `self._active_chain()` umstellen und davor den Unbestätigt-Fall einbauen:

```python
        with self._lock:
            if self.meta().scene_gate:
                selection = self.load("scene_selection")
                if not isinstance(selection, SceneSelection) or selection.confirmed_utc is None:
                    # present-but-unconfirmed parks the run at the gate: the proposal
                    # exists, the user has not picked yet — a team turn now would only
                    # run into save_storyline's refusal.
                    return "scene_selection"
            for name in self._active_chain():
                if self.load(name) is None:
                    return name
            return "done"
```

In `restore_coherent_suffix` (:419) `for name in _CHAIN:` → `for name in self._active_chain():`.

In `status()` (:558) `for name in _CHAIN:` unverändert lassen (generischer Artefakt-Block ist harmlos), aber vor `result` den Gate-Block berechnen und in `result` aufnehmen (nach `"script_gate": script_gate,`):

```python
        selection = self.load("scene_selection")
        scene_gate: dict[str, Any] = {
            "enabled": meta.scene_gate,
            "pending": (
                meta.scene_gate
                and isinstance(selection, SceneSelection)
                and selection.confirmed_utc is None
            ),
            "confirmed": (
                isinstance(selection, SceneSelection) and selection.confirmed_utc is not None
            ),
        }
        if isinstance(selection, SceneSelection):
            scene_gate["candidates"] = [c.model_dump() for c in selection.candidates]
            scene_gate["recommended"] = [
                c.scene_number for c in selection.candidates if c.recommended
            ]
            scene_gate["selected"] = list(selection.selected_scene_numbers)
```

und `"scene_gate": scene_gate,` ins `result`-Dict.

- [ ] **Step 5: Tests grün + Regression**

Run: `uv run pytest tests/test_board_scene_selection.py tests/test_board.py`
Expected: alle PASS (bestehende Board-Tests beweisen die Alt-Board-Verträglichkeit; falls ein Alt-Test jetzt `scene_selection` im `artifacts`-Dict sieht und auf exakte Keys prüft, den Test um den neuen Key erweitern — Verhalten, nicht Snapshot).

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/short_creator/board_models.py services/local-api/src/laura/short_creator/board.py services/local-api/tests/test_board_scene_selection.py
git commit -m "feat(short-creator): scene_selection artifact as gate-dependent chain root"
```

---

### Task GS2: propose_scene_selection-Tool + strukturelle Guards

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (`save_storyline` :1648, `save_script_chapter` :1866; neues Tool daneben; Tool-Registrierung dort, wo `review_scene`/`save_storyline` gelistet sind — `tool_names`-Roster in `production_agents.py` mitprüfen)
- Test: `services/local-api/tests/test_production_tools_scene_gate.py` (neu; Fixtures/Kontextaufbau aus dem bestehenden `save_storyline`-Test in `tests/test_production_tools.py` übernehmen — gleiche Faktur, nur `scene_gate=True` im Meta)

**Interfaces:**
- Consumes: `SceneSelection`, `SceneCandidate`, `BoardMeta.scene_gate` (GS1); `_resolve_scene(db, asset_id, scene_number)`; `get_scene_transcript`-Maschinerie (:1354) für Snippets; `_content_hash`.
- Produces: Tool `propose_scene_selection(candidates: list[dict]) -> dict`; Pure-Helper `scene_selection_block_reason(selection, refs, gate_on) -> str | None`; `save_storyline` stempelt `parents={"scene_selection": ...}` bei aktivem Gate.

- [ ] **Step 1: Failing Test für den Pure-Guard**

```python
from laura.short_creator.production_tools import scene_selection_block_reason
from laura.short_creator.board_models import SceneCandidate, SceneSelection


def _sel(selected: list[int], confirmed: bool) -> SceneSelection:
    return SceneSelection(
        candidates=[
            SceneCandidate(
                scene_number=n, src_start_frame=0, src_end_frame_exclusive=100,
                thumb_frame=50, description="d", transcript_snippet="t",
                rationale="r", recommended=True,
            )
            for n in (2, 5, 7)
        ],
        selected_scene_numbers=selected if confirmed else [],
        confirmed_utc="2026-08-06T00:00:00Z" if confirmed else None,
    )


def test_gate_off_never_blocks() -> None:
    assert scene_selection_block_reason(None, [1, 2], gate_on=False) is None


def test_gate_on_without_selection_blocks() -> None:
    reason = scene_selection_block_reason(None, [2], gate_on=True)
    assert reason is not None and "propose_scene_selection" in reason


def test_gate_on_unconfirmed_blocks() -> None:
    reason = scene_selection_block_reason(_sel([], confirmed=False), [2], gate_on=True)
    assert reason is not None and "awaiting" in reason


def test_gate_on_outside_selection_names_offenders() -> None:
    reason = scene_selection_block_reason(_sel([2, 5], confirmed=True), [2, 9], gate_on=True)
    assert reason is not None and "9" in reason and "[2, 5]" in reason


def test_gate_on_subset_passes() -> None:
    assert scene_selection_block_reason(_sel([2, 5], confirmed=True), [5], gate_on=True) is None
```

- [ ] **Step 2: Fail verifizieren**

Run: `uv run pytest tests/test_production_tools_scene_gate.py`
Expected: FAIL — `ImportError: cannot import name 'scene_selection_block_reason'`.

- [ ] **Step 3: Pure-Guard auf Modul-Ebene von production_tools.py**

```python
def scene_selection_block_reason(
    selection: SceneSelection | None, referenced_scenes: list[int], *, gate_on: bool
) -> str | None:
    """Why a storyline/script write must be refused under Gate S — or None to proceed.

    Structural, not prompt (I2 lesson): the contract that only USER-picked scenes may be
    written lives at the write site.
    """
    if not gate_on:
        return None
    if not isinstance(selection, SceneSelection):
        return (
            "scene selection gate: no proposal on the board yet — call "
            "propose_scene_selection first; the user then confirms in chat"
        )
    if selection.confirmed_utc is None:
        return (
            "scene selection gate: awaiting the user's pick — the user must confirm "
            "the scene selection in chat before storyline or script are written"
        )
    allowed = set(selection.selected_scene_numbers)
    stray = sorted(set(referenced_scenes) - allowed)
    if stray:
        return (
            f"scenes {stray} are outside the user's confirmed selection "
            f"{sorted(allowed)} — only selected scenes may be used"
        )
    return None
```

(`gate_on` als keyword-only, damit Aufrufer lesbar bleiben.)

- [ ] **Step 4: Guards einbauen**

In `save_storyline` direkt nach der Fenster-Validierung (nach dem `bad_refs`-Block, :1687) und VOR `old_storyline = board.load("storyline")`:

```python
            meta = board.meta()
            selection = board.load("scene_selection")
            selection = selection if isinstance(selection, SceneSelection) else None
            block = scene_selection_block_reason(
                selection, [s for s, _w in refs], gate_on=meta.scene_gate
            )
            if block is not None:
                return {"ok": False, "reason": block}
```

Beim Bau der Storyline die Parents stempeln — der `Storyline(...)`-Konstruktoraufruf (:1661) wird:

```python
                storyline = Storyline(
                    red_thread=red_thread,
                    arc=[Chapter(**c) for c in numbered_chapters(chapters)],
                )
```
→ nach dem Guard-Block (Selection ist dort geladen) vor `board.save("storyline", ...)`:

```python
            if meta.scene_gate and selection is not None:
                storyline = storyline.model_copy(
                    update={"parents": {"scene_selection": _content_hash(selection)}}
                )
```

In `save_script_chapter` innerhalb der Transaktion, direkt nach dem Storyline-Guard (:1895):

```python
                meta_for_gate = board.meta()
                selection_for_gate = board.load("scene_selection")
                selection_for_gate = (
                    selection_for_gate
                    if isinstance(selection_for_gate, SceneSelection)
                    else None
                )
                block = scene_selection_block_reason(
                    selection_for_gate,
                    [int(line.get("scene_number", 0)) for line in lines],
                    gate_on=meta_for_gate.scene_gate,
                )
                if block is not None:
                    return {"ok": False, "reason": block}
```

- [ ] **Step 5: propose_scene_selection-Tool**

Neben `save_storyline` (gleiches Closure-Muster, `board`/`db`/`asset_id` aus dem umgebenden Scope):

```python
    def propose_scene_selection(candidates: list[dict[str, Any]]) -> dict[str, Any]:
        """Propose scene candidates for the user's Gate-S pick and save them to the board.
        Each candidate: {"scene_number", "description", "transcript_snippet", "rationale",
        "recommended"} — frame range and thumb frame are resolved server-side from the scene
        itself, so pass only what you judged. At least one candidate must be recommended
        (the pre-checked suggestion). Saving a new proposal archives the old one and
        invalidates everything downstream; the run then STOPS and waits for the user."""
        try:
            built: list[SceneCandidate] = []
            for cand in candidates:
                scene_number = int(cand.get("scene_number", 0))
                resolved = _resolve_scene(db, asset_id, scene_number)
                if resolved is None:
                    return {"ok": False, "reason": f"scene {scene_number} does not exist"}
                src_start, src_end, _text = resolved
                built.append(
                    SceneCandidate(
                        scene_number=scene_number,
                        src_start_frame=src_start,
                        src_end_frame_exclusive=src_end,
                        thumb_frame=src_start + (src_end - src_start) // 2,
                        description=str(cand.get("description") or "").strip()
                        or "(keine Bildanalyse verfügbar)",
                        transcript_snippet=str(
                            cand.get("transcript_snippet") or ""
                        ).strip(),
                        rationale=str(cand.get("rationale") or "").strip(),
                        recommended=bool(cand.get("recommended", False)),
                    )
                )
            try:
                selection = SceneSelection(candidates=built)
            except ValidationError as exc:
                return {"ok": False, "errors": _validation_errors(exc)}
            if not any(c.recommended for c in built):
                return {
                    "ok": False,
                    "reason": "mark at least one candidate recommended — it is the "
                    "pre-checked suggestion the user confirms with one click",
                }
            version = board.save("scene_selection", selection)
            return {
                "ok": True,
                "version": version,
                "candidates": len(built),
                "note": (
                    "proposal saved — STOP now. The user picks scenes in chat; the run "
                    "resumes automatically after their confirmation."
                ),
            }
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}
```

Ein leerer `transcript_snippet` scheitert an der Modell-Validierung (`min_length=1`) und landet als Feld-Fehler beim Agenten — gewollt: das Snippet trägt die Auswahl-Info, wenn das VLM degraded ist.

Registrierung: das Tool in dieselbe Tool-Liste aufnehmen, in der `review_scene` und `save_storyline` stehen (Rückgabe-Dict des Tool-Builders in production_tools.py; danach `grep -n "save_storyline" services/local-api/src/laura/short_creator/production_agents.py` — jede Agenten-Roster-Liste, die `save_storyline` führt, bekommt `propose_scene_selection` daneben).

- [ ] **Step 6: Tool-Level-Tests ergänzen**

Im neuen Testmodul, mit der Fixture-Faktur des bestehenden `save_storyline`-Tests (Board mit `scene_gate=True` im Meta, echte Szenen in der Test-DB):

```python
def test_save_storyline_refused_until_confirmed(...):
    # build tools context with scene_gate=True, no scene_selection on the board
    result = tools["save_storyline"]("thread", [{"role": "hook", "message": "m",
                                                 "scene_numbers": [1], "target_seconds": 10}])
    assert result["ok"] is False and "propose_scene_selection" in result["reason"]

def test_propose_then_confirm_then_storyline_stamps_parent(...):
    tools["propose_scene_selection"]([{"scene_number": 1, "description": "d",
                                       "transcript_snippet": "t", "rationale": "r",
                                       "recommended": True}])
    # confirm server-side, as GS4's service will: load, stamp selected + confirmed_utc, save
    selection = board.load("scene_selection")
    board.save("scene_selection", selection.model_copy(update={
        "selected_scene_numbers": [1], "confirmed_utc": "2026-08-06T00:00:00Z"}))
    result = tools["save_storyline"]("thread", [{"role": "hook", "message": "m",
                                                 "scene_numbers": [1], "target_seconds": 10}])
    assert result["ok"] is True
    storyline = board.load("storyline")
    assert "scene_selection" in storyline.parents

def test_save_script_chapter_rejects_unselected_scene(...):
    # selection confirmed for [1]; a line for scene 2 must be refused with "outside"
    ...
```

(`...`-Stellen = Fixture-Aufbau nach dem Muster des Nachbar-Tests — der Testautor übernimmt dessen Context-Builder wörtlich.)

- [ ] **Step 7: Grün + Commit**

Run: `uv run pytest tests/test_production_tools_scene_gate.py tests/test_production_tools.py`
Expected: PASS.

```bash
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/src/laura/short_creator/production_agents.py services/local-api/tests/test_production_tools_scene_gate.py
git commit -m "feat(short-creator): propose_scene_selection tool + structural Gate-S guards"
```

---

### Task GS3: Orchestrator-Pause + Charter + Session-Erzeugung

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py` (Early-Exit nach dem Done-Short-Circuit :646-660; Charter-Abschnitt in `build_production_task` :158-210)
- Modify: `services/local-api/src/laura/api/short_creator.py` (Session-Erzeugung: dort, wo `script_gate=True` für neue Auto-Short-Sessions gesetzt wird — `scene_gate=True` daneben)
- Test: `services/local-api/tests/test_production_orchestrator_scene_gate.py` (neu; Faktur nach den bestehenden run_production-Tests mit injiziertem `execute`)

**Interfaces:**
- Consumes: `Board.resume_point` liefert `"scene_selection"` bei fehlendem ODER unbestätigtem Vorschlag (GS1); `SceneSelection`.
- Produces: `run_production` endet ohne Team-Turn mit `summary="awaiting user scene selection"`, `resume_point="scene_selection"`, Board-Status bleibt `"active"`. GS4's Resume läuft danach normal weiter.

- [ ] **Step 1: Failing Test**

```python
def test_run_awaiting_selection_never_spawns_team(tmp_path, ...):
    """Proposal on the board, unconfirmed, no message -> no execute call, awaiting summary."""
    calls: list[str] = []
    def fake_execute(*a, **k):
        calls.append("x")
        raise AssertionError("team must not run while the scene gate is open")
    result = run_production(..., execute=fake_execute, message=None)
    assert calls == []
    assert result["resume_point"] == "scene_selection"
    assert "scene selection" in result["summary"]
```

(Aufbau: Board mit `scene_gate=True`, gespeicherter unbestätigter `SceneSelection` — Faktur des bestehenden Done-Short-Circuit-Tests übernehmen.)

- [ ] **Step 2: Fail verifizieren**

Run: `uv run pytest tests/test_production_orchestrator_scene_gate.py`
Expected: FAIL (execute wird heute aufgerufen bzw. summary passt nicht).

- [ ] **Step 3: Early-Exit in run_production**

Direkt NACH dem Done-Short-Circuit (:660) und VOR dem deterministic_eligible-Block:

```python
    # Gate S (spec 2026-08-06): a proposal is on the board and the user has not picked yet.
    # A team turn now would only run into save_storyline's structural refusal — so a plain
    # resume parks instead of spending an LLM run. A follow-up MESSAGE still goes through
    # (the user may be adjusting the proposal in chat via the team).
    if (
        message is None
        and board.meta().scene_gate
        and isinstance(board.load("scene_selection"), SceneSelection)
        and board.resume_point(expected_scenes) == "scene_selection"
    ):
        return _completed_result(
            board,
            session_id=session_id,
            restored=restored,
            status="ok",
            stage="A",
            team="magentic",
            weak=_qa_weak(board),
            escalated=False,
            summary="awaiting user scene selection — pick scenes in chat to continue",
            export_id=_export_id_of(board),
            resume_point="scene_selection",
        )
```

(Import `SceneSelection` aus board_models ergänzen. Board-Status bewusst NICHT auf failed/complete — der Lauf ist gesund geparkt.)

- [ ] **Step 4: Charter-Abschnitt in build_production_task**

Im Task-Text (bei den Resume-/Phasen-Zeilen um :203) einen Gate-S-Absatz ergänzen, der nur bei `meta.scene_gate` erscheint:

```python
    if meta.scene_gate:
        gate_s_lines = (
            "SCENE SELECTION GATE (mandatory):\n"
            "1. Review every expected scene (review_scene) BEFORE proposing.\n"
            "2. Call propose_scene_selection with 4-8 candidates that fit the task —\n"
            "   description = what the scene SHOWS, transcript_snippet = what is SAID\n"
            "   (from get_scene_transcript), rationale = why it belongs in this film.\n"
            "   Mark your suggested subset recommended.\n"
            "3. Then STOP. Do not write a storyline or script — save_storyline refuses\n"
            "   until the user confirmed the selection in chat.\n"
            "4. After confirmation, use ONLY the selected scenes.\n"
        )
```

und in den zusammengesetzten Task-String direkt nach der Resume-Point-Zeile einfügen.

- [ ] **Step 5: scene_gate für neue Sessions**

In `services/local-api/src/laura/api/short_creator.py`: `grep -n "script_gate" src/laura/api/short_creator.py` — an der Stelle, die für neue Auto-Short-Sessions `script_gate=True` übergibt (Exploration: um :403/:576), zusätzlich `scene_gate=True` durchreichen; `run_production`s Signatur/BoardMeta-Bau in production_orchestrator.py (:610-614) nimmt den neuen Parameter analog zu `script_gate` entgegen (Default `False`, damit v1-Aufrufer und Overview unberührt bleiben).

- [ ] **Step 6: Grün + Commit**

Run: `uv run pytest tests/test_production_orchestrator_scene_gate.py tests/test_production_orchestrator.py`
Expected: PASS.

```bash
git add services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/src/laura/api/short_creator.py services/local-api/tests/test_production_orchestrator_scene_gate.py
git commit -m "feat(short-creator): Gate-S pause, charter section, scene_gate on new sessions"
```

---

### Task GS4: Confirm-Service + Endpoint + Router-Tool select_scenes

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py` (Service `confirm_scene_selection` + `POST /production/{session_id}/scene-selection:confirm` neben dem `/message`-Endpoint :944)
- Modify: `services/local-api/src/laura/chat/router.py` (`TOOLS` + `_SYSTEM_PROMPT`-Regeln + Validierung)
- Modify: `services/local-api/src/laura/chat/executor.py` (`_handle_select_scenes` + Dispatch)
- Modify: `services/local-api/src/laura/chat/context.py` bzw. `compose_context`-Modul (Vorschlags-Zeile; `grep -n "def compose_context" src/laura/chat`)
- Test: `services/local-api/tests/test_api_scene_selection.py` (neu), Erweiterungen in `tests/test_chat_router.py` + `tests/test_chat_executor.py`

**Interfaces:**
- Consumes: `SceneSelection` (GS1), `run_production_resume` (:935), `_production_job_busy`-Busy-Guard (Muster aus `_handle_approve_script`, executor.py:453-569), `_open_board_or_404`.
- Produces: `confirm_scene_selection(db, session_id, scene_numbers) -> dict` (Service, von Endpoint UND Executor benutzt); Router-Tool `{"tool": "select_scenes", "args": {"scene_numbers": [int, ...]}}`; Executor-Karte mit `content.tool = "select_scenes"`.

- [ ] **Step 1: Failing Service-Tests**

```python
def test_confirm_happy_path_enqueues_resume(...):
    out = confirm_scene_selection(db, session_id, [2, 5])
    sel = board.load("scene_selection")
    assert sel.selected_scene_numbers == [2, 5] and sel.confirmed_utc is not None
    assert out["job_id"]  # resume enqueued

def test_confirm_rejects_stray_scene(...):
    with pytest.raises(HTTPException) as exc:
        confirm_scene_selection(db, session_id, [99])
    assert exc.value.status_code == 422

def test_confirm_rejects_empty(...):
    with pytest.raises(HTTPException) as exc:
        confirm_scene_selection(db, session_id, [])
    assert exc.value.status_code == 422

def test_reconfirm_same_set_is_noop(...):
    confirm_scene_selection(db, session_id, [2])
    v1 = board.load("scene_selection").version
    out = confirm_scene_selection(db, session_id, [2])
    assert out.get("already_current") is True
    assert board.load("scene_selection").version == v1

def test_confirm_busy_returns_409(...):
    # running production job on the session -> 409, selection untouched
    ...
```

- [ ] **Step 2: Fail verifizieren**

Run: `uv run pytest tests/test_api_scene_selection.py`
Expected: FAIL — `confirm_scene_selection` existiert nicht.

- [ ] **Step 3: Service + Endpoint**

In `api/short_creator.py` neben `run_production_resume`:

```python
def confirm_scene_selection(
    db: Database, session_id: str, scene_numbers: list[int]
) -> dict[str, Any]:
    """Server-side Gate-S confirmation (spec 2026-08-06 §4.4): stamps the user's pick on
    the scene_selection artifact and enqueues the resume run. The ONLY writer of
    ``confirmed_utc`` — chat and HTTP both land here."""
    session = repos.get_production_session(db, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found")
    board = _open_board_or_404(db, str(session["asset_id"]), session_id)
    if not board.meta().scene_gate:
        raise HTTPException(status.HTTP_409_CONFLICT, "scene gate is not enabled")
    selection = board.load("scene_selection")
    if not isinstance(selection, SceneSelection):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "no scene proposal on the board yet"
        )
    picked = sorted(set(int(n) for n in scene_numbers))
    if not picked:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "pick at least one scene")
    pool = {c.scene_number for c in selection.candidates}
    stray = sorted(set(picked) - pool)
    if stray:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"scenes {stray} are not among the proposed candidates",
        )
    if _production_job_busy(db, session_id):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "a production run is in progress — wait for it before changing the selection",
        )
    if selection.confirmed_utc is not None and selection.selected_scene_numbers == picked:
        # Idempotent re-confirm: a fresh timestamp would bump the version and wipe a
        # perfectly valid storyline downstream for nothing.
        return {"session_id": session_id, "already_current": True}
    board.save(
        "scene_selection",
        selection.model_copy(
            update={"selected_scene_numbers": picked, "confirmed_utc": _utc_now_iso()}
        ),
    )
    return confirm_result | run_production_resume(db, session_id)  # see note below
```

Hinweis Rückgabe: `confirm_result = {"session_id": session_id, "selected": picked}` als lokale Variable vor dem Save; die letzte Zeile ist `return {**confirm_result, **run_production_resume(db, session_id)}`. `_utc_now_iso` = die im Modul bereits benutzte UTC-Helper-Funktion (`grep -n "utcnow\|now_utc\|_iso" src/laura/api/short_creator.py` und dieselbe verwenden; falls keine existiert: `datetime.now(timezone.utc).isoformat(timespec="seconds")`).

Endpoint daneben:

```python
class SceneSelectionConfirmRequest(BaseModel):
    scene_numbers: list[int]


@router.post(
    "/production/{session_id}/scene-selection:confirm",
    status_code=status.HTTP_202_ACCEPTED,
)
def confirm_scene_selection_endpoint(
    session_id: str,
    body: SceneSelectionConfirmRequest,
    request: Request,
    principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
) -> dict[str, Any]:
    """Confirm the user's Gate-S scene pick. See :func:`confirm_scene_selection`."""
    return confirm_scene_selection(_db(request), session_id, body.scene_numbers)
```

- [ ] **Step 4: Router-Tool**

In `chat/router.py`: `"select_scenes"` in `TOOLS` aufnehmen. Args-Validierung nach dem Muster der bestehenden `_validate_*`-Helfer: `scene_numbers` muss eine nicht-leere Liste von ints ≥ 1 sein. `_SYSTEM_PROMPT` bekommt (bei den Produktions-Regeln):

```
- select_scenes: der User wählt Szenen für den Szenen-Vorschlag (Gate S). Nur wenn der
  Kontext eine Zeile "Szenen-Vorschlag offen" zeigt. args.scene_numbers ist die KOMPLETTE
  gewünschte Auswahl. Relativ-Anweisungen gegen die Empfehlung auflösen: bei Empfehlung
  [2, 4, 5] heißt "nimm 2 und 5 statt 4" -> [2, 5]; "passt so" / "nimm deine Auswahl"
  -> die Empfehlung unverändert.
```

Beispiele in den Prompt-Beispielblock aufnehmen („passt so" → select_scenes mit der Empfehlungsliste; „nimm 2 und 5 statt 4" → [2, 5]).

In `compose_context` (Active-Session-Block): wenn der Produktions-Status `scene_gate.pending` meldet, eine Zeile anhängen:

```python
lines.append(
    f"Szenen-Vorschlag offen: empfohlen {recommended} von Kandidaten {candidates}"
)
```

(`recommended`/`candidates` aus `status["scene_gate"]["recommended"]` / Kandidaten-`scene_number`-Liste.)

- [ ] **Step 5: Executor-Handler**

In `chat/executor.py` neben `_handle_approve_script` (dessen Busy-/Fehler-Faktur übernehmen):

```python
def _handle_select_scenes(...):  # same signature family as _handle_approve_script
    ...
    numbers = [int(n) for n in args.get("scene_numbers", [])]
    try:
        out = confirm_scene_selection(db, session_id, numbers)
    except HTTPException as exc:
        return _error_card("select_scenes", str(exc.detail))
    picked = out.get("selected") or numbers
    text = (
        f"Szenen übernommen: {picked}. Die Produktion läuft mit deiner Auswahl weiter."
        if not out.get("already_current")
        else f"Auswahl unverändert ({picked}) — nichts zu tun."
    )
    return _action_card(tool="select_scenes", text=text, ...)
```

(Die konkreten Karten-/Rückgabe-Helfer des Moduls verwenden — dieselben, die `_handle_approve_script` benutzt; der Dispatch-Match bei executor.py:1031-1041 bekommt den neuen Tool-Namen.)

Router-Tests: „passt so" bei offenem Vorschlag → `select_scenes` mit Empfehlungsliste; „nimm 2 und 5 statt 4" → `[2, 5]`; ohne offenen Vorschlag darf der Router NICHT select_scenes wählen (clarify/discuss). Executor-Tests: happy path (confirm + Karte), 422-Fehlertext landet in der Karte, busy → Fehlertext.

- [ ] **Step 6: Grün + Commit**

Run: `uv run pytest tests/test_api_scene_selection.py tests/test_chat_router.py tests/test_chat_executor.py`
Expected: PASS.

```bash
git add services/local-api/src/laura/api/short_creator.py services/local-api/src/laura/chat/router.py services/local-api/src/laura/chat/executor.py services/local-api/tests/test_api_scene_selection.py services/local-api/tests/test_chat_router.py services/local-api/tests/test_chat_executor.py
git commit -m "feat(chat): Gate-S confirm service, endpoint and select_scenes chat path"
```

(Liegt `compose_context` in einer weiteren Datei, diese explizit mit adden.)

---

### Task GS5: api.ts-Typen + SceneSelectionCard + volle Gates

**Files:**
- Modify: `apps/desktop/src/api.ts` (ProductionStatus-Typen um `scene_gate`; `confirmSceneSelection`)
- Create: `apps/desktop/src/components/chat/SceneSelectionCard.tsx`
- Modify: `apps/desktop/src/components/chat/ActionCard.tsx` (Gate-S-Zweig neben dem Gate-B-Zweig :372-384; Dispatch für `content.tool === "select_scenes"`)
- Modify: `apps/desktop/src/components/chat/ChatStage.tsx` (`deriveTarget` kennt `select_scenes`)
- Test: `apps/desktop/src/components/chat/SceneSelectionCard.test.tsx` (neu)

**Interfaces:**
- Consumes: `status.scene_gate` (GS1/GS3-Payload: `{enabled, pending, confirmed, candidates?, recommended?, selected?}`), `status.meta.asset_id`, `client.assetFrameUrl(assetId, frame)` (api.ts:1313), `POST /production/{sid}/scene-selection:confirm` (GS4).
- Produces: `client.confirmSceneSelection(sessionId: string, sceneNumbers: number[]): Promise<{...}>`, `<SceneSelectionCard status={...} sessionId={...} onConfirmed={...} />`.

- [ ] **Step 1: api.ts-Typen + Client-Methode**

```typescript
export interface SceneCandidate {
  scene_number: number;
  src_start_frame: number;
  src_end_frame_exclusive: number;
  thumb_frame: number;
  description: string;
  transcript_snippet: string;
  rationale: string;
  recommended: boolean;
}

export interface SceneGateStatus {
  enabled: boolean;
  pending: boolean;
  confirmed: boolean;
  candidates?: SceneCandidate[];
  recommended?: number[];
  selected?: number[];
}
```

`ProductionStatus` um `scene_gate?: SceneGateStatus;` erweitern (neben `script_gate`, api.ts:813). Client-Methode neben `revertProduction`:

```typescript
async confirmSceneSelection(
  sessionId: string,
  sceneNumbers: number[],
): Promise<{ session_id: string; job_id?: string; already_current?: boolean }> {
  return this.request(`/production/${sessionId}/scene-selection:confirm`, {
    method: "POST",
    body: JSON.stringify({ scene_numbers: sceneNumbers }),
  });
}
```

(Die tatsächliche interne Request-Helper-Methode des Clients verwenden — dieselbe wie bei den Nachbar-POSTs.)

- [ ] **Step 2: Failing Component-Test**

```tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { SceneSelectionCard } from "./SceneSelectionCard";

const gate = {
  enabled: true, pending: true, confirmed: false,
  recommended: [2],
  candidates: [
    { scene_number: 2, src_start_frame: 0, src_end_frame_exclusive: 300,
      thumb_frame: 150, description: "n8n Flow im Bild",
      transcript_snippet: "wir bauen den flow", rationale: "Hook", recommended: true },
    { scene_number: 5, src_start_frame: 300, src_end_frame_exclusive: 600,
      thumb_frame: 450, description: "Terminal", transcript_snippet: "deploy läuft",
      rationale: "Beweis", recommended: false },
  ],
};

it("preselects recommended tiles and confirms the toggled set", async () => {
  const confirm = vi.fn().mockResolvedValue({ session_id: "s1" });
  render(<SceneSelectionCard gate={gate} assetId="a1" sessionId="s1"
                             confirm={confirm} onConfirmed={() => {}} />);
  // recommended tile is pre-checked
  expect(screen.getByTestId("scene-tile-2")).toHaveAttribute("data-selected", "true");
  expect(screen.getByTestId("scene-tile-5")).toHaveAttribute("data-selected", "false");
  fireEvent.click(screen.getByTestId("scene-tile-5"));
  fireEvent.click(screen.getByRole("button", { name: /Auswahl übernehmen/ }));
  expect(confirm).toHaveBeenCalledWith("s1", [2, 5]);
});

it("refuses to confirm an empty selection", () => {
  const confirm = vi.fn();
  render(<SceneSelectionCard gate={gate} assetId="a1" sessionId="s1"
                             confirm={confirm} onConfirmed={() => {}} />);
  fireEvent.click(screen.getByTestId("scene-tile-2")); // deselect the only pick
  const button = screen.getByRole("button", { name: /Auswahl übernehmen/ });
  expect(button).toBeDisabled();
});
```

Run: `pnpm test SceneSelectionCard`
Expected: FAIL — Komponente existiert nicht.

- [ ] **Step 3: SceneSelectionCard implementieren**

```tsx
import { useMemo, useState } from "react";
import { client, type SceneGateStatus } from "../../api";

interface Props {
  gate: SceneGateStatus;
  assetId: string;
  sessionId: string;
  confirm?: (sessionId: string, sceneNumbers: number[]) => Promise<unknown>;
  onConfirmed: () => void;
}

/** Gate-S checkpoint: clickable candidate tiles, recommended pre-checked (spec 2026-08-06 §4.5). */
export function SceneSelectionCard({ gate, assetId, sessionId, confirm, onConfirmed }: Props) {
  const candidates = gate.candidates ?? [];
  const [selected, setSelected] = useState<Set<number>>(
    () => new Set(candidates.filter((c) => c.recommended).map((c) => c.scene_number)),
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const doConfirm = confirm ?? ((sid: string, nums: number[]) => client.confirmSceneSelection(sid, nums));
  const picked = useMemo(() => [...selected].sort((a, b) => a - b), [selected]);

  const toggle = (n: number) =>
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(n)) next.delete(n); else next.add(n);
      return next;
    });

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await doConfirm(sessionId, picked);
      onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Bestätigung fehlgeschlagen");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="rounded-lg border border-zinc-700 bg-zinc-900/60 p-3 space-y-3">
      <div className="text-sm font-medium">🎬 Szenen-Auswahl — Lauras Empfehlung ist vorausgewählt</div>
      <div className="grid grid-cols-2 gap-2">
        {candidates.map((c) => {
          const isOn = selected.has(c.scene_number);
          return (
            <button
              key={c.scene_number}
              type="button"
              data-testid={`scene-tile-${c.scene_number}`}
              data-selected={isOn ? "true" : "false"}
              onClick={() => toggle(c.scene_number)}
              className={`rounded-md border p-2 text-left transition ${
                isOn ? "border-emerald-500 bg-emerald-500/10" : "border-zinc-700 opacity-70"
              }`}
            >
              <img
                src={client.assetFrameUrl(assetId, c.thumb_frame)}
                alt={`Szene ${c.scene_number}`}
                className="mb-1 aspect-video w-full rounded object-cover"
              />
              <div className="text-xs font-semibold">Szene {c.scene_number}</div>
              <div className="text-xs text-zinc-400">{c.description}</div>
              <div className="text-xs italic text-zinc-500">„{c.transcript_snippet}"</div>
            </button>
          );
        })}
      </div>
      {error ? <div className="text-xs text-red-400">{error}</div> : null}
      <button
        type="button"
        disabled={busy || picked.length === 0}
        onClick={submit}
        className="rounded-md bg-emerald-600 px-3 py-1.5 text-sm font-medium disabled:opacity-50"
      >
        Auswahl übernehmen ({picked.length})
      </button>
    </div>
  );
}
```

Achtung `assetFrameUrl`: liefert der Client dort keinen direkten URL-String, sondern das Blob→ObjectURL-Muster (api.ts:1313-1322), dann dieselbe kleine `Thumb`-Hilfskomponente wie `SceneStrip.tsx:30-69` übernehmen statt `img src` direkt.

- [ ] **Step 4: Verdrahtung**

- `ActionCard.tsx`: im Produktions-Zweig neben dem Gate-B-Block (:372-384) rendern, wenn `status.scene_gate?.pending`: `<SceneSelectionCard gate={status.scene_gate} assetId={status.meta.asset_id} sessionId={sessionId} onConfirmed={refetchStatus} />` (dieselben Props-Quellen wie der Gate-B-Zweig; `refetchStatus` = der vorhandene Status-Refresh-Callback des Umfelds).
- Dispatch: `content.tool === "select_scenes"` rendert eine einfache Bestätigungszeile (Text aus der Karte des Executors) — NICHT in `UnknownActionLine` fallen lassen.
- `ChatStage.tsx` `deriveTarget`: `select_scenes` aufnehmen (gleiches Ziel wie `approve_script`).
- Suche nach weiteren String-Lesern: `grep -rn "approve_script" apps/desktop/src` — jede Stelle, die dort matcht, auf Notwendigkeit für `select_scenes` prüfen und nachziehen.

- [ ] **Step 5: Volle Gates**

Run (aus `apps/desktop`): `pnpm typecheck && pnpm test && pnpm build`
Run (aus `services/local-api`): `uv run pytest` und `uv run mypy` und `uv run ruff check .`
Expected: alles grün; Summary-Zeilen in den Report.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/api.ts apps/desktop/src/components/chat/SceneSelectionCard.tsx apps/desktop/src/components/chat/SceneSelectionCard.test.tsx apps/desktop/src/components/chat/ActionCard.tsx apps/desktop/src/components/chat/ChatStage.tsx
git commit -m "feat(desktop): SceneSelectionCard with clickable tiles for Gate S"
```

---

## Manuelle Prüfliste (nach Merge, Live-App)

1. Neue Auto-Short-Session starten → nach Transkript-Bestätigung erscheint die Kachel-Karte mit vorausgewählter Empfehlung.
2. Kachel abwählen/zuwählen → „Auswahl übernehmen" → Lauf geht weiter, Storyline nutzt nur gewählte Szenen.
3. Chat-Weg: „nimm 2 und 5 statt 4" bei offenem Vorschlag.
4. Alt-Session (vor Gate S) resumen → läuft unverändert ohne Szenen-Gate.
