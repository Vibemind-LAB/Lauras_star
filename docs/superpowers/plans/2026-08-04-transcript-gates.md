# Transkript-Gates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Text-first-Chat-Pipeline: Quell-Transkript bestätigen (Gate A), Sprechertext freigeben (Gate B), Szenen zum bestätigten Text matchen, Secondbrain-read-only-Tools fürs Team.

**Architecture:** Beide Gates laufen über die bestehende Chat-Karten-Maschinerie (Router-Tool → Executor → action-Karte). Gate A nutzt die VORHANDENE Segment-Edit-Kette (`repos.update_segment` + `transcript.realign`-Job + Audit) plus neuen Confirm-Stempel und Segment-Re-Index. Gate B ist ein deterministisches Tool-Gate: `synthesize_script_voice` verweigert bis zur Freigabe (BoardMeta-Flag), Freigabe per Chat-Tool startet die Fortsetzung. Szenen-Matching und Secondbrain sind neue FunctionTools im Produktionsteam.

**Tech Stack:** Python 3.11/FastAPI/pydantic v2/SQLite (uv, pytest, mypy, ruff) · TypeScript strict/React/vitest (pnpm) · Qdrant optional (best-effort) · ElevenLabs optional.

## Global Constraints

- Deutsch für User-sichtbare Texte, Englisch für Identifier/Kommentare/Commits.
- TypeScript `strict`, niemals `any` (→ `unknown` + narrowing).
- Gates: Backend `uv run pytest -q` (voll, nur in Task 13), zielgerichtet pro Task; bare `uv run mypy` (prüft auch tests); `uv run ruff check src tests`. Desktop: `pnpm vitest run <pfad>`, `pnpm typecheck`, voll `pnpm test -- --run` (CI fährt vitest).
- Schema-Pin: Migration 0035 ⇒ `test_schema_version_is_34_after_migrate` in `tests/test_embeddings_store.py` wird zu `..._is_35...` umbenannt UND auf 35 gestellt (Lesson aus dem Chat-Arc: der Pin wird pro Migration mitgezogen; Task 1 erledigt das SOFORT, nicht erst der Volllauf).
- Semantic-Indexing ist best-effort: darf NIE einen Request/Job mitreißen (Muster handlers.py fd0914b).
- Approval-Idempotenz: Doppel-Bestätigung → 409-Semantik bzw. no-op mit ehrlicher Meldung.
- Env-gated Extras: neue Integrationen ohne Env-Variable = deaktiviert, kein Import-Fehler.
- Chat bleibt Polling (2500 ms), kein SSE. Eine Tool-Aktion pro Router-Turn.
- Koordination: Die laufenden Chip-Sessions (Framing/zoom=off, Grounding-Kontrakt, busy_timeout, Board-Heilung) landen ZUERST auf feat/generate-ui; dieser Plan rebased darauf. Bei Konflikt in `production_tools.py`/`production_agents.py` gewinnt der Chip-Stand, dieser Arc passt sich an.
- Commits: Conventional Commits, explizite `git add <pfade>` (nie `-A`).

## Dateistruktur (neu/geändert)

- `services/local-api/src/laura/db/migrations/0035_transcript_confirm.sql` — NEU
- `services/local-api/src/laura/db/repos.py` — +2 Repos (confirm)
- `services/local-api/src/laura/analysis/semantic_sync.py` — NEU (Item-Bau + Re-Index, aus handlers.py extrahiert)
- `services/local-api/src/laura/analysis/handlers.py` — nutzt semantic_sync
- `services/local-api/src/laura/api/analysis.py` — +Confirm-Endpoint; PATCH-Segment triggert Re-Index
- `services/local-api/src/laura/chat/router.py` — +4 Tools
- `services/local-api/src/laura/chat/executor.py` — +4 Handler
- `services/local-api/src/laura/api/short_creator.py` — Unbestätigt-Warnung in den Services
- `services/local-api/src/laura/short_creator/board_models.py` — BoardMeta +2 Felder
- `services/local-api/src/laura/short_creator/board.py` — `set_script_approved`
- `services/local-api/src/laura/short_creator/production_tools.py` — Voice-Gate, `suggest_scenes_for_script`, gemessene Wortrate, Secondbrain-Registrierung
- `services/local-api/src/laura/short_creator/script_match.py` — NEU (Zeile→Szene-Matching)
- `services/local-api/src/laura/short_creator/brain_tools.py` — NEU (Vault-Suche/-Lesen)
- `services/local-api/src/laura/short_creator/production_orchestrator.py` — Status +script_gate, Prompt-Hinweis
- `apps/desktop/src/api.ts` — Typen (script_gate)
- `apps/desktop/src/components/chat/ActionCard.tsx` — TranscriptCard-Zweig + Script-Gate-Zustand

---

