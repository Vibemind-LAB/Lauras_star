# Fortsetzbare Rough-Cut-Szenenauswahl Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Eine offene Rough-Cut-Bildauswahl wird nach jeder Änderung lokal gespeichert, bleibt über App-/Backend-Neustarts und lange Pausen erhalten und kann über eine sichtbare offene Session sicher fortgesetzt werden.

**Architecture:** SQLite speichert einen revisionsgebundenen Visual-Selection-Draft pro Produktions-Session. Ein gemeinsamer Domain-Service prüft Proposal, Rough Cut, Voice/FPS und Quellmedium, bevor API, Confirm oder UI den Zustand verändern; Electron arbeitet mit einer serialisierten Compare-and-Swap-Autosave-Queue. Produktions-Session, ursprünglicher Brief und Chat werden dauerhaft verknüpft und über einen read-only Open-Sessions-Endpoint wieder auffindbar.

**Tech Stack:** Python 3.11, FastAPI, Pydantic v2, SQLite, pytest, mypy, Ruff, TypeScript strict, React 18, Electron, Vitest, Testing Library, Tailwind.

**Spec:** `docs/superpowers/specs/2026-08-17-resumable-visual-selection-design.md`

## Global Constraints

- Timeline-Zustand bleibt in Ganzzahl-Frames; Ranges bleiben end-exclusive.
- Autosave und Fortsetzen dürfen keinen LLM-Aufruf, Agentenlauf oder Produktionsjob erzeugen.
- Nur die finale Bildauswahl-Bestätigung darf die bestehende Resume-Pipeline anstoßen.
- Visual-v1-, Scene-Selection- und Gate-off-Payloads bleiben kompatibel.
- API-Felder sind strikt typisiert; keine Scalar-Coercion und keine unbekannten Felder.
- Entwürfe haben keine Zeitablauffrist.
- Fehlende oder geänderte Quellen, Proposal-/Rough-Cut-/Voice-/FPS-Drift stoppen vor jeder Mutation.
- TypeScript bleibt `strict` und verwendet kein `any`.
- Neue Doku-Prosa ist Deutsch; Code-Identifier, Kommentare und Commit-Messages sind Englisch.
- Keine neue Cloud-Synchronisierung und kein LLM-Aufruf pro Auswahländerung.

---

## Dateistruktur

- `services/local-api/src/laura/db/migrations/0036_resumable_visual_selection.sql` ergänzt additive Session-Metadaten und die Draft-Tabelle.
- `services/local-api/src/laura/db/repos.py` ist die einzige SQL-Zugriffsschicht für Session-Verknüpfung, Session-Touch und revisionsgebundene Drafts.
- `services/local-api/src/laura/short_creator/visual_selection_state.py` kapselt Quell-Snapshots, kanonische Hashes und Proposal-Freshness ohne HTTP-Abhängigkeit.
- `services/local-api/src/laura/short_creator/visual_selection_drafts.py` kapselt Default-Drafts, strukturelle Zwischenvalidierung und den persistenten Draft-Lebenszyklus.
- `services/local-api/src/laura/short_creator/visual_candidates.py` bindet Proposal-Parents in den v2-Proposal-Hash.
- `services/local-api/src/laura/short_creator/production_tools.py` erzeugt neue v2-Proposals mit starkem Source-Parent.
- `services/local-api/src/laura/api/short_creator.py` exponiert Draft-/Open-Session-Verträge und verbindet Freshness mit der finalen Bestätigung.
- `services/local-api/src/laura/chat/executor.py`, `services/local-api/src/laura/api/chat.py` und `services/local-api/src/laura/chat/router.py` halten Chat, Session und ursprünglichen Brief verbunden.
- `apps/desktop/src/api.ts` enthält die strikten Wire-Typen und Clientmethoden.
- `apps/desktop/src/components/chat/useVisualSelectionDraft.ts` besitzt die serialisierte Autosave-/Revision-Logik.
- `apps/desktop/src/components/chat/VisualSelectionCard.tsx` stellt den Server-Draft dar und blockiert Confirm bei ungespeichertem, konfliktbehaftetem oder veraltetem Zustand.
- `apps/desktop/src/components/chat/OpenSessionsPanel.tsx` stellt offene Sessions dar.
- `apps/desktop/src/components/chat/ChatStage.tsx`, `ConversationList.tsx` und `ActionCard.tsx` öffnen verknüpfte oder verwaiste Sessions ohne LLM-Turn.

---

