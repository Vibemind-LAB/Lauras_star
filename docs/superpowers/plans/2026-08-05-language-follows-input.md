# Sprache folgt dem Input Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Die Video-Sprache folgt der Sprache der Chat-Anweisung (explizite Nennung gewinnt), und „mach das in english" auf ein bestehendes Video wechselt die Board-Sprache per Team-Follow-up.

**Architecture:** Der Router liefert ein optionales `language`-Argument für `start_short`/`start_overview` (LLM erkennt die Anweisungssprache; explizite Nennung gewinnt); der Executor reicht es an `run_project_auto_short`/den Overview-Service durch (beide akzeptieren `language` bereits — heute reicht es nur niemand weiter). Für den Follow-up-Fall bekommt das Team ein neues board-gebundenes Tool `set_board_language` (atomarer Meta-Write unter dem Board-Lock) plus eine Charter-Zeile: erst Sprache wechseln, dann jedes Kapitel neu schreiben — das Gate re-armed sich über den content_hash von selbst.

**Tech Stack:** Python 3.11 (services/local-api, uv), bestehende Router-/Board-/Charter-Muster.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-05-language-follows-input-design.md` — Entscheidungen bindend.
- `language` ist ein englischer Sprachname als freier String: optional; wenn vorhanden nicht-leer, nur Buchstaben/Leerzeichen, ≤ 32 Zeichen (Router-Validierung; die Service-Felder erlauben 40 — Router bleibt strenger). KEINE Whitelist, kein Locale-Mapping.
- Fehlt `language`, bleibt „German" — kein Verhaltensbruch für bestehende Threads/Tests.
- `set_board_language` schreibt `meta.language` atomar unter dem Board-Lock (Muster `set_script_approved`); es ist NICHT im deterministischen Tail (der Vier-Tool-Pin `_STEP_BY_RESUME_POINT` bleibt unberührt) und NICHT bei `qa_reviewer`.
- Kein neues Gate-Verhalten: der Sprachwechsel wirkt über die normale Script-Neufassung (content_hash re-armed das Gate).
- UI-/Chat-Texte deutsch, Identifier/Kommentare/Commits englisch. Bare `uv run mypy` (src+tests), `ruff check src tests`, kein `print`.
- pytest NIE mit zusätzlichem `-q` (addopts hat schon `-q`). Gates je Task VORDERGRUND mit echter Summary-Zeile.
- Explizites `git add <paths>` (nie `-A`). Commits enden mit `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task SP1: Router — `language`-Argument + Erkennungsregel

**Files:**
- Modify: `services/local-api/src/laura/chat/router.py` (`_SYSTEM_PROMPT`-Bullets für start_short/start_overview, Rules-Absatz, `_validate_args`)
- Test: `services/local-api/tests/test_chat_router.py`

**Interfaces:**
- Consumes: bestehende `_validate_args`-Muster (`_validate_optional_target_seconds` zeigt den Optional-Stil).
- Produces: `start_short`/`start_overview` akzeptieren `"language"?: str` (validiert); SP2 liest `args.get("language")`.

- [ ] **Step 1: Failing Tests schreiben** (in `tests/test_chat_router.py`)

```python
def test_start_short_accepts_optional_language() -> None:
    reply = json.dumps({
        "tool": "start_short",
        "args": {"topic": "VibeMind", "language": "English"},
    })
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: reply)
    assert decision["args"]["language"] == "English" and decision["fallback"] is False


def test_start_short_language_validation_rejects_garbage() -> None:
    replies = iter([
        json.dumps({"tool": "start_short",
                    "args": {"topic": "t", "language": "en-US_123!"}}),
        json.dumps({"tool": "start_short", "args": {"topic": "t", "language": "English"}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["args"]["language"] == "English" and decision["fallback"] is False


def test_start_overview_accepts_optional_language() -> None:
    reply = json.dumps({
        "tool": "start_overview",
        "args": {"topic": "VibeMind", "language": "Spanish"},
    })
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: reply)
    assert decision["args"]["language"] == "Spanish" and decision["fallback"] is False


def test_system_prompt_carries_the_language_rule() -> None:
    from laura.chat.router import _SYSTEM_PROMPT

    assert "language of the user's instruction" in _SYSTEM_PROMPT
    assert "auf Englisch" in _SYSTEM_PROMPT   # explicit-mention example verbatim
    assert '"language": "English"' in _SYSTEM_PROMPT
```