### Task 1: Migration 0035 + Confirm-Repos + Schema-Pin

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0035_transcript_confirm.sql`
- Modify: `services/local-api/src/laura/db/repos.py` (ans Dateiende, hinter den Konversations-Repos)
- Modify: `services/local-api/tests/test_embeddings_store.py:58-60` (Pin 34→35)
- Test: `services/local-api/tests/test_transcript_confirm_repos.py`

**Interfaces:**
- Produces: `repos.set_transcript_confirmed(db: Database, asset_id: str, confirmed_utc: str) -> None`; Spalte `media_assets.transcript_confirmed_at TEXT NULL` (kommt via `SELECT *` in `repos.get_asset` automatisch mit).

- [ ] **Step 1: Failing test**

```python
"""repos: transcript_confirmed_at stamp on media_assets."""

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    database = SqliteDatabase(settings.db_path)
    database.migrate()
    return database


def _asset(db: Database) -> str:
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root="ws",
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mp4", source_path="a.mp4",
    )
    return str(asset["id"])


def test_new_asset_is_unconfirmed(tmp_path: Path) -> None:
    db = _db(tmp_path)
    aid = _asset(db)
    row = repos.get_asset(db, aid)
    assert row is not None
    assert row["transcript_confirmed_at"] is None


def test_set_transcript_confirmed_stamps_and_overwrites(tmp_path: Path) -> None:
    db = _db(tmp_path)
    aid = _asset(db)
    repos.set_transcript_confirmed(db, aid, "2026-08-04T12:00:00Z")
    row = repos.get_asset(db, aid)
    assert row is not None and row["transcript_confirmed_at"] == "2026-08-04T12:00:00Z"
    repos.set_transcript_confirmed(db, aid, "2026-08-04T13:00:00Z")
    row = repos.get_asset(db, aid)
    assert row is not None and row["transcript_confirmed_at"] == "2026-08-04T13:00:00Z"
```

- [ ] **Step 2: rot** — `uv run pytest tests/test_transcript_confirm_repos.py -q` → FAIL (no such column / no attribute).
- [ ] **Step 3: Migration**

```sql
-- 0035_transcript_confirm.sql
-- Gate A: the user confirms an asset's source transcript before productions build on it.
ALTER TABLE media_assets ADD COLUMN transcript_confirmed_at TEXT NULL;
```

- [ ] **Step 4: Repo (repos.py, Ende der Datei, Stil der Nachbarn mit db.transaction())**

```python
def set_transcript_confirmed(db: Database, asset_id: str, confirmed_utc: str) -> None:
    """Stamp the asset's source transcript as user-confirmed (Gate A)."""
    with db.transaction() as conn:
        conn.execute(
            "UPDATE media_assets SET transcript_confirmed_at=? WHERE id=?",
            (confirmed_utc, asset_id),
        )
```

- [ ] **Step 5: Schema-Pin** in `tests/test_embeddings_store.py`: Testname `test_schema_version_is_35_after_migrate`, Assertion `== 35`.
- [ ] **Step 6: grün** — `uv run pytest tests/test_transcript_confirm_repos.py tests/test_embeddings_store.py -q`, dann `uv run mypy`, `uv run ruff check src tests`.
- [ ] **Step 7: Commit** — `git add services/local-api/src/laura/db/migrations/0035_transcript_confirm.sql services/local-api/src/laura/db/repos.py services/local-api/tests/test_transcript_confirm_repos.py services/local-api/tests/test_embeddings_store.py` · `git commit -m "feat(db): transcript_confirmed_at stamp (Gate A)"`

---

### Task 2: semantic_sync — Segment-Re-Index als wiederverwendbarer Baustein

**Files:**
- Create: `services/local-api/src/laura/analysis/semantic_sync.py`
- Modify: `services/local-api/src/laura/analysis/handlers.py` (Item-Bau ~Zeile 320-336 durch Aufruf ersetzen)
- Test: `services/local-api/tests/test_semantic_sync.py`

**Interfaces:**
- Consumes: `laura.semantic.get_index()/semantic_available()`, `SemanticIndex.index(items)` (Upsert per Punkt-Id), `repos.get_latest_transcript_run`, `repos.get_transcript(db, asset_id, run_id)`.
- Produces: `segment_index_item(asset: dict[str, Any], seg_row: dict[str, Any], speaker_label: str | None) -> dict[str, Any]` und `reindex_segments(db: Database, asset_id: str, segment_ids: list[str]) -> int` (Anzahl upserted; 0 wenn Semantic aus/nicht erreichbar — best-effort, wirft NIE).

- [ ] **Step 1: Failing test** — Fake-Index via monkeypatch:

```python
"""semantic_sync: per-segment re-index is best-effort and upserts the same item shape."""

from pathlib import Path
from typing import Any

import laura.analysis.semantic_sync as sync
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase


class _FakeIndex:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def index(self, items: list[dict[str, Any]]) -> int:
        self.items.extend(items)
        return len(items)


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path, start_runner=False)
    database = SqliteDatabase(settings.db_path)
    database.migrate()
    return database


def test_reindex_segments_upserts_edited_segment(tmp_path, monkeypatch) -> None:
    db = _db(tmp_path)
    # Seed: Projekt + Asset + Transkript-Run + 1 Segment (Repo-Helfer wie in
    # tests/test_analysis_api.py — der Implementer übernimmt die dortige Seed-Sequenz
    # create_analysis_run/insert_segments wortgleich; sie ist die Quelle der Wahrheit
    # für Spaltennamen).
    aid, seg_id = _seed_one_segment(db, text="cloud code")  # Helfer im Testfile
    fake = _FakeIndex()
    monkeypatch.setattr(sync, "get_index", lambda: fake)
    n = sync.reindex_segments(db, aid, [seg_id])
    assert n == 1
    assert fake.items[0]["payload"]["segment_id"] == seg_id
    assert fake.items[0]["payload"]["text"] == "cloud code"


def test_reindex_is_best_effort_when_index_raises(tmp_path, monkeypatch) -> None:
    db = _db(tmp_path)
    aid, seg_id = _seed_one_segment(db, text="x")

    def _boom() -> Any:
        raise RuntimeError("qdrant down")

    monkeypatch.setattr(sync, "get_index", _boom)
    assert sync.reindex_segments(db, aid, [seg_id]) == 0  # never raises
```

- [ ] **Step 2: rot.**
- [ ] **Step 3: Implementierung** — `segment_index_item` aus dem bestehenden Item-Bau in `handlers.py` (~320-336) EXTRAHIEREN (identische Keys: project_id, asset_id, segment_id, asset_name, text, start_frame, end_frame, speaker_label; Punkt-Id-Konvention unverändert). `reindex_segments`: Segmente des neuesten Transkript-Runs laden, auf `segment_ids` filtern, Items bauen, `get_index()`; jede Exception → `_log.warning` + `return 0` (Muster handlers.py).
- [ ] **Step 4: handlers.py** auf `semantic_sync.segment_index_item` umstellen (Verhalten identisch; bestehende Analysis-Tests decken das).
- [ ] **Step 5: grün** — `uv run pytest tests/test_semantic_sync.py tests/test_analysis_api.py -q`, mypy, ruff.
- [ ] **Step 6: Commit** — `git add services/local-api/src/laura/analysis/semantic_sync.py services/local-api/src/laura/analysis/handlers.py services/local-api/tests/test_semantic_sync.py` · `git commit -m "feat(analysis): shared segment index items + best-effort per-segment reindex"`

---

### Task 3: Gate-A-Services + Confirm-Endpoint + Re-Index nach Segment-Edit

**Files:**
- Modify: `services/local-api/src/laura/api/analysis.py` (PATCH-Segment ~194 erweitert; neuer POST)
- Test: `services/local-api/tests/test_transcript_gate_api.py`

**Interfaces:**
- Consumes: Task 1 `set_transcript_confirmed`, Task 2 `reindex_segments`, bestehend `repos.update_segment`, `audit.record`, `require_permission("asset:write")`.
- Produces: `POST /assets/{asset_id}/transcript:confirm` → 200 `{"asset_id": ..., "transcript_confirmed_at": ...}`, 404 bei unbekanntem Asset; PATCH `/transcript/segments/{id}` ruft zusätzlich `reindex_segments(db, asset_id, [segment_id])` (best-effort) und setzt `transcript_confirmed_at` NICHT zurück (bewusst: Korrektur nach Bestätigung bleibt bestätigt — die Karte zeigt den Stand).

- [ ] **Step 1: Failing tests** (Muster/Fixtures von `tests/test_analysis_api.py` übernehmen: client-Fixture + Seed):

```python
def test_confirm_transcript_stamps(client, db) -> None:
    aid = _seed_asset_with_transcript(db)
    r = client.post(f"/assets/{aid}/transcript:confirm")
    assert r.status_code == 200
    assert r.json()["transcript_confirmed_at"] is not None
    row = repos.get_asset(db, aid)
    assert row is not None and row["transcript_confirmed_at"] is not None


def test_confirm_unknown_asset_404(client) -> None:
    assert client.post("/assets/nope/transcript:confirm").status_code == 404


def test_segment_patch_triggers_reindex(client, db, monkeypatch) -> None:
    aid = _seed_asset_with_transcript(db)
    seg_id = _first_segment_id(db, aid)
    calls: list[tuple[str, list[str]]] = []
    monkeypatch.setattr(
        "laura.api.analysis.reindex_segments",
        lambda _db, a, ids: calls.append((a, ids)) or 1,
    )
    r = client.patch(f"/transcript/segments/{seg_id}", json={"text": "Claude Code"})
    assert r.status_code == 200
    assert calls == [(aid, [seg_id])]