### Task 1: Persistente Session-Metadaten und Draft-Repository

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0036_resumable_visual_selection.sql`
- Modify: `services/local-api/src/laura/db/repos.py`
- Modify: `services/local-api/tests/test_production_sessions_repos.py`
- Create: `services/local-api/tests/test_visual_selection_draft_repos.py`
- Test: `services/local-api/tests/test_db_backend.py`

**Interfaces:**
- Consumes: bestehende `Database.transaction(immediate=True)`, `production_sessions` und `conversations`.
- Produces: `DraftRevisionConflict`, `create_production_session(db, *, session_id, asset_id, created_utc, brief_text="")`, `link_production_session_conversation`, `touch_production_session`, `get_visual_selection_draft`, `save_visual_selection_draft`, `delete_visual_selection_draft`, `list_production_sessions_by_updated`.

- [x] **Step 1: Migration- und Session-Metadaten-RED schreiben**

  Ergänze einen Test, der eine alte Datenbank migriert, eine Session mit Brief anlegt, sie mit einem Chat verknüpft und die Rückwärtskompatibilität bestehender Aufrufer prüft:

  ```python
  repos.create_production_session(
      db,
      session_id="session-1",
      asset_id=asset_id,
      created_utc="2026-08-17T08:00:00+00:00",
      brief_text="Baue den Rough Cut weiter",
  )
  repos.link_production_session_conversation(db, "session-1", conversation_id)
  row = repos.get_production_session(db, "session-1")
  assert row is not None
  assert row["brief_text"] == "Baue den Rough Cut weiter"
  assert row["conversation_id"] == conversation_id
  assert row["updated_utc"] == "2026-08-17T08:00:00+00:00"
  ```

- [x] **Step 2: RED ausführen**

  Run: `cd services/local-api && uv run pytest tests/test_production_sessions_repos.py tests/test_db_backend.py -q`

  Expected: FAIL, weil Migration 0036 und die erweiterten Repository-Signaturen fehlen.

- [x] **Step 3: Additive Migration und Session-Repositories implementieren**

  Die Migration enthält:

  ```sql
  ALTER TABLE production_sessions ADD COLUMN conversation_id TEXT
      REFERENCES conversations(id) ON DELETE SET NULL;
  ALTER TABLE production_sessions ADD COLUMN brief_text TEXT NOT NULL DEFAULT '';
  ALTER TABLE production_sessions ADD COLUMN updated_utc TEXT NOT NULL DEFAULT '';
  UPDATE production_sessions SET updated_utc = created_utc WHERE updated_utc = '';
  CREATE INDEX idx_production_sessions_updated
      ON production_sessions(updated_utc DESC, session_id);
  CREATE INDEX idx_production_sessions_conversation
      ON production_sessions(conversation_id);

  CREATE TABLE visual_selection_drafts (
      session_id TEXT PRIMARY KEY
          REFERENCES production_sessions(session_id) ON DELETE CASCADE,
      proposal_hash TEXT NOT NULL,
      source_fingerprint TEXT NOT NULL,
      selections_json TEXT NOT NULL,
      revision INTEGER NOT NULL CHECK (revision >= 1),
      updated_utc TEXT NOT NULL
  );
  ```

  `create_production_session` erhält `brief_text: str = ""`, setzt `updated_utc=created_utc` und lässt bestehende Aufrufer unverändert funktionieren.

- [x] **Step 4: Draft-Roundtrip- und CAS-RED schreiben**

  Der neue Test öffnet dieselbe SQLite-Datei über zwei `SqliteDatabase`-Instanzen, speichert Revision 1, liest sie nach einem simulierten Neustart und lässt zwei Schreiber mit `expected_revision=1` konkurrieren:

  ```python
  first = repos.save_visual_selection_draft(
      db_a,
      session_id="session-1",
      proposal_hash="a" * 64,
      source_fingerprint="b" * 64,
      selections=selections,
      expected_revision=None,
      updated_utc="2026-08-17T08:01:00+00:00",
  )
  assert first["revision"] == 1
  assert repos.get_visual_selection_draft(db_b, "session-1") == first

  second = repos.save_visual_selection_draft(
      db_a, session_id="session-1", proposal_hash="a" * 64,
      source_fingerprint="b" * 64, selections=changed,
      expected_revision=1, updated_utc="2026-08-17T08:02:00+00:00",
  )
  assert second["revision"] == 2
  with pytest.raises(repos.DraftRevisionConflict) as conflict:
      repos.save_visual_selection_draft(
          db_b, session_id="session-1", proposal_hash="a" * 64,
          source_fingerprint="b" * 64, selections=stale,
          expected_revision=1, updated_utc="2026-08-17T08:03:00+00:00",
      )
  assert conflict.value.current["revision"] == 2
  ```

- [x] **Step 5: Draft-Repository minimal implementieren**

  `save_visual_selection_draft` verwendet eine `BEGIN IMMEDIATE`-Transaktion, liest die aktuelle Revision innerhalb derselben Transaktion und aktualisiert Draft plus `production_sessions.updated_utc` atomar. JSON wird mit stabilen Separatoren gespeichert und beim Lesen wieder als Liste ausgegeben.

  ```python
  class DraftRevisionConflict(RuntimeError):
      def __init__(self, current: dict[str, Any] | None) -> None:
          super().__init__("visual selection draft revision conflict")
          self.current = current
  ```

- [x] **Step 6: Repository-GREEN und statische Gates ausführen**

  Run: `cd services/local-api && uv run pytest tests/test_production_sessions_repos.py tests/test_visual_selection_draft_repos.py tests/test_db_backend.py -q`

  Run: `cd services/local-api && uv run mypy src/laura/db/repos.py tests/test_production_sessions_repos.py tests/test_visual_selection_draft_repos.py`

  Run: `cd services/local-api && uv run ruff check src/laura/db/repos.py tests/test_production_sessions_repos.py tests/test_visual_selection_draft_repos.py`

  Expected: alle Befehle Exit 0.

- [x] **Step 7: Task 1 committen**

  ```bash
  git add services/local-api/src/laura/db/migrations/0036_resumable_visual_selection.sql services/local-api/src/laura/db/repos.py services/local-api/tests/test_production_sessions_repos.py services/local-api/tests/test_visual_selection_draft_repos.py services/local-api/tests/test_db_backend.py
  git commit -m "feat(db): persist visual selection drafts"
  ```

---

### Task 2: Starker Source-Parent und gemeinsame Freshness-Prüfung

**Files:**
- Create: `services/local-api/src/laura/short_creator/visual_selection_state.py`
- Modify: `services/local-api/src/laura/short_creator/visual_candidates.py`
- Modify: `services/local-api/src/laura/short_creator/production_tools.py`
- Create: `services/local-api/tests/test_visual_selection_state.py`
- Modify: `services/local-api/tests/test_visual_candidates.py`
- Modify: `services/local-api/tests/test_production_tools_visual_recut.py`

**Interfaces:**
- Consumes: `repos.get_asset`, `_rough_cut_source_hash`, `sha256_file`, Visual-v2-Plan-Parents.
- Produces: `SourceMediaStaleError`, `SourceMediaSnapshot`, `capture_source_media_snapshot(db, *, asset_id, rough_cut_hash, fps, voice_hash, voice_total_frames, script_hash, request_hash, strong)`, `validate_source_media_snapshot(db, *, asset_id, plan, strong)` und `build_rough_cut_visual_plan(request, scenes, narration_text, voice_total_frames, fps, proposal_parents)`.

- [x] **Step 1: Quell-Freshness-RED schreiben**

  Erzeuge ein echtes temporäres Video-Placeholder-File, einen Asset-Datensatz mit SHA-256 und einen kanonischen Snapshot. Prüfe getrennt:

  - unveränderte Datei: Quick- und Strong-Validierung erfolgreich;
  - gelöschte Datei: `SourceMediaStaleError(reason="source_missing")`;
  - geänderte Größe oder `mtime_ns`: Quick-Validierung stale;
  - geänderter Inhalt bei gleicher Größe und wiederhergestelltem Zeitstempel: Quick kann gleich bleiben, Strong muss `source_content_changed` liefern;
  - Rough-Cut-, FPS-, Voice- oder Script-Hash-Drift: Quick-Validierung stale.

  ```python
  snapshot = capture_source_media_snapshot(
      db,
      asset_id=asset_id,
      rough_cut_hash="1" * 64,
      fps=30.0,
      voice_hash="2" * 64,
      voice_total_frames=900,
      script_hash="3" * 64,
      request_hash="4" * 64,
      strong=True,
  )
  assert len(snapshot.quick_hash) == 64
  assert len(snapshot.strong_hash) == 64
  ```

- [x] **Step 2: Freshness-RED ausführen**

  Run: `cd services/local-api && uv run pytest tests/test_visual_selection_state.py -q`

  Expected: FAIL mit fehlendem Modul oder fehlenden Symbolen.

- [x] **Step 3: Kanonischen Snapshot minimal implementieren**

  Die Domain-Typen sind strikt und transportneutral:

  ```python
  @dataclass(frozen=True)
  class SourceMediaSnapshot:
      quick_hash: str
      strong_hash: str | None
      resolved_path: str
      size: int
      mtime_ns: int

  class SourceMediaStaleError(RuntimeError):
      def __init__(self, reason: str) -> None:
          super().__init__(reason)
          self.reason = reason
  ```

  Quick-JSON enthält Asset-ID, gespeicherten Asset-SHA, `Path.resolve(strict=True)`, Größe, `mtime_ns`, Rough-Cut-Hash, kanonische FPS, Voice-Hash/-Frames, Script- und Request-Hash. Strong-JSON erweitert Quick um den aktuell berechneten Datei-SHA-256. Bei `strong=False` bleibt `strong_hash=None`; Proposal-Erzeugung und finale Bestätigung verwenden `strong=True` und prüfen den Wert vor dem Aufbau der Parent-Map explizit auf `None`. Ist der gespeicherte Asset-SHA leer, wird er einmalig per enger Repository-CAS-Funktion gesetzt; stimmt er nicht, wird bereits Proposal-Erzeugung fail-closed abgebrochen.

- [x] **Step 4: Proposal-Parent-RED schreiben**

  Ergänze einen Visual-Candidates-Test, der zwei identische v2-Pläne nur mit unterschiedlichem `source_media`-Parent baut und verschiedene `proposal_hash`-Werte erwartet:

  ```python
  first = build_rough_cut_visual_plan(
      request=request, scenes=scenes, narration_text="text",
      voice_total_frames=900, fps=30.0,
      proposal_parents={"source_media": "a" * 64, "source_media_quick": "b" * 64},
  )
  second = build_rough_cut_visual_plan(
      request=request, scenes=scenes, narration_text="text",
      voice_total_frames=900, fps=30.0,
      proposal_parents={"source_media": "c" * 64, "source_media_quick": "d" * 64},
  )
  assert first.proposal_hash != second.proposal_hash
  assert first.parents["source_media"] == "a" * 64
  ```

- [x] **Step 5: Proposal-Erzeugung mit Source-Parents implementieren**

  `build_rough_cut_visual_plan` erhält `proposal_parents: Mapping[str, str] | None = None`, sortiert sie vor dem Hashing und setzt sie direkt auf `VisualPlan.parents`. `start_visual_recut` erzeugt vor `board.save` einen starken Snapshot und setzt:

  ```python
  proposal_parents = {
      "visual_recut_request": _content_hash(request),
      "script": _content_hash(script),
      "voice": _content_hash(voice),
      "rough_cut": rough_cut_hash,
      "source_media": snapshot.strong_hash,
      "source_media_quick": snapshot.quick_hash,
  }
  ```

  Die bestehende Pending-Idempotenz vergleicht dieselben Parents. Alte pending v2-Pläne ohne Source-Parents werden nicht wiederverwendet, sondern neu aufgebaut.

- [x] **Step 6: Source-/Proposal-GREEN ausführen**

  Run: `cd services/local-api && uv run pytest tests/test_visual_selection_state.py tests/test_visual_candidates.py tests/test_production_tools_visual_recut.py -q`

  Run: `cd services/local-api && uv run mypy src/laura/short_creator/visual_selection_state.py src/laura/short_creator/visual_candidates.py src/laura/short_creator/production_tools.py`

  Run: `cd services/local-api && uv run ruff check src/laura/short_creator/visual_selection_state.py src/laura/short_creator/visual_candidates.py src/laura/short_creator/production_tools.py tests/test_visual_selection_state.py`

  Expected: alle Befehle Exit 0; bestehende v1-Tests unverändert grün.

- [x] **Step 7: Task 2 committen**

  ```bash
  git add services/local-api/src/laura/short_creator/visual_selection_state.py services/local-api/src/laura/short_creator/visual_candidates.py services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_visual_selection_state.py services/local-api/tests/test_visual_candidates.py services/local-api/tests/test_production_tools_visual_recut.py
  git commit -m "feat(shorts): bind visual proposals to source media"
  ```

---

### Task 3: Draft-Domain-Service, HTTP-Verträge und Confirm-Heal

**Files:**
- Create: `services/local-api/src/laura/short_creator/visual_selection_drafts.py`
- Modify: `services/local-api/src/laura/api/short_creator.py`
- Create: `services/local-api/tests/test_visual_selection_drafts.py`
- Modify: `services/local-api/tests/test_api_visual_recut.py`
- Modify: `services/local-api/tests/test_production_api.py`

**Interfaces:**
- Consumes: Task 1 Draft-Repositories, Task 2 Source-Freshness, `VisualSceneSelection`, `VisualPlan`, `Board.transaction`.
- Produces: `VisualSelectionDraftView`, `default_visual_selections`, `read_visual_selection_draft`, `save_visual_selection_draft`, Draft GET/PUT, Draft im Produktionsstatus und Freshness-geschütztes Confirm.

- [x] **Step 1: Domain-Service-RED schreiben**

  Schreibe reine Service-Tests für:

  - deterministischen Default aus jeder `scene_choice`-Empfehlung;
  - vollständige Rough-Cut-Reihenfolge;
  - Kandidat muss zur Zeile gehören;
  - Dauer strikt 1–10 und höchstens `candidate.max_duration_s`;
  - weniger als drei Includes und Undercoverage dürfen als Draft gespeichert werden;
  - fehlende oder doppelte Zeilen werden abgelehnt;
  - stale Source-Parent führt zu `VisualDraftStaleError`, bevor das Repository schreibt.

  ```python
  view = read_visual_selection_draft(db, session_id=session_id, board=board)
  assert view.revision is None
  assert [row.rough_cut_order for row in view.selections] == list(range(len(plan.scene_choices)))
  assert view.selections[0].candidate_id == plan.scene_choices[0].recommended_candidate_id
  ```

- [x] **Step 2: Domain-RED ausführen**

  Run: `cd services/local-api && uv run pytest tests/test_visual_selection_drafts.py -q`

  Expected: FAIL mit fehlendem Modul oder fehlenden Services.

- [x] **Step 3: Domain-Service minimal implementieren**

  Definiere:

  ```python
  class VisualSelectionDraftView(BaseModel):
      model_config = ConfigDict(extra="forbid")
      session_id: str
      proposal_hash: str
      selections: list[VisualSceneSelection]
      revision: int | None
      updated_utc: str | None
      stale: bool = False
      stale_reason: str | None = None

  def save_visual_selection_draft(
      db: Database,
      *,
      session_id: str,
      asset_id: str,
      board: Board,
      proposal_hash: str,
      selections: list[VisualSceneSelection],
      expected_revision: int | None,
      now_utc: str,
  ) -> VisualSelectionDraftView:
      plan = require_pending_v2_visual_plan(board, proposal_hash)
      validate_draft_selections(plan, selections)
      source_fingerprint = validate_source_media_snapshot(
          db, asset_id=asset_id, plan=plan, strong=False
      ).quick_hash
      row = repos.save_visual_selection_draft(
          db,
          session_id=session_id,
          proposal_hash=proposal_hash,
          source_fingerprint=source_fingerprint,
          selections=[selection.model_dump(mode="json") for selection in selections],
          expected_revision=expected_revision,
          updated_utc=now_utc,
      )
      return draft_view_from_row(row)
  ```

  Der Service validiert zuerst Board/Plan/Quick-Freshness und ruft erst danach Task 1s CAS-Repository auf. Repository-Ausnahmen werden als Domain-Konflikt mit aktuellem View weitergegeben.

- [x] **Step 4: HTTP-RED für GET, PUT, Status und Restart schreiben**

  Die API-Matrix prüft:

  - GET ohne Draft liefert Defaults und `revision: null`;
  - PUT liefert Revision 1, danach Revision 2;
  - neue `SqliteDatabase`-Instanz liefert exakt Revision 2;
  - alte Revision liefert 409 samt aktuellem Draft;
  - Proposal-/Rough-Cut-/Voice-/FPS-/Source-Drift liefert 409 ohne Draft-/Board-/Job-Mutation;
  - unvollständige Draft-Coverage liefert 200;
  - unbekannte Felder, Strings statt Integer/Bool und ungültige SHA-Syntax liefern 422;
  - `GET /production/{session_id}` trägt denselben Draft-Snapshot;
  - Autosave verändert `latest_job_id` nicht und legt keinen Job an.

- [x] **Step 5: Strikte DTOs und Draft-Endpoints implementieren**

  ```python
  class VisualSelectionDraftRequest(BaseModel):
      model_config = ConfigDict(extra="forbid")
      proposal_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
      expected_revision: int | None = Field(default=None, ge=1, strict=True)
      selections: list[VisualSceneSelectionRequest]

  @router.get("/production/{session_id}/visual-selection/draft")
  def get_visual_selection_draft_endpoint(
      session_id: str,
      request: Request,
      principal: Annotated[Principal, Depends(require_permission("read"))],
  ) -> dict[str, Any]:
      return _read_visual_draft_for_session(_db(request), session_id).model_dump(mode="json")

  @router.put("/production/{session_id}/visual-selection/draft")
  def put_visual_selection_draft_endpoint(
      session_id: str,
      body: VisualSelectionDraftRequest,
      request: Request,
      principal: Annotated[Principal, Depends(require_permission("timeline:edit"))],
  ) -> dict[str, Any]:
      return _save_visual_draft_for_session(
          _db(request), session_id, body, now_utc=_utc_now_iso()
      ).model_dump(mode="json")
  ```

  Domain-Konflikte werden stabil auf `409` mit `{"code":"revision_conflict","current":{"revision":2,"selections":[]}}` abgebildet; Stale-Fehler auf `409` mit `{"code":"stale_visual_selection","reason":"source_content_changed"}`.

- [x] **Step 6: Confirm-Freshness- und Heal-RED schreiben**

  Ergänze Tests, die vor `confirm_visual_selection` die Quelldatei bei gleicher Größe verändern, den Board-Plan austauschen und einen bereits bestätigten Plan mit liegengebliebenem Draft simulieren. Erwartung:

  - Source-Drift: 409, keine Board-Version, kein Job;
  - frischer Draft: Bestätigung, Draft entfernt, genau ein Resume-Job;
  - bestätigter Plan plus Rest-Draft: idempotenter Read/Confirm entfernt Draft und heilt höchstens den bestehenden Resume-Pfad;
  - v1-Confirm bleibt unverändert.

- [x] **Step 7: Gemeinsame Confirm-Prüfung implementieren**

  Innerhalb des bestehenden `board.transaction()` wird für v2 vor `apply_scene_selections` die starke Task-2-Prüfung ausgeführt. Nach erfolgreichem `board.save` wird der Draft gelöscht; der bestehende Busy-Guard und `run_production_resume` bleiben die einzige Enqueue-Stelle. `_production_status_payload` liest Drafts nur bei pending v2.

- [x] **Step 8: API-/Confirm-GREEN ausführen**

  Run: `cd services/local-api && uv run pytest tests/test_visual_selection_drafts.py tests/test_api_visual_recut.py tests/test_production_api.py tests/test_production_tools_visual_recut.py -q`

  Run: `cd services/local-api && uv run mypy src/laura/short_creator/visual_selection_drafts.py src/laura/api/short_creator.py tests/test_visual_selection_drafts.py tests/test_api_visual_recut.py`

  Run: `cd services/local-api && uv run ruff check src/laura/short_creator/visual_selection_drafts.py src/laura/api/short_creator.py tests/test_visual_selection_drafts.py tests/test_api_visual_recut.py`

  Expected: alle Befehle Exit 0.

- [x] **Step 9: Task 3 committen**

  ```bash
  git add services/local-api/src/laura/short_creator/visual_selection_drafts.py services/local-api/src/laura/api/short_creator.py services/local-api/tests/test_visual_selection_drafts.py services/local-api/tests/test_api_visual_recut.py services/local-api/tests/test_production_api.py
  git commit -m "feat(api): autosave visual selection drafts"
  ```

---

### Task 4: Offene Sessions, Chat-Verknüpfung und dauerhafter Brief-Kontext

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py`
- Modify: `services/local-api/src/laura/chat/executor.py`
- Modify: `services/local-api/src/laura/api/chat.py`
- Modify: `services/local-api/src/laura/chat/router.py`
- Modify: `services/local-api/tests/test_production_api.py`
- Modify: `services/local-api/tests/test_chat_executor.py`
- Modify: `services/local-api/tests/test_chat_api.py`
- Modify: `services/local-api/tests/test_chat_router.py`