- [ ] **Step 2: FAIL laufen lassen** — `uv run pytest tests/test_chat_router.py` (aus `services/local-api`): unbekanntes Argument wird heute zwar nicht abgelehnt, aber die Prompt-Pins fehlen und die Garbage-Validierung greift nicht → mindestens `test_start_short_language_validation_rejects_garbage` und der Prompt-Pin scheitern.

- [ ] **Step 3: Implementieren**

1. `_validate_args`: gemeinsamer Helfer + Einhängen in beide Zweige:

```python
_LANGUAGE_RE = re.compile(r"^[A-Za-z][A-Za-z ]{0,31}$")


def _validate_optional_language(args: dict[str, Any]) -> str | None:
    if "language" not in args:
        return None
    language = args["language"]
    if not isinstance(language, str) or _LANGUAGE_RE.fullmatch(language) is None:
        return (
            "language must be an English language name (letters/spaces, max 32 chars), "
            'e.g. "German" or "English"'
        )
    return None
```

   (`import re` oben ergänzen, falls noch nicht vorhanden.) In den `start_short`-Zweig
   nach dem Format-Check und in den `start_overview`-Zweig nach
   `_validate_optional_target_seconds` jeweils `error = _validate_optional_language(args)`
   + Rückgabe bei Fehler einfügen.

2. `_SYSTEM_PROMPT`: die beiden Tool-Bullets erweitern —
   `start_short: {"topic": str, "target_seconds"?: int, "format"?: ..., "language"?: str}`
   (analog start_overview) — und im Rules-Absatz ergänzen:

```text
 Set "language" on start_short/start_overview to the language of the user's instruction
 (an English language name: "German", "English", "Spanish", ...); if the instruction
 explicitly names a target language ('auf Englisch', 'in english'), that explicit mention
 wins. Examples: 'bau mir einen Short über X' -> {"language": "German"}; 'build me a
 short about X' -> {"language": "English"}; 'bau mir einen Short über X auf Englisch' ->
 {"language": "English"}.
```

- [ ] **Step 4: Grün + Gates** — `uv run pytest tests/test_chat_router.py`, bare `uv run mypy`, `uv run ruff check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/chat/router.py services/local-api/tests/test_chat_router.py
git commit -m "feat(chat): router detects the instruction language for new productions"
```

---

### Task SP2: Executor reicht `language` durch

**Files:**
- Modify: `services/local-api/src/laura/chat/executor.py` (`_handle_start_short` ~311-343, `_handle_start_overview` ~345-378)
- Test: `services/local-api/tests/test_chat_executor.py`

**Interfaces:**
- Consumes: SP1s `args.get("language")`; `run_project_auto_short(db, project_id, *, topic, target_seconds, format, language)` akzeptiert `language` BEREITS (api/short_creator.py:488-496); der Overview-Service akzeptiert `language` ebenfalls (echo-only — Overview nutzt Original-Audio; der Durchreicher ist trotzdem korrekt und zukunftssicher). Der bestehende `_optional`-Helfer der Datei liefert Defaults.
- Produces: `BoardMeta.language` trägt den erkannten Wert für neue Chat-Sessions.

- [ ] **Step 1: Failing Tests schreiben** (Stil der Datei: die start_short-Tests fangen den Service-Aufruf per Monkeypatch — deren Capture-Muster wiederverwenden)

```python
def test_start_short_threads_language_to_the_service(seeded_project) -> None:
    captured: dict[str, Any] = {}

    def fake_service(db, project_id, *, topic, target_seconds, format, language):
        captured.update(topic=topic, language=language)
        return {"session_id": "s1", "job_id": "j1", "warnings": []}

    _run_start_short(  # helper style of the file
        seeded_project, args={"topic": "VibeMind", "language": "English"},
        service=fake_service,
    )
    assert captured["language"] == "English"


def test_start_short_defaults_language_to_german(seeded_project) -> None:
    captured: dict[str, Any] = {}
    # same fake, args WITHOUT language
    ...
    assert captured["language"] == "German"
```

(Der Implementer passt die zwei Tests an das reale Monkeypatch-Muster der Datei an —
existiert dort ein Capture über `monkeypatch.setattr` auf `run_project_auto_short`,
genau das verwenden; die Asserts sind bindend. Analog EIN Test für start_overview,
sofern dessen Handler den Service mit language aufrufen kann.)

- [ ] **Step 2: FAIL laufen lassen.**

- [ ] **Step 3: Implementieren** — in `_handle_start_short`:

```python
    language = str(_optional(args, "language", "German"))
```

   und `language=language` an den `run_project_auto_short`-Aufruf. Analog
   `_handle_start_overview` an seinen Service-Aufruf (der Parameter existiert dort;
   falls der Overview-Service-Aufruf im Handler KEIN language akzeptiert, den Handler
   unverändert lassen und im Report als „Overview exempt (v1)" dokumentieren — die Spec
   deckt beide Ausgänge).

- [ ] **Step 4: Grün + Gates** — `uv run pytest tests/test_chat_executor.py tests/test_chat_router.py`, bare `uv run mypy`, `uv run ruff check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/chat/executor.py services/local-api/tests/test_chat_executor.py
git commit -m "feat(chat): start handlers thread the detected language into the session"
```

---

### Task SP3: `set_board_language`-Tool + Charter-Zeile

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board.py` (neue Methode `set_language`)
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (Tool `set_board_language` in `build_production_tool_specs`)
- Modify: `services/local-api/src/laura/short_creator/production_agents.py` (`scene_author.tool_names` + System-Message-Satz)
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py` (`build_production_task`: Sprachwechsel-Zeile)
- Test: `services/local-api/tests/test_production_tools_write.py` (oder die Datei, in der die anderen Board-Write-Tools getestet sind — real prüfen), `services/local-api/tests/test_production_agents.py`, `services/local-api/tests/test_production_pipeline.py` (bestehender Vier-Tool-Pin bleibt grün), `services/local-api/tests/test_production_orchestrator.py` (Task-Text-Pin)

**Interfaces:**
- Consumes: `Board.set_script_approved`/`clear_script_approval` als Muster für den atomaren Meta-Write (board.py ~458-496: `self._lock`, `model_copy(update=...)`, `_write_atomic(self.root / "meta.json", ...)`); `ToolSpec`-Closure-Muster in `build_production_tool_specs`; `production_agent_specs`-Roster + `EXPECTED_ASSIGNMENTS`-Exakt-Test.
- Produces: `Board.set_language(language: str) -> None`; Tool `set_board_language(language: str) -> dict` mit `{"ok": True, "previous": <alt>, "language": <neu>}` bzw. `{"ok": False, "reason": ...}`.

- [ ] **Step 1: Failing Tests schreiben**

Board-Methode (in der Board-Testdatei, Muster der set_script_approved-Tests):

```python
def test_set_language_updates_meta_atomically(tmp_board) -> None:
    board = tmp_board(language="German")
    board.set_language("English")
    assert board.meta().language == "English"
```

Tool (in der Write-Tools-Testdatei, deren Fixture-Stil):

```python
def test_set_board_language_switches_and_reports_previous(write_tool_env) -> None:
    result = _call_tool(write_tool_env, "set_board_language", language="English")
    assert result == {"ok": True, "previous": "German", "language": "English"}
    assert write_tool_env.board.meta().language == "English"


def test_set_board_language_rejects_garbage_without_writing(write_tool_env) -> None:
    result = _call_tool(write_tool_env, "set_board_language", language="  ")
    assert result["ok"] is False
    assert write_tool_env.board.meta().language == "German"
```

Roster/Pins:

```python
# test_production_agents.py: EXPECTED_ASSIGNMENTS["scene_author"] erhält
# "set_board_language"; der qa_reviewer-Exakt-Tupel-Test bleibt UNVERÄNDERT grün.
# test_production_pipeline.py: test_tail_tool_menu_is_pinned_exactly bleibt UNVERÄNDERT.
# test_production_orchestrator.py: Task-Text-Pin:
def test_task_text_carries_the_language_switch_rule(seeded_env) -> None:
    task = build_production_task(...)  # bestehende Aufrufform der Datei
    assert "set_board_language" in task
```

- [ ] **Step 2: FAIL laufen lassen.**

- [ ] **Step 3: Implementieren**

`board.py` (direkt unter `clear_script_approval`):

```python
    def set_language(self, language: str) -> None:
        """Switch the board's script/voice/caption language (user-requested via chat).

        Same locked atomic-meta-write pattern as :meth:`set_script_approved`. Existing
        artifacts are untouched — the team rewrites the script afterwards, and the
        content-hash change re-arms the approval gate on its own."""
        with self._lock:
            meta = self.meta().model_copy(update={"language": language})
            _write_atomic(self.root / "meta.json", meta.model_dump_json(indent=2))
```

`production_tools.py` (in `build_production_tool_specs`, bei den anderen Write-Tools;
Validierung wie der Router — Buchstaben/Leerzeichen, ≤ 32):