```

- [ ] **Step 2: rot.**
- [ ] **Step 3: Implementierung** — Confirm-Endpoint im Stil der Nachbarn (`require_permission("asset:write")`, `audit.record(db, principal, "transcript.confirm", entity_type="asset", entity_id=aid)`, `utcnow_iso()` aus `laura.util`); im bestehenden PATCH nach `repos.update_segment` den `reindex_segments`-Aufruf (Segment→asset_id via `repos.get_segment`).
- [ ] **Step 4: grün** — `uv run pytest tests/test_transcript_gate_api.py tests/test_analysis_api.py -q`, mypy, ruff.
- [ ] **Step 5: Commit** — `git add services/local-api/src/laura/api/analysis.py services/local-api/tests/test_transcript_gate_api.py` · `git commit -m "feat(api): transcript confirm endpoint + reindex-on-segment-edit (Gate A)"`

---

### Task 4: Router-Tools (review/correct/confirm_transcript, approve_script)

**Files:**
- Modify: `services/local-api/src/laura/chat/router.py` (TOOLS-frozenset + `_validate_args`-Tabelle + Systemprompt-Toolliste)
- Test: `services/local-api/tests/test_chat_router.py` (ergänzen)

**Interfaces:**
- Produces (RouterDecision.args je Tool):
  - `review_transcript`: `{"asset_ref": str}` (nicht-leer)
  - `correct_transcript`: `{"asset_ref": str, "corrections": [{"segment_index": int >= 1, "text": str nicht-leer}] }` (Liste nicht-leer)
  - `confirm_transcript`: `{"asset_ref": str}`
  - `approve_script`: `{"session_ref": str}` (nicht-leer)

- [ ] **Step 1: Failing tests** (Stil der bestehenden `test_chat_router.py`-Validierungstests):

```python
def test_correct_transcript_requires_nonempty_corrections() -> None:
    bad = {"tool": "correct_transcript", "args": {"asset_ref": "a", "corrections": []}}
    ok = {"tool": "correct_transcript", "args": {
        "asset_ref": "a",
        "corrections": [{"segment_index": 3, "text": "Claude Code"}],
    }}
    assert _validate(bad) is not None   # Fehlertext vorhanden
    assert _validate(ok) is None


def test_new_tools_in_toolset() -> None:
    for t in ("review_transcript", "correct_transcript", "confirm_transcript", "approve_script"):
        assert t in TOOLS