**Interfaces:**
- Consumes: Task 1 Session-Metadaten, Board `resume_point()` und Task 3 Draft-Status.
- Produces: `GET /production-sessions/open`, Chat→Session-Link und `brief_text` im aktiven Router-Kontext.

- [ ] **Step 1: Session-Brief- und Chat-Link-RED schreiben**

  Prüfe, dass `_create_production_session(db, asset_id, task=expected_task, target_seconds=45, format="9:16", language="en")` den vollständigen Brief speichert und `_handle_start_short` nach erfolgreichem Start die Session mit `conversation_id` verknüpft. Ein fehlgeschlagener Start darf keine Verknüpfung erzeugen.

  ```python
  messages = execute_decision(
      db,
      settings,
      conversation_id=conversation_id,
      decision={
          "tool": "start_short",
          "args": {"topic": "Rough Cut", "target_seconds": 45},
      },
      now_utc="2026-08-17T08:00:00+00:00",
  )
  session_id = messages[-1]["content"]["refs"]["session_id"]
  session = repos.get_production_session(db, session_id)
  assert session is not None
  assert session["conversation_id"] == conversation_id
  assert session["brief_text"] == expected_task
  ```

- [ ] **Step 2: Chat-Link-RED ausführen**

  Run: `cd services/local-api && uv run pytest tests/test_chat_executor.py -k "start_short and session" -q`

  Expected: FAIL, weil Brief und Conversation-Link fehlen.