```python
    def set_board_language(language: str) -> dict[str, Any]:
        """Switch the production language (script/voice/captions) for this board.

        Call this FIRST when the user asks for another language, then rewrite every
        chapter via save_script_chapter — it picks the new language up automatically."""
        try:
            cleaned = (language or "").strip()
            if not cleaned or len(cleaned) > 32 or not all(
                c.isalpha() or c == " " for c in cleaned
            ):
                return {"ok": False, "reason": "language must be an English language "
                                               "name (letters/spaces, max 32 chars)"}
            previous = board.meta().language
            board.set_language(cleaned)
            return {"ok": True, "previous": previous, "language": cleaned}
        except Exception as exc:  # tool must never kill the agent loop
            return {"ok": False, "reason": str(exc)[:200]}
```

`production_agents.py`: `"set_board_language"` in `scene_author.tool_names` aufnehmen +
ein Satz in dessen System-Message: bei Sprachwunsch zuerst `set_board_language`, dann
jedes Kapitel in der neuen Sprache neu schreiben.

`production_orchestrator.py` `build_production_task`: eine Zeile im Task-Text (bei den
bestehenden Regel-Absätzen): "If the user message asks for another language, the Scene
Author calls set_board_language FIRST and then rewrites every chapter in that language —
never leave chapters in the old language behind."

- [ ] **Step 4: Grün + Gates** — `uv run pytest tests/test_production_tools_write.py tests/test_production_agents.py tests/test_production_pipeline.py tests/test_production_orchestrator.py` (Dateinamen an den realen Bestand anpassen), bare `uv run mypy`, `uv run ruff check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/board.py services/local-api/src/laura/short_creator/production_tools.py services/local-api/src/laura/short_creator/production_agents.py services/local-api/src/laura/short_creator/production_orchestrator.py <testdateien>
git commit -m "feat(short-creator): set_board_language tool + language-switch charter rule"
```

---

### Task SP4: Volle Gates + Doku + manuelle Prüfliste

**Files:**
- Modify: `docs/00-overview.md` (ein Satz im Chat-Absatz: Sprache folgt der Anweisung; „mach das in english" wechselt), `tasks/todo.md` (Block im etablierten Stil)

- [ ] **Step 1:** Backend voll: `uv run pytest -p no:cacheprovider` (VORDERGRUND, timeout 600000, echte Summary-Zeile — NIE backgrounden/Monitor). Dann bare `uv run mypy`, `uv run ruff check src tests`.
- [ ] **Step 2:** Desktop: `pnpm test -- --run` + `pnpm typecheck` aus `apps/desktop` (keine Frontend-Änderung — der grüne Lauf beweist das).
- [ ] **Step 3:** Doku-Satz + todo.md-Block („Sprache folgt dem Input", SP1-SP4-Bullets, **Verifiziert:**, **Exit:**).
- [ ] **Step 4:** Manuelle Prüfliste in den Report (je „manuell zu prüfen"): (a) „build me a 45s short about X" → englisches Script an der Gate-Karte; (b) Follow-up „mach das in english" auf ein deutsches Video → Team ruft set_board_language (Ledger), Script englisch, Gate re-armed, nach Freigabe englische Voice; (c) deutscher Auftrag bleibt deutsch (Regression). App-Neustart mit detachtem Backend.
- [ ] **Step 5: Commit**

```bash
git add docs/00-overview.md tasks/todo.md
git commit -m "docs: language follows the instruction + todo tick"
```

---

## Self-Review (Plan gegen Spec)

- **Spec-Abdeckung:** Start-Erkennung+Validierung+Prompt (SP1) ✓; Durchreiche+Default (SP2, Overview-Ausnahme dokumentiert) ✓; set_board_language+Charter+Pins (SP3) ✓; keine Whitelist (Global Constraints) ✓; Fehlerbild (Tool-ok:False, Router-Validierungsrunde) ✓; Doku/Prüfliste (SP4) ✓.
- **Platzhalter:** SP2-Testcode markiert die Anpassung ans reale Monkeypatch-Muster explizit; ein `...` steht NUR im zweiten Testskelett mit bindenden Asserts darüber/darunter — der Implementer füllt das Muster der Datei ein. Kein leerer Schritt.
- **Typkonsistenz:** `language: str` durchgängig; `set_board_language`-Ergebnisform `{ok, previous, language}` in Tool und Test identisch; `Board.set_language(language: str) -> None` (SP3 Producer=Consumer).