```

(`_validate` = der im Testfile bereits etablierte Zugriff auf die Validierung; wie die vorhandenen Tests dieser Datei.)

- [ ] **Step 2: rot** → **Step 3: implementieren** (Validierungstabelle erweitern; `segment_index` int und nicht bool — Muster `revert.version`). Systemprompt: je Tool eine Zeile Beschreibung, deutsch formulierte Beispiele („zeig mir das Transkript von X", „ersetze in Segment 3 ‚Carpati' durch ‚Karpathy'", „Transkript passt", „Script freigeben").
- [ ] **Step 4: grün** — `uv run pytest tests/test_chat_router.py -q`, mypy, ruff.
- [ ] **Step 5: Commit** — `git add services/local-api/src/laura/chat/router.py services/local-api/tests/test_chat_router.py` · `git commit -m "feat(chat): router tools for transcript gates and script approval"`

---

### Task 5: Executor-Handler Gate A

**Files:**
- Modify: `services/local-api/src/laura/chat/executor.py`
- Test: `services/local-api/tests/test_chat_executor.py` (ergänzen)

**Interfaces:**
- Consumes: Task 3 Services (direkt via repos + `reindex_segments` — Executor ruft NICHT über HTTP, Muster `_handle_create_project`), Task 4 Decisions.
- Produces Handler (Dispatch-Tabelle erweitern):
  - `_handle_review_transcript`: Asset per `asset_ref` auflösen (Namens-Matching wortgleich wie `switch_project`: exakter Treffer vor Teiltreffer, kein Treffer → deutsche Rückfrage mit den verfügbaren Videonamen). Karte anhängen: `kind="action"`, `content={"tool": "review_transcript", "refs": {"asset_id": aid}, "outcome": "done", "payload": {"confirmed_at": <stamp|None>, "segments": [{"index": i, "id": seg_id, "start_s": round(start_sample/sample_rate, 1), "text": ...} für die ersten 100], "total": n}}` (bei n > 100 zusätzlich Textzeile „… und {n-100} weitere Segmente").
  - `_handle_correct_transcript`: Segmente des neuesten Runs laden, `segment_index` (1-basiert) → Segment-Id; unbekannter Index → Fehlertext mit gültigem Bereich; je Korrektur `repos.update_segment` + Audit (`audit.system_principal()` wenn kein Principal) + `reindex_segments`; Antwort-Text „{k} Segment(e) korrigiert." plus aktualisierte Karte wie oben.
  - `_handle_confirm_transcript`: `set_transcript_confirmed(db, aid, now_utc)` + Text „Transkript von ‚{name}' bestätigt."
- Sample-Rate für `start_s`: aus `media_assets.audio_sample_rate`, Fallback 16000 — dem Implementer: exakte Spalte in `repos.get_asset`-Row prüfen (`update_asset_probe` schreibt `audio_sample_rate`).

- [ ] **Step 1: Failing tests** — drei Tests im Stil der bestehenden Executor-Tests (Seed-Helfer der Datei nutzen): (a) review erzeugt Karte mit `payload.segments[0].text` und `total`; (b) correct mit `segment_index` ändert `transcript_segments.text` in der DB und ruft den (monkeypatchten) `reindex_segments` mit der Segment-Id; (c) confirm setzt den Stempel und antwortet deutsch. Zusätzlich: unbekannter `asset_ref` → Rückfrage-Text, KEINE Karte.
- [ ] **Step 2: rot** → **Step 3: implementieren** (Handler wie beschrieben; nie raisen — Muster der Datei).
- [ ] **Step 4: grün** — `uv run pytest tests/test_chat_executor.py -q`, mypy, ruff.
- [ ] **Step 5: Commit** — `git add services/local-api/src/laura/chat/executor.py services/local-api/tests/test_chat_executor.py` · `git commit -m "feat(chat): executor handlers for transcript review, correction and confirm"`

---

### Task 6: Unbestätigt-Warnung in Produktions-Services

**Files:**
- Modify: `services/local-api/src/laura/api/short_creator.py` (`run_project_auto_short`, `run_project_auto_overview`)
- Test: `services/local-api/tests/test_chat_executor.py` oder `tests/test_auto_short_api.py` (dort, wo die Warnungen der Services bereits gepinnt sind — der Implementer sucht `warnings` in beiden und ergänzt am etablierten Ort)

**Interfaces:**
- Produces: In den `warnings`-Listen beider Services zusätzlich der String `"Transkript unbestätigt: {display_name}"` je verwendetem Asset ohne `transcript_confirmed_at`. Kein Blocken.

- [ ] **Step 1: Failing test** — Auto-Short auf Asset ohne Stempel → Warnung enthalten; mit Stempel → nicht enthalten.
- [ ] **Step 2: rot** → **Step 3: implementieren** (nach der Asset-Auflösung, vor dem Scout; ein `repos.get_asset`-Read je Asset).
- [ ] **Step 4: grün** — gezielte Tests, mypy, ruff. **Step 5: Commit** — `git add services/local-api/src/laura/api/short_creator.py services/local-api/tests/<testfile>` · `git commit -m "feat(short-creator): warn when producing on an unconfirmed transcript"`

---

### Task 7: Gate B — Script-Checkpoint (Board-Flag, Voice-Gate, Status, Freigabe)

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board_models.py` (BoardMeta), `board.py`, `production_tools.py` (synthesize_script_voice), `production_orchestrator.py` (Status ~323-349, Prompt-STOP-Hinweis analog contact_sheet ~158-164), `services/local-api/src/laura/api/short_creator.py` (`run_project_auto_short` setzt Gate an neuen Sessions), `services/local-api/src/laura/chat/executor.py` (`_handle_approve_script`)
- Test: `services/local-api/tests/test_script_gate.py`

**Interfaces:**
- `BoardMeta` + `script_gate: bool = False` und `script_approved_utc: str | None = None` (pydantic-Defaults ⇒ alte meta.json laden unverändert).
- `Board.set_script_approved(self, approved_utc: str) -> None` — meta.json atomisch neu schreiben (`_write_atomic`, Muster der bestehenden Meta-Writes).
- `synthesize_script_voice` verweigert deterministisch:

```python
meta = board.meta()
if meta.script_gate and meta.script_approved_utc is None:
    return {
        "ok": False,
        "reason": (
            "script gate: awaiting user approval — the user must approve the "
            "script in chat before voice is synthesized"
        ),
    }
```