- [ ] **Step 3: Session-Erzeugung und Executor verknüpfen**

  `_create_production_session` reicht `task` als `brief_text` an Task 1 weiter. `_handle_start_short` ruft erst nach erfolgreichem `run_project_auto_short` auf:

  ```python
  repos.link_production_session_conversation(
      db, str(result["session_id"]), conversation_id
  )
  ```

  Die Link-Funktion berührt sowohl Session- als auch Conversation-Zeitstempel mit `now_utc`.

- [ ] **Step 4: Open-Sessions-RED schreiben**

  Lege Sessions in den Zuständen Visual-Gate, Kontaktbogen-Gate, running, complete, failed und ohne Board an. Prüfe:

  - nur fortsetzbare/running Sessions werden gelistet;
  - Sortierung erfolgt nach `updated_utc` absteigend;
  - Antwort enthält `session_id`, `conversation_id`, `project_id`, Asset-Anzeige, Brief-Vorschau, `resume_point`, `state`, `updated_utc`, `draft_updated_utc`, `latest_job_id`, `stale`, `stale_reason`;
  - read-only GET verändert weder Session noch Board noch Jobs;
  - eine gelöschte Conversation setzt den Link auf null, die Session bleibt auffindbar.

- [ ] **Step 5: Open-Sessions-Endpoint implementieren**

  ```python
  @router.get("/production-sessions/open")
  def list_open_production_sessions(
      request: Request,
      principal: Annotated[Principal, Depends(require_permission("read"))],
  ) -> list[dict[str, Any]]:
      return _open_production_session_views(_db(request))
  ```

  Die Ableitung öffnet Boards read-only, verwendet Job-Liveness als Autorität und lässt kaputte/verwaiste Board-Verzeichnisse als stabile diagnostische Zeile erscheinen, ohne 500 für die gesamte Liste.

