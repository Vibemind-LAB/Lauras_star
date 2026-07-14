# Agent-Vision-Short v2 — Sessions (Slice 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slice 4 der Spec [2026-07-13-agent-vision-short-v2-design.md](../specs/2026-07-13-agent-vision-short-v2-design.md): **Produktions-Sessions** — Erst-Produktion und Folge-Nachrichten (adjust/revert) als Jobs über die API, mit Resume als Standardpfad. Damit wird das Vibe-Editing von außen bedienbar; ChatPanel-Attach ist Slice 5.

**Architecture:** Eine kleine `production_sessions`-Tabelle (session_id → asset_id) macht Sessions ohne Workspace-Scan auffindbar. Ein Job-Kind `production.run` ruft `run_production` (Slice 3); Folge-Nachrichten laufen als weiterer Job derselben Session mit `message`-Payload — der Task-Text bekommt einen Continuation-Vertrag (User-Wunsch + Board-Stand + Revert-Anleitung), das Team entscheidet; `revert_artifact` wird ein Coding-Agent-Tool. Endpunkte spiegeln `api/short_creator.py` (Berechtigung, 404/503-Gates, 202-Semantik). Coarse NDJSON-Run-Logs pro Session-Job (Lifecycle + Board-Snapshots) — feingranulare Agent-Events sind Slice-5/6-Verfeinerung.

**Tech Stack:** Python 3.11 · uv · pytest · FastAPI · bestehende Jobs-/Board-/Orchestrator-Schicht.

## Global Constraints

- v1 und Slice-1-3-Verhalten unangetastet; autogen lazy; Backend startet ohne `autoshort` (Endpunkte 503 wie `_autoshort_available`).
- Board bleibt Source of Truth; Sessions-Tabelle ist NUR ein Index (session_id, asset_id, created_utc) — kein Board-Inhalt in der DB.
- Jobs: `max_attempts=1` (LLM-Runs teuer + nicht idempotent, wie v1 auto-short); Queue wie `short_creator.run` (`QUEUE_ANALYSIS_CPU`).
- Revert ist eine INHALTLICHE Entscheidung des Teams auf User-Wunsch — als Tool (`revert_artifact`) mit klarer Charta, nie automatisch.
- Voller mypy-Scope (bare `uv run mypy`) 0 Fehler; ruff sauber; kein `print`; Conventional Commits; explizite `git add`-Pfade; Codex-Sperrgebiet tabu.
- Doku-Prosa Deutsch; Code/Kommentare/Commits Englisch.

**Arbeitsverzeichnis aller Kommandos:** `services/local-api/`.

**Referenz-Signaturen (verifiziert):** `run_production(db, config, *, asset_id, session_id, task, target_seconds=60, execute=None, deps=None) -> dict` und `board_root_for(db, asset_id, session_id)` (production_orchestrator.py); `Board.open/status/resume_point`; Jobs: `enqueue(db, *, queue, kind, payload, ..., max_attempts)` (jobs/runner.py:62), Registry-Muster `register_<x>_handlers(registry)` (short_creator/handlers.py:48, wired in main.py:76), `queue_for(kind)` + `_STAGE_QUEUE` (queues.py:41-47), `JobContext(job_id, kind, queue, payload, db, ...)`; API-Muster: `api/short_creator.py` (require_permission("timeline:edit"), 404 asset, 503 `_autoshort_available()`, 202 + `{"job_id"}`, AutoShortRequest-Feldgrenzen); Migrations-Muster: `db/migrations/0001_init.sql`…`0020_job_created_order.sql` (nächste Nummer 0021) + zugehörige repos-Funktionen in db/repos.py.

---

### Task 1: Migration `production_sessions` + Repos

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0021_production_sessions.sql`
- Modify: `services/local-api/src/laura/db/repos.py`
- Test: `services/local-api/tests/test_production_sessions_repos.py` (neu)

**Interfaces:**
- Produces: Tabelle `production_sessions(session_id TEXT PRIMARY KEY, asset_id TEXT NOT NULL REFERENCES assets(id), created_utc TEXT NOT NULL)`; Repos `create_production_session(db, *, session_id: str, asset_id: str, created_utc: str) -> None`, `get_production_session(db, session_id: str) -> dict[str, Any] | None`, `list_production_sessions(db, asset_id: str) -> list[dict[str, Any]]` (neueste zuerst). Task 4-6 konsumieren genau diese.

- [ ] **Step 1: Failing Tests** — Muster aus einem bestehenden Repo-Testfile (z. B. `tests/test_shorts_repos.py`) spiegeln: create→get roundtrip; get unbekannt → None; list nach asset gefiltert + Sortierung; doppeltes create → Fehler (PK) ODER idempotent — Entscheidung: PK-Konflikt soll `sqlite3.IntegrityError` werfen (Aufrufer generiert frische ids; Test mit `pytest.raises`).
- [ ] **Step 2: Run** `uv run pytest tests/test_production_sessions_repos.py -v` — FAIL (Tabelle/Funktionen fehlen).
- [ ] **Step 3: Implementation** — SQL-Datei im Stil der Nachbarn (Header-Kommentar englisch); Repos als dünne SQL-Wrapper im Stil der Nachbarfunktionen (Row→dict).
- [ ] **Step 4: Run** — PASS. Auch `uv run pytest tests/ -q -k "migration or schema"` falls es Schema-Zähl-Tests gibt (prüfen: `grep -rn "0020\|schema_version" tests/ | head` — Versionskonstanten ggf. mitziehen; Frontend EXPECTED_SCHEMA_VERSION NICHT anfassen, nur notieren falls betroffen → im Report melden).
- [ ] **Step 5: Commit** — `feat(short-creator): production_sessions table + repos` (Pfade: migration, repos.py, testfile).

---

### Task 2: `revert_artifact`-Tool + Coding-Agent-Charta

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py`
- Modify: `services/local-api/src/laura/short_creator/production_agents.py`
- Test: `services/local-api/tests/test_production_tools_revert.py` (neu)