- Orchestrator-Status-Dict zusätzlich: `"script_gate": {"enabled": meta.script_gate, "approved": meta.script_approved_utc is not None, "pending": <enabled and not approved and board.load("script") is not None>}` und bei pending `"script_lines": [{"chapter": l.chapter, "scene_number": l.scene_number, "text": l.text} ...]`.
- Orchestrator-Prompt: nach dem Contact-Sheet-STOP-Absatz ein Absatz: bei aktivem, unbestätigtem Gate endet der Lauf nach dem letzten `save_script_chapter` (das Voice-Tool verweigert ohnehin — Prompt erklärt es dem Team, das Tool erzwingt es).
- `run_project_auto_short`: beim Anlegen NEUER Sessions `script_gate=True` in BoardMeta (Overview v1 unverändert False — bewusst; steht so in der Task, nicht raten).
- `_handle_approve_script`: `session_ref` auflösen (bestehendes `_resolve_session_id`), `Board.open(...)` via `board_root_for`, `set_script_approved(now_utc)`, dann `run_production_follow_up(db, session_id, "Script freigegeben — bitte fortsetzen: Voice, Cutlist, Contact Sheet, Render.")` und Action-Karte `{"tool": "approve_script", "refs": {"session_id": sid, "job_id": ...}, "outcome": "running"}`. Doppel-Freigabe (bereits approved): Text „Script war schon freigegeben." und KEIN neuer Lauf.

- [ ] **Step 1: Failing tests** — (a) BoardMeta-Roundtrip mit/ohne neue Felder (alte meta.json ohne Felder lädt); (b) `synthesize_script_voice` mit gate+unapproved → ok False mit „script gate"; nach `set_script_approved` → der Gate-Check greift nicht mehr (Rest der Voice-Prereqs via bestehender Test-Fixtures dieser Tools — Muster `tests/test_production_tools_contact_sheet.py::_board`); (c) Status-Dict trägt `script_gate.pending=True` + `script_lines`, sobald Script da und Gate offen; (d) Executor-Freigabe: setzt approved, startet Folge-Job, Karte referenziert Session; Doppel-Freigabe → Text, kein Job.
- [ ] **Step 2: rot** → **Step 3: implementieren** in obiger Reihenfolge (Models → Board → Tool-Gate → Status → Prompt → Service-Flag → Executor).
- [ ] **Step 4: grün** — `uv run pytest tests/test_script_gate.py tests/test_production_tools_contact_sheet.py tests/test_chat_executor.py -q`, mypy, ruff.
- [ ] **Step 5: Commit** — `git add services/local-api/src/laura/short_creator/board_models.py services/local-api/src/laura/short_creator/board.py services/local-api/src/laura/short_creator/production_tools.py services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/src/laura/api/short_creator.py services/local-api/src/laura/chat/executor.py services/local-api/tests/test_script_gate.py` · `git commit -m "feat(short-creator): script approval gate before voice/render (Gate B)"`

---

### Task 8: Wortraten-Kalibrierung aus dem Timings-Sidecar

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (`budget_words_for` ~935, `script_budget`-Tool)
- Test: `services/local-api/tests/test_script_budget_rate.py`

**Interfaces:**
- `budget_words_for(usable_seconds: float, language: str = ..., *, measured_rate_wps: float | None = None) -> int` — bei gesetzter Rate `int(usable_seconds * measured_rate_wps)`, sonst bisherige Sprach-Heuristik.
- `script_budget` misst vor der Berechnung: liegt auf dem Board ein `voice`-Artefakt mit `voice_s > 0`, dann `measured_rate_wps = <Wortzahl des zugehörigen Scripts> / voice_s` (Wortzahl = `sum(len(l.text.split()) ...)` des aktuellen Scripts; Kalibrier-Fund 2026-08-04: 95 Wörter ≈ 50 s ⇒ real ~1.9 W/s statt versprochener ~1.55). Antwort-Dict zusätzlich `"rate_source": "measured" | "heuristic"`.

- [ ] **Step 1: Failing tests** — (a) `budget_words_for(60.0, measured_rate_wps=1.9) == 114`; (b) `script_budget` auf Board MIT voice+script → `rate_source == "measured"` und words entsprechend; ohne voice → `"heuristic"`.
- [ ] **Step 2: rot** → **Step 3: implementieren.** → **Step 4: grün** (gezielt + mypy + ruff).
- [ ] **Step 5: Commit** — `git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_script_budget_rate.py` · `git commit -m "feat(short-creator): word budget uses measured speech rate when available"`

---

### Task 9: Text-first-Szenen-Matching (`suggest_scenes_for_script`)

**Files:**
- Create: `services/local-api/src/laura/short_creator/script_match.py`
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (Tool-Registrierung in `build_production_tool_specs`), `production_orchestrator.py` (ein Prompt-Satz: nach Script-Freigabe zuerst `suggest_scenes_for_script`, dann Storyline validieren)
- Test: `services/local-api/tests/test_script_match.py`

**Interfaces:**
- Consumes: `laura.short_creator.discovery._segment_hits(db, project_id, topic, limit)` (lexikalisch+semantisch, bestehende Fallback-Logik) und die dort etablierte Segment→Szene-Zuordnung (read-only; der Implementer übernimmt die Zuordnung aus `discovery.search_material` — sie ist die Referenz, NICHT neu erfinden).
- Produces: `match_lines_to_scenes(db: Database, project_id: str, asset_id: str, lines: list[str]) -> list[dict[str, Any]]` — je Zeile `{"line_index": i, "scene_number": int | None, "score": float, "matched_text": str | None}`; None wenn kein Treffer über der bestehenden Discovery-Schwelle. Tool `suggest_scenes_for_script()` (ohne Argumente): liest das Board-Script, ruft das Matching, Antwort `{"ok": True, "suggestions": [...]}` bzw. `{"ok": False, "reason": "no script on the board; save_script_chapter first"}`.