- [ ] **Step 6: Router-Kontext-RED schreiben**

  Erzeuge 25 Nachrichten, sodass der ursprüngliche Auftrag nicht mehr im letzten-20-Fenster liegt. `_active_session` liefert `brief_text`; `compose_context` muss genau eine kompakte Zeile vor `Recent conversation` enthalten:

  ```python
  assert "Original production brief: Baue einen 45-Sekunden-Short" in context
  assert old_first_message not in context
  assert len([line for line in context.splitlines() if line.startswith("Original production brief:")]) == 1
  ```

- [ ] **Step 7: Persistierten Brief in `_active_session` und `compose_context` integrieren**

  `_active_session` setzt `brief` aus der Sessionzeile. `compose_context` normalisiert Zeilenumbrüche und begrenzt nur die Router-Zeile auf eine definierte maximale Zeichenzahl; der vollständige Brief bleibt in SQLite. Das letzte-20-Nachrichtenfenster bleibt unverändert.

- [ ] **Step 8: Chat-/Session-GREEN ausführen**

  Run: `cd services/local-api && uv run pytest tests/test_production_api.py tests/test_chat_executor.py tests/test_chat_api.py tests/test_chat_router.py -q`

  Run: `cd services/local-api && uv run mypy src/laura/api/short_creator.py src/laura/chat/executor.py src/laura/api/chat.py src/laura/chat/router.py`

  Run: `cd services/local-api && uv run ruff check src/laura/api/short_creator.py src/laura/chat/executor.py src/laura/api/chat.py src/laura/chat/router.py`

  Expected: alle Befehle Exit 0.