**Interfaces:**
- Produces (Tool): `revert_artifact(name: str, version: int)` — validiert `name` gegen die Singleton-Namen (`storyline|script|voice|cutlist|render_report|qa_report`), ruft `board.revert(name, version)`; Erfolg → `{"ok": True, "name": name, "restored_version": version, "invalidated": [...]}` (invalidierte Downstream-Namen aus board.invalidate-Rückgabe — `Board.revert` gibt heute None zurück: das Tool liest vorher `downstream_of(name)` und danach, welche davon fehlen; einfacher: Tool merkt sich `[d for d in downstream_of(name) if board.load(d) is not None]` VOR dem Revert als "will be invalidated" und gibt das zurück); `FileNotFoundError` → `{"ok": False, "reason": "no archived <name> v<version>"}`; unbekannter Name → ok False mit gültiger Namensliste.
- coding_agent: `revert_artifact` in tool_names (iters 8 bleibt); Prompt-Zusatz (1-2 Sätze): reverts ONLY when the task/user message explicitly asks to go back; after a revert, rebuild downstream via the normal pipeline.
- qa_reviewer und andere Agenten bekommen das Tool NICHT.

- [ ] **Step 1: Failing Tests** — happy revert (storyline v1←v2, script invalidiert, Rückgabe nennt script), unbekannte Version → ok False, unbekannter Name → ok False + Namensliste, roster: coding_agent enthält revert_artifact / andere nicht, Prompt enthält "revert".
- [ ] **Step 2: Run** — FAIL.
- [ ] **Step 3: Implementation** — Tool im bestehenden Closure-/Fehler-Muster; Roster-/Prompt-Update minimal.
- [ ] **Step 4: Run** — PASS + `uv run pytest tests/test_production_agents.py -q` (Bestandsschutz — Prompt-Contract-Tests müssen grün bleiben).
- [ ] **Step 5: Commit** — `feat(short-creator): revert_artifact tool for the coding agent`.

---