- [ ] **Step 1: Failing tests** — Fixture: Projekt + Asset + Transkript-Segmente mit bekannten Texten + Szenen (Seed-Muster aus den Discovery-Tests — `tests/` nach `search_material` durchsuchen und dieselbe Saat nutzen); Zeile „Claude Code im Vault" matcht die Szene, deren Segmente diesen Text tragen; Fantasie-Zeile → `scene_number is None`.
- [ ] **Step 2: rot** → **Step 3: implementieren** (pro Zeile ein `_segment_hits`-Aufruf mit der Zeile als Topic, bester Szenen-Score gewinnt). → **Step 4: grün** + mypy + ruff.
- [ ] **Step 5: Commit** — `git add services/local-api/src/laura/short_creator/script_match.py services/local-api/src/laura/short_creator/production_tools.py services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/tests/test_script_match.py` · `git commit -m "feat(short-creator): deterministic line-to-scene matching tool"`

---

### Task 10: Secondbrain-read-only-Tools

**Files:**
- Create: `services/local-api/src/laura/short_creator/brain_tools.py`
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (Registrierung, nur wenn Env gesetzt)
- Test: `services/local-api/tests/test_brain_tools.py`

**Interfaces:**
- Produces: `brain_root() -> Path | None` (aus `os.environ["LAURA_SECONDBRAIN_PATH"]`, None wenn unset/nicht existent); `search_second_brain(query: str, limit: int = 8) -> dict[str, Any]` — case-insensitive Substring-Suche über Name+Inhalt aller `*.md` (rekursiv), Ergebnis `{"ok": True, "results": [{"note": stem, "path": relpath, "snippet": <±120 Zeichen um den ersten Treffer>}]}`; `read_brain_note(name: str) -> dict[str, Any]` — Stem-Auflösung case-insensitive, `{"ok": True, "note": stem, "content": <max 8000 Zeichen>}`; Pfad-Traversal-Guard: aufgelöster Pfad MUSS unter `brain_root()` liegen (`Path.resolve()` + `is_relative_to`), sonst `{"ok": False, "reason": "note not found"}`. Beide Tools erscheinen in `build_production_tool_specs` NUR wenn `brain_root()` nicht None (Konvention „optionale Extras").

- [ ] **Step 1: Failing tests** — tmp-Vault mit 2 Notizen; Suche findet per Inhalt; read per Name; `read_brain_note("../secret")` → not found; ohne Env: Tools nicht in den Specs (`monkeypatch.delenv`).
- [ ] **Step 2: rot** → **Step 3: implementieren** (stdlib only; keine neuen Dependencies). → **Step 4: grün** + mypy + ruff.
- [ ] **Step 5: Commit** — `git add services/local-api/src/laura/short_creator/brain_tools.py services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_brain_tools.py` · `git commit -m "feat(short-creator): read-only second-brain tools (env-gated)"`

---

### Task 11: api.ts-Typen (script_gate + Karten-Payloads)

**Files:**
- Modify: `apps/desktop/src/api.ts` (`ProductionBoardStatus`-Interface erweitern)
- Test: `apps/desktop/src/api.chat.test.ts` (ergänzen)

**Interfaces:**
- Produces: auf `ProductionBoardStatus` optional `script_gate?: { enabled: boolean; approved: boolean; pending: boolean }` und `script_lines?: { chapter: number; scene_number: number; text: string }[]`. (Transkript-Karte braucht KEINEN Client-Call: Segmente liegen im Karten-Payload.)

- [ ] **Step 1: Failing test** — mockFetch-Antwort von `getProductionStatus` mit `script_gate.pending=true` + `script_lines` parst typsicher (Zugriff im Test auf `status.script_gate?.pending`).
- [ ] **Step 2: rot** → **Step 3: Typen ergänzen** → **Step 4: `pnpm vitest run src/api.chat.test.ts` + `pnpm typecheck`.**
- [ ] **Step 5: Commit** — `git add apps/desktop/src/api.ts apps/desktop/src/api.chat.test.ts` · `git commit -m "feat(desktop): script gate contracts on production status"`

---

### Task 12: Frontend-Karten — Transkript prüfen + Script-Freigabe-Zustand

**Files:**
- Modify: `apps/desktop/src/components/chat/ActionCard.tsx`
- Test: `apps/desktop/src/components/chat/ActionCard.test.tsx` (ergänzen)

**Interfaces:**
- Consumes: Task 5 Karten-Payload (`tool: "review_transcript"`, payload.segments/confirmed_at/total), Task 11 Status-Typen, bestehende Narrow-Helfer der Datei.
- Produces:
  - Zweig `review_transcript`: Kopf „Transkript prüfen" + Badge „✓ bestätigt" (wenn confirmed_at) bzw. „unbestätigt"; Segmentliste `#{index} · {start_s}s · {text}` (scrollbarer Container, `max-h-64 overflow-y-auto`); bei total > angezeigt: Zeile „… und {rest} weitere Segmente". Hinweiszeile: „Korrigieren per Nachricht: ‚ersetze in Segment 3 …'".
  - `ProductionActionCard`: wenn `status.script_gate?.pending` — statt Ergebniszeile den Block „📝 Sprechertext wartet auf Freigabe" mit den `script_lines` (Kapitel · Szene · Text) und Hinweis „Antworte ‚Script freigeben' oder nenne Änderungen." (Freigabe läuft als normale Chat-Nachricht → approve_script; die Karte selbst hat KEINE Buttons — eine Aktionsfläche pro Gate genügt, v1 ist chat-getrieben.)
- Deutsch exakt wie zitiert; TS strict, kein `any`.

- [ ] **Step 1: Failing tests** — (a) review_transcript-Karte rendert 3 Segmente + „unbestätigt"; mit confirmed_at → „✓ bestätigt"; (b) Production-Karte mit gemocktem `getProductionStatus` (`script_gate.pending`, 2 lines) rendert „Sprechertext wartet auf Freigabe" + beide Zeilen und KEINE „▶ ansehen"-Zeile.
- [ ] **Step 2: rot** → **Step 3: implementieren** → **Step 4: `pnpm vitest run src/components/chat` + `pnpm typecheck` + voll `pnpm test -- --run`.**
- [ ] **Step 5: Commit** — `git add apps/desktop/src/components/chat/ActionCard.tsx apps/desktop/src/components/chat/ActionCard.test.tsx` · `git commit -m "feat(desktop): transcript review card + script gate state"`

---

### Task 13: Volle Gates + Doku + manuelle Prüfliste

**Files:**
- Modify: `docs/00-overview.md` (Absatz „Transkript-Gates" im Chat-Kapitel), `tasks/todo.md` (Haken)

- [ ] **Step 1:** Backend voll: `uv run pytest -q -p no:cacheprovider` (VORDERGRUND, Summary aus der Ausgabe lesen), bare `uv run mypy`, `uv run ruff check src tests`.
- [ ] **Step 2:** Desktop voll: `pnpm test -- --run`, `pnpm typecheck`.
- [ ] **Step 3:** Doku-Absatz (deutsch, 5-8 Zeilen: die zwei Gates, Chat-Kommandos, Env `LAURA_SECONDBRAIN_PATH`).
- [ ] **Step 4:** Manuell zu prüfen (im Report ausdrücklich als solches markieren): kompletter Chat-Durchlauf in der App — Transkript-Karte → Korrektur → bestätigen → Short starten → Script-Gate-Karte → „Script freigeben" → fertiger Film spielt. (Stale-Backend-Regel beachten.)
- [ ] **Step 5: Commit** — `git add docs/00-overview.md tasks/todo.md` · `git commit -m "docs: transcript gates flow"`

---

## Self-Review (beim Schreiben erledigt)

- **Spec-Abdeckung:** Gate A (T1 Stempel, T2 Re-Index, T3 Endpoint+Patch-Hook, T4/T5 Chat, T6 Warnung) · Gate B (T7, T11, T12) · Text-first (T9) + Kalibrierung (T8) · Secondbrain (T10) · Doku/Gates (T13). Spec-Abweichung, bewusst: Korrekturen SEGMENT-weise statt wortweise (die vorhandene `update_segment`+Realign-Kette ist die Maschinerie; eine neue Wort-Patch-Tabelle wäre YAGNI — in T3 dokumentiert). Zweite bewusste Entscheidung: Gate B v1 nur für auto-short-Sessions (Overview folgt nach Bewährung).
- **Platzhalter-Scan:** keine TBD/„similar to"; wo der Implementer bestehende Seeds übernehmen soll, ist die QUELLDATEI genannt (test_analysis_api.py, Discovery-Tests, contact_sheet-Fixtures) — das ist Referenz auf existierenden Code, kein Platzhalter.
- **Typ-Konsistenz:** `set_transcript_confirmed` (T1) ↔ T3/T5/T6; `reindex_segments` (T2) ↔ T3/T5; `script_gate`/`script_approved_utc`/`set_script_approved` (T7) ↔ T11/T12; `measured_rate_wps` nur in T8; Router-Args (T4) ↔ Executor (T5/T7).