- [ ] **Step 9: Task 4 committen**

  ```bash
  git add services/local-api/src/laura/api/short_creator.py services/local-api/src/laura/chat/executor.py services/local-api/src/laura/api/chat.py services/local-api/src/laura/chat/router.py services/local-api/tests/test_production_api.py services/local-api/tests/test_chat_executor.py services/local-api/tests/test_chat_api.py services/local-api/tests/test_chat_router.py
  git commit -m "feat(chat): resume linked production sessions"
  ```

---

### Task 5: Desktop-Wire-Typen und serialisiertes Autosave

**Files:**
- Modify: `apps/desktop/src/api.ts`
- Modify: `apps/desktop/src/api.production.test.ts`
- Create: `apps/desktop/src/components/chat/useVisualSelectionDraft.ts`
- Create: `apps/desktop/src/components/chat/useVisualSelectionDraft.test.tsx`
- Modify: `apps/desktop/src/components/chat/VisualSelectionCard.tsx`
- Modify: `apps/desktop/src/components/chat/VisualSelectionCard.test.tsx`

**Interfaces:**
- Consumes: Task 3 Draft im ProductionStatus und PUT-Vertrag.
- Produces: `VisualSelectionDraft`, `OpenProductionSession`, `LauraApiError`, `saveVisualSelectionDraft`, `listOpenProductionSessions` und `useVisualSelectionDraft`.

- [ ] **Step 1: Client-Wire-RED schreiben**

  Ergänze API-Tests für den exakten PUT-Body und Open-Sessions-GET:

  ```typescript
  await client.saveVisualSelectionDraft("session-1", {
    proposal_hash: "a".repeat(64),
    expected_revision: 2,
    selections,
  });
  expect(fetch).toHaveBeenCalledWith(
    "http://127.0.0.1:8765/production/session-1/visual-selection/draft",
    expect.objectContaining({
      method: "PUT",
      body: JSON.stringify({
        proposal_hash: "a".repeat(64), expected_revision: 2, selections,
      }),
    }),
  );
  ```

  Prüfe zusätzlich, dass ein 409-JSON-Body als `LauraApiError` mit `status` und `body: unknown` erhalten bleibt, während `message` weiterhin mit `"409: "` beginnt.

- [ ] **Step 2: Client-RED ausführen**

  Run: `cd apps/desktop && pnpm test -- src/api.production.test.ts`

  Expected: FAIL mit fehlenden Typen/Methoden.

- [ ] **Step 3: Strikte Wire-Typen und Clientmethoden implementieren**

  ```typescript
  export interface VisualSelectionDraft {
    session_id: string;
    proposal_hash: string;
    selections: VisualSceneSelection[];
    revision: number | null;
    updated_utc: string | null;
    stale: boolean;
    stale_reason: string | null;
  }

  export interface SaveVisualSelectionDraftRequest {
    proposal_hash: string;
    expected_revision: number | null;
    selections: VisualSceneSelection[];
  }

  export class LauraApiError extends Error {
    constructor(
      readonly status: number,
      readonly body: unknown,
      message: string,
    ) {
      super(message);
    }
  }
  ```

  `ProductionBoardStatus.visual_selection_gate` erhält `draft?: VisualSelectionDraft`. Keine bestehenden optionalen Felder werden verpflichtend.

- [ ] **Step 4: Autosave-Hook-RED schreiben**

  Mit deferred Promises prüft der Hook:

  - Initialisierung mit Server-Draft statt Empfehlungen;
  - Kandidat, Include und Dauer senden jeweils den vollständigen Zustand;
  - drei schnelle Änderungen erzeugen keine parallelen PUTs und verwenden Revisionen 1, 2, 3;
  - 409 setzt `conflict`, bewahrt lokale Entscheidungen und bietet `loadServerDraft`;
  - Stale-409 setzt `stale`;
  - Netzwerkfehler setzt `error` und `retry` sendet denselben letzten Entwurf;
  - `flush()` wartet auf die Queue.

  Erwartete Hook-Schnittstelle:

  ```typescript
  const {
    decisions,
    updateDecision,
    saveState,
    savedAt,
    flush,
    retry,
    loadServerDraft,
  } = useVisualSelectionDraft({ client, sessionId, gate });
  ```

- [ ] **Step 5: Hook minimal implementieren**

  Der Hook besitzt `revisionRef`, `queueRef`, `latestSelectionsRef` und einen Generation-Guard für Gate-/Session-Wechsel. Er hängt jeden Save an die bestehende Promise-Kette, statt Debounce-Timer zu verwenden. Damit ist jede Nutzeränderung unmittelbar durable, aber Requests bleiben geordnet.

- [ ] **Step 6: Kartenintegration-RED schreiben**

  Ergänze Komponenten-Tests:

  - gespeicherter Kandidat/Use/Skip/Dauer gewinnt nach Remount über Empfehlungen;
  - jede Eingabe zeigt `Speichert …`, danach `Gespeichert`;
  - Confirm ist während Save, bei Error, Conflict und Stale deaktiviert;
  - Confirm ruft zuerst `flush()` und danach mit exakt dem serverbestätigten Zustand `confirmVisualSelection` auf;
  - unvollständige Coverage speichert, bleibt aber nicht confirmierbar;
  - Legacy-v1-Karte ruft keinen Draft-Endpoint auf.

- [ ] **Step 7: `VisualSelectionCard` auf den Hook umstellen**

  Ersetze für v2 die lokale `useState(initialSceneDecisions(gate))`-Quelle durch den Hook. Jede bestehende `setDecisions`-Stelle ruft `updateDecision(next)` auf. Rendere Speicher-/Fehler-/Konflikt-/Stale-Status und die Aktionen `Erneut speichern` beziehungsweise `Serverstand laden`.