### Task 3: Continuation-Vertrag im Orchestrator (`message`)

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py`
- Test: `services/local-api/tests/test_production_orchestrator.py` (Tests anhängen)

**Interfaces:**
- `build_production_task(db, board, *, asset_id, task, target_seconds, message: str | None = None) -> str` — bei `message`: zusätzlicher Block NACH dem Board-Status: `USER FOLLOW-UP REQUEST` mit dem Freitext (Länge auf 2000 Zeichen gekappt) + Anleitung: interpret the request; going back = coding_agent calls revert_artifact(name, version) using the archived_versions in the board status, then rebuild downstream; content changes = re-save the affected artifact (highest affected wins) and let downstream rebuild; NEVER redo intact upstream artifacts.
- `run_production(..., message: str | None = None)` — reicht durch; Board MUSS bereits existieren, wenn `message` gesetzt ist (sonst `{"ok": False, "error": "unknown session (no board)"}` — eine Folge-Nachricht ohne Erst-Produktion ist ein Fehler).

- [ ] **Step 1: Failing Tests** — task text enthält den Follow-up-Block + Anleitung nur bei message; message auf frisches Board (kein meta.json) → error dict; message-Run mit Fake-Execute → ok True und Task-Text (an Fake übergeben) enthält den User-Text.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implementation.** **Step 4: Run** — PASS (alle Orchestrator-Tests). **Step 5: Commit** — `feat(short-creator): follow-up message contract in production task`.

---

### Task 4: Job-Handler `production.run`

**Files:**
- Modify: `services/local-api/src/laura/short_creator/handlers.py`
- Modify: `services/local-api/src/laura/jobs/queues.py`
- Modify: `services/local-api/src/laura/main.py` (nur falls Registrierung nicht schon über `register_short_creator_handlers` läuft — Handler dort mit registrieren, KEIN neuer main-Hook)
- Test: `services/local-api/tests/test_production_run_handler.py` (neu)

**Interfaces:**
- `handle_production_run(ctx: JobContext, *, execute: ExecuteFn | None = None, deps: ProductionDeps | None = None) -> dict[str, Any]` — Payload `{asset_id, session_id, task, target_seconds?, message?}`; lazy Import von `run_production` + `resolve_from_env` (Muster `handle_short_creator_run`); Rückgabe = run_production-Dict unverändert. Registrierung: in `register_short_creator_handlers` zusätzlich `registry["production.run"] = handle_production_run`. Queue: `"production.run": QUEUE_ANALYSIS_CPU` in `_STAGE_QUEUE`.
- **Coarse Run-Log**: der Handler schreibt NDJSON nach `<workspace>/agent-runs/<session_id>/runs/<UTC>Z.ndjson`: Zeile 1 `{"type":"meta", asset_id, session_id, task, message?}`, Zeile 2 nach dem Run `{"type":"done", ok, stage, weak, escalated, export_id, resume_point}`; Schreibfehler werden geloggt, nie geworfen (Muster api/short_creator.py Stream-Log).

- [ ] **Step 1: Failing Tests** — Handler mit Fake-Execute: succeeded-Ergebnis landet als Rückgabe; asset fehlt → ok False dict (kein Raise); Run-Log-Datei existiert mit meta+done-Zeilen; Registry enthält production.run nach register; queue_for("production.run") == QUEUE_ANALYSIS_CPU.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implementation.** **Step 4: Run** — PASS + `uv run pytest tests/test_short_creator_handlers.py -q` falls vorhanden (grep). **Step 5: Commit** — `feat(short-creator): production.run job handler + coarse session run log`.

---

### Task 5: API — Session anlegen

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py`
- Test: `services/local-api/tests/test_production_api.py` (neu)

**Interfaces:**
- `POST /assets/{asset_id}/production` — Body `ProductionCreateRequest(task: str = Field(min_length=1, max_length=2000), target_seconds: int = Field(default=60, gt=0, le=600))`; Gates wie auto-short (Permission timeline:edit, 404 Asset, 503 ohne autoshort); erzeugt `session_id = new_id()`, `repos.create_production_session(...)` (created_utc UTC isoformat), `enqueue(..., kind="production.run", payload={asset_id, session_id, task, target_seconds}, max_attempts=1)`; 202 → `{"session_id": ..., "job_id": ...}`.

- [ ] **Step 1: Failing Tests** — Muster aus `tests/test_shorts_render_api.py`/bestehenden API-Tests (TestClient, Token-Header): 202 mit session_id+job_id (Job-Row existiert, kind korrekt, payload enthält session_id); 404 unbekanntes Asset; 422 leerer task; Session-Row persistiert.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implementation.** **Step 4: Run** — PASS + bestehende short_creator-API-Tests grün. **Step 5: Commit** — `feat(short-creator): create production session endpoint`.

---

### Task 6: API — Folge-Nachricht + Status

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py`
- Test: `services/local-api/tests/test_production_api.py` (anhängen)

**Interfaces:**
- `POST /production/{session_id}/message` — Body `ProductionMessageRequest(text: str = Field(min_length=1, max_length=2000))`; 404 wenn Session unbekannt (repos) ODER Board fehlt; enqueued `production.run` mit `message=text` (task = Meta-Task vom Board: `board.meta().task`); 202 → `{"session_id", "job_id"}`.
- `GET /production/{session_id}` — 404 wenn Session unbekannt; sonst `board.status()` + `{"resume_point": board.resume_point(expected)}` (expected via `context.scene_transcripts`; bei fehlendem Board: 404 mit detail "board missing"). Read-only, Permission wie die anderen GETs im File (prüfen und spiegeln).
- Board-Zugriff: `board_root_for(db, session.asset_id, session_id)` → `Board.open`.

- [ ] **Step 1: Failing Tests** — message: 202 + Job-Payload enthält message+session_id+asset_id; 404 unbekannte Session; 404 Session ohne Board; GET: Status-Shape (meta/scene_reviews/artifacts/resume_point) nach angelegtem Board; 404 unbekannt.
- [ ] **Step 2: Run** — FAIL. **Step 3: Implementation.** **Step 4: Run** — PASS. **Step 5: Commit** — `feat(short-creator): production message + status endpoints`.

---

### Task 7: Gesamt-Verifikation Slice 4

- [ ] `uv run pytest -q` → exit 0 · bare `uv run mypy` → 0 Fehler · `uv run ruff check src tests` → clean.
- [ ] Ledger-Eintrag; Doku-Hinweis: `docs/agentic-short-creator.md` um einen kurzen v2-Sessions-Abschnitt ergänzen (Endpunkte + Beispiel-cURL, Deutsch) — eigener Commit `docs(short-creator): v2 production sessions usage`.