- [ ] **Step 8: Desktop-Autosave-GREEN ausführen**

  Run: `cd apps/desktop && pnpm test -- src/api.production.test.ts src/components/chat/useVisualSelectionDraft.test.tsx src/components/chat/VisualSelectionCard.test.tsx`

  Run: `cd apps/desktop && pnpm run typecheck`

  Run: `cd apps/desktop && pnpm run lint:tokens`

  Expected: alle Befehle Exit 0.

- [ ] **Step 9: Task 5 committen**

  ```bash
  git add apps/desktop/src/api.ts apps/desktop/src/api.production.test.ts apps/desktop/src/components/chat/useVisualSelectionDraft.ts apps/desktop/src/components/chat/useVisualSelectionDraft.test.tsx apps/desktop/src/components/chat/VisualSelectionCard.tsx apps/desktop/src/components/chat/VisualSelectionCard.test.tsx
  git commit -m "feat(desktop): autosave visual selections"
  ```

---

### Task 6: „Offene Sessions“-Einstieg und explizites Fortsetzen

**Files:**
- Create: `apps/desktop/src/components/chat/OpenSessionsPanel.tsx`
- Create: `apps/desktop/src/components/chat/OpenSessionsPanel.test.tsx`
- Modify: `apps/desktop/src/components/chat/ConversationList.tsx`
- Modify: `apps/desktop/src/components/chat/ConversationList.test.tsx`
- Modify: `apps/desktop/src/components/chat/ChatStage.tsx`
- Modify: `apps/desktop/src/components/chat/ChatStage.test.tsx`
- Modify: `apps/desktop/src/components/chat/ActionCard.tsx`
- Modify: `apps/desktop/src/components/chat/ActionCard.test.tsx`

**Interfaces:**
- Consumes: Task 4 Open-Sessions-Endpoint, Task 5 `OpenProductionSession`, bestehende Produktions-Aktionskarte.
- Produces: sichtbare offene Sessions, verknüpftes Chat-Resume und read-only Orphan-Session-Ansicht.

- [ ] **Step 1: Open-Sessions-Panel-RED schreiben**

  Prüfe:

  - null Sessions: Bereich bleibt kompakt und zeigt keinen falschen Resume-Button;
  - genau eine Session: prominenter Button `Fortsetzen` mit Brief-Vorschau und Speicherzeit;
  - mehrere Sessions: nach `updated_utc` sortierte Zeilen mit Zustand;
  - stale Session: sichtbarer Warntext, kein impliziter Confirm;
  - Klick liefert das vollständige `OpenProductionSession`-Objekt an `onResume`.

- [ ] **Step 2: Panel-RED ausführen**

  Run: `cd apps/desktop && pnpm test -- src/components/chat/OpenSessionsPanel.test.tsx`

  Expected: FAIL mit fehlender Komponente.

- [ ] **Step 3: Panel minimal implementieren**

  ```typescript
  export interface OpenSessionsPanelProps {
    sessions: OpenProductionSession[];
    onResume: (session: OpenProductionSession) => void;
  }
  ```

  Das Panel nutzt bestehende Token-Klassen und zeigt keine automatische Aktion beim Rendern.

- [ ] **Step 4: ChatStage-Resume-RED schreiben**

  Prüfe mit einem Mock-Client:

  - Mount lädt Conversation-Liste und offene Sessions;
  - verknüpfter Resume-Klick lädt genau den angegebenen Chat und erzeugt keinen POST;
  - nach Laden erscheint die zum `session_id` gehörende ActionCard;
  - verwaister Resume-Klick zeigt Brief plus Produktionskarte direkt, ohne Conversation oder LLM-Turn anzulegen;
  - Session wird nie ohne Klick geöffnet;
  - nach Autosave/Confirm werden offene Sessions und Status aktualisiert.

- [ ] **Step 5: Produktionskarte für Orphan-Ansicht wiederverwendbar machen**

  Exportiere die bestehende `ProductionActionCard` unter dem präziseren Namen `ProductionSessionCard`, ohne Polling-/Gate-Prioritätslogik zu duplizieren:

  ```typescript
  export interface ProductionSessionCardProps {
    client: LauraClient;
    sessionId: string;
    jobId: string | null;
    initialOutcome: string;
    onFocus?: () => void;
  }
  ```

  `ActionCard` verwendet denselben Export. Die Orphan-Ansicht setzt `initialOutcome` aus dem Open-Session-State und zeigt oberhalb der Karte den persistierten Brief.

- [ ] **Step 6: ChatStage und ConversationList integrieren**

  `ChatStage` lädt beide Listen gemeinsam, hält `resumedOrphan: OpenProductionSession | null` und behandelt einen Resume-Klick explizit:

  ```typescript
  if (session.conversation_id !== null) {
    setResumedOrphan(null);
    setActiveId(session.conversation_id);
  } else {
    setActiveId(null);
    setResumedOrphan(session);
  }
  ```

  Ein normaler Chat-Klick löscht `resumedOrphan`. `ConversationList` rendert `OpenSessionsPanel` oberhalb von „Neuer Chat“.

- [ ] **Step 7: UI-GREEN ausführen**

  Run: `cd apps/desktop && pnpm test -- src/components/chat/OpenSessionsPanel.test.tsx src/components/chat/ConversationList.test.tsx src/components/chat/ChatStage.test.tsx src/components/chat/ActionCard.test.tsx`

  Run: `cd apps/desktop && pnpm run typecheck`

  Run: `cd apps/desktop && pnpm run lint:tokens`

  Expected: alle Befehle Exit 0.

- [ ] **Step 8: Task 6 committen**

  ```bash
  git add apps/desktop/src/components/chat/OpenSessionsPanel.tsx apps/desktop/src/components/chat/OpenSessionsPanel.test.tsx apps/desktop/src/components/chat/ConversationList.tsx apps/desktop/src/components/chat/ConversationList.test.tsx apps/desktop/src/components/chat/ChatStage.tsx apps/desktop/src/components/chat/ChatStage.test.tsx apps/desktop/src/components/chat/ActionCard.tsx apps/desktop/src/components/chat/ActionCard.test.tsx
  git commit -m "feat(desktop): resume open production sessions"
  ```

---

### Task 7: Restart-/Stale-Akzeptanz, Dokumentation und Abschluss-Gates

**Files:**
- Create: `services/local-api/tests/test_visual_selection_resume_acceptance.py`
- Modify: `docs/rough-cut-visual-selection.md`
- Modify: `tasks/todo.md`
- Modify: `lessons.md` only if this implementation receives a new factual user correction

**Interfaces:**
- Consumes: vollständige Tasks 1–6.
- Produces: automatisierter Restart-/Multi-Day-Vertrag, Bedienungsdoku und verifizierter Abschlussbericht.

- [ ] **Step 1: End-to-End-Restart-RED schreiben**

  Der Test verwendet eine echte SQLite-Datei und echte Board-Artefakte:

  1. pending v2-Plan erzeugen;
  2. drei Entscheidungen als Draft speichern;
  3. Datenbank- und Board-Objekte verwerfen;
  4. Zeitstempel sieben Tage zurücksetzen;
  5. neue `SqliteDatabase`- und Board-Instanzen öffnen;
  6. Open-Sessions-GET und ProductionStatus prüfen;
  7. exakte Entscheidungen vergleichen;
  8. final bestätigen und genau einen Resume-Job nachweisen.

  Zusätzlich pinnt der Test die Script-/Voice-Content-Hashes vor und nach dem Resume.

- [ ] **Step 2: Acceptance-RED/GREEN ausführen**

  Run: `cd services/local-api && uv run pytest tests/test_visual_selection_resume_acceptance.py -q`

  Expected vor nötigen Integrationskorrekturen: FAIL am ersten nicht vollständig verdrahteten Vertrag. Korrigiere ausschließlich die verantwortliche Task-Grenze, wiederhole den Einzeltest bis Exit 0 und führe danach die betroffene Nachbarsuite erneut aus.

- [ ] **Step 3: Dokumentation aktualisieren**

  Ergänze `docs/rough-cut-visual-selection.md` um:

  - Autosave-Status und Revisionen;
  - „Offene Sessions“ und explizites Fortsetzen;
  - Verhalten nach App-/Backend-Neustart;
  - Konflikt- und Stale-Meldungen;
  - Drive-Datei-Prüfung vor Confirm;
  - klaren Hinweis, dass Autosave keine Tokens verbraucht.

  Markiere das Arbeitspaket in `tasks/todo.md` erst nach allen automatisierten Gates und der Live-Verifikation als erledigt. Wenn Live extern blockiert ist, bleibt der Live-Punkt offen und wird als Non-Claim dokumentiert.

- [ ] **Step 4: Backend-Vollgates frisch ausführen**

  Run: `cd services/local-api && uv run pytest -p no:cacheprovider`

  Run: `cd services/local-api && uv run mypy src tests`

  Run: `cd services/local-api && uv run ruff check src tests`

  Expected: Exit 0; vollständige Testanzahl und Warnungen im Abschlussbericht festhalten.

- [ ] **Step 5: Desktop-Vollgates frisch ausführen**

  Run: `cd apps/desktop && pnpm test`

  Run: `cd apps/desktop && pnpm run typecheck`

  Run: `cd apps/desktop && pnpm run lint:tokens`

  Run: `cd apps/desktop && pnpm run lint`

  Expected: Tests, Typecheck und Token-Lint Exit 0. Falls das repositoryweit bekannte fehlende ESLint-Binary weiterhin der einzige Lint-Fehler ist, exakt als Infrastruktur-Non-Claim dokumentieren; keine ungeplante Dependency-/Lockfile-Änderung vornehmen.

- [ ] **Step 6: Live-Drive-Akzeptanz durchführen**

  Mit dem vorhandenen Projekt „Drive VibeMind“ und dem vom Nutzer gewählten Cloud-Provider:

  1. Backend und Electron aus diesem Worktree starten;
  2. eine neue Visual-v2-Auswahl öffnen;
  3. Kandidaten, Use/Skip und Dauer mehrerer Szenen ändern;
  4. `Gespeichert` und Revision protokollieren;
  5. Electron und Backend vollständig stoppen;
  6. neu starten und über „Offene Sessions“ fortsetzen;
  7. jede Auswahl und denselben Proposal-Hash vergleichen;
  8. Bildauswahl und Kontaktbogen bestätigen;
  9. Render/QA verfolgen und Script-/Voice-Hashes unverändert nachweisen.

  Keine API-Keys, Tokenwerte oder Präfixe in Logs, Doku oder Commit aufnehmen. Bei Provider-/Drive-Nichtverfügbarkeit stoppen und den externen Blocker belegen, statt Erfolg zu behaupten.

- [ ] **Step 7: Scope-, Secret- und Diff-Gates ausführen**

  Run: `git diff --check`

  Run: `git status --short --branch`

  Run: `git diff --name-only main...HEAD`

  Prüfe neue Dateien zusätzlich auf versehentliche `.env`-, Token-, Key- und lokale Workspace-Pfade. Expected: nur geplante Source-/Test-/Doku-Pfade, keine Secrets, keine Runtime-Artefakte.

- [ ] **Step 8: Task 7 committen**

  ```bash
  git add services/local-api/tests/test_visual_selection_resume_acceptance.py docs/rough-cut-visual-selection.md tasks/todo.md
  git commit -m "docs(shorts): verify resumable visual selection"
  ```

- [ ] **Step 9: Completion-Review durchführen**

  Gleiche jedes Abnahmekriterium aus der Spec mit Testnamen, Gate-Ausgabe oder Live-Evidenz ab. Prüfe insbesondere: kein Autosave-Job, kein LLM-Aufruf, kein stilles Conflict-Overwrite, starke Drive-Prüfung vor Confirm, exakte Wiederherstellung nach Neustart und unveränderte Script-/Voice-Hashes.
