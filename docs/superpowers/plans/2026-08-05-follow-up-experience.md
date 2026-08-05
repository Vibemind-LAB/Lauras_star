# Follow-up-Erlebnis Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Freie Kritik/Fragen zum Video bekommen gegrundete Antworten mit umsetzbarem Vorschlag, Anpassungs-Sprache landet zuverlässig als Follow-up, und die Fertig-Karte lädt zum Weitermachen ein — Laura wird per Chat wirklich bedienbar.

**Architecture:** Neues Router-Tool `discuss` (User-Text wörtlich) mit Prioritätsregel im Router-Prompt; ein Executor-Handler baut den gegrundeten Kontext (Board-Status, Script, Transkript-Treffer, Thread-Tail) und macht EINEN LLM-Call über einen injizierbaren Runner (Muster: `chat_runner`); `compose_context` bekommt eine Active-Session-Zeile, die `api/chat.py` best-effort ermittelt; die ProductionActionCard zeigt im Done-Zustand eine Hinweiszeile.

**Tech Stack:** Python 3.11 (services/local-api, uv), React/TS (apps/desktop), bestehende Router-/Executor-Muster.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-05-follow-up-experience-design.md` — Entscheidungen dort sind bindend.
- Ein Chat-Turn endet NIE im 500 und nie ohne Antwort: der Discuss-LLM-Call ist zeitbegrenzt (Router-Timeout-Muster) und fällt auf den deterministischen Text „Dazu kann ich gerade nichts Fundiertes sagen — beschreib konkret, was am Video anders sein soll." zurück.
- Vorschlags-Format EXAKT: eine Zeile beginnend mit `Vorschlag: ` gefolgt vom Satz „Antworte ‚ja', dann setze ich das um — oder beschreib es anders." (der Router-Prompt referenziert wörtlich `Vorschlag:`).
- Kein Direkt-Umsetzen ohne Bestätigung; keine neuen Karten-Typen; keine Renderer-/Cutlist-Änderungen; kein neuer persistenter Zustand (das Gedächtnis ist der Thread).
- Die Active-Session-Zeile ist best-effort: jeder Fehler bei ihrer Ermittlung lässt sie weg, der Turn läuft.
- UI-/Antwort-Texte deutsch, Identifier/Kommentare/Commits englisch. Typing strikt (bare `uv run mypy` deckt src+tests), `ruff check src tests`, kein `print`.
- pytest NIE mit zusätzlichem `-q` (addopts hat schon `-q`; doppelt unterdrückt die Summary). Gates je Task VORDERGRUND mit echter Summary-Zeile.
- Explizites `git add <paths>` (nie `-A`). Commits enden mit `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

### Task FE1: Router — `discuss`-Tool + Prompt-Regeln + öffentlicher One-Shot-Runner

**Files:**
- Modify: `services/local-api/src/laura/chat/router.py` (TOOLS, `_SYSTEM_PROMPT`, `_validate_args`, neue Funktion `build_one_shot_runner`)
- Test: `services/local-api/tests/test_chat_router.py` (ergänzen)

**Interfaces:**
- Consumes: bestehende Router-Strukturen (`TOOLS` frozenset, `_SYSTEM_PROMPT`-Toolliste, `_require_str`, `_default_runner(config) -> Callable[[str], str]`).
- Produces: `"discuss"` ∈ `TOOLS` mit Arg `{"text": str}` (nicht-leer); `build_one_shot_runner(config: AgentConfig) -> Callable[[str], str]` — öffentliche Fassade über `_default_runner`, FE2 nutzt sie für den Discuss-Call.

- [ ] **Step 1: Failing Tests schreiben** (in `tests/test_chat_router.py` ergänzen; `_config()` und der Runner-Injektionsstil der Datei werden wiederverwendet)

```python
def test_discuss_is_a_known_tool_with_text_arg() -> None:
    assert "discuss" in TOOLS
    reply = json.dumps({"tool": "discuss", "args": {"text": "warum ist szene 2 so lang?"}})
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: reply)
    assert decision == {
        "tool": "discuss",
        "args": {"text": "warum ist szene 2 so lang?"},
        "fallback": False,
    }


def test_discuss_requires_nonempty_text() -> None:
    replies = iter([
        json.dumps({"tool": "discuss", "args": {"text": ""}}),
        json.dumps({"tool": "discuss", "args": {"text": "ok"}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "discuss" and decision["fallback"] is False


def test_discuss_single_key_shape_normalizes() -> None:
    reply = json.dumps({"discuss": {"text": "die captions sind zu klein"}})
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: reply)
    assert decision["tool"] == "discuss" and decision["fallback"] is False


def test_system_prompt_carries_the_priority_and_proposal_rules() -> None:
    """The prompt work IS the feature for routing quality — pin the load-bearing strings
    so a later prompt edit cannot silently drop them (live incident 2026-08-05: a free
    critique containing 'Transkript' was answered with a transcript card)."""
    from laura.chat.router import _SYSTEM_PROMPT

    assert "discuss" in _SYSTEM_PROMPT
    assert "warum steht das im transkript" in _SYSTEM_PROMPT  # negative example verbatim
    assert "Vorschlag:" in _SYSTEM_PROMPT                      # yes-after-proposal rule
    assert "mach Szene 2 kürzer" in _SYSTEM_PROMPT             # adjustment example


def test_build_one_shot_runner_is_public() -> None:
    from laura.chat.router import build_one_shot_runner

    runner = build_one_shot_runner(_config())
    assert callable(runner)
```

- [ ] **Step 2: FAIL laufen lassen** — `uv run pytest tests/test_chat_router.py` (aus `services/local-api`): `discuss` unbekannt.

- [ ] **Step 3: Implementieren**

In `router.py`:

1. `TOOLS`: `"discuss"` aufnehmen (frozenset + der Exakt-Test `test_every_tool_is_reachable` in der Testdatei bekommt `"discuss"` dazu — den bestehenden Test ANPASSEN, nicht duplizieren).
2. `_validate_args`: neuer Zweig

```python
    if tool == "discuss":
        return _require_str(args, "text")
```

3. `_SYSTEM_PROMPT`-Toolliste ergänzen (nach `approve_script`):

```text
- discuss: {"text": str} — answer a question, critique, or comment about the result or
  process; pass the user's message verbatim as text. Choose this whenever the user is
  ASKING or COMPLAINING about the video, its scenes, its wording, or the transcript
  quality rather than requesting a specific action. Example: 'warum steht das im
  transkript das macht kein sinn' -> discuss (NOT review_transcript — review_transcript
  is ONLY for explicitly asking to SEE the transcript, e.g. 'zeig mir das Transkript').
```

4. Rules-Absatz am Prompt-Ende ergänzen (an den bestehenden „Rules:"-Text anhängen):

```text
 When the context shows an active production session and the message talks about the
 RESULT (video, scenes, cut, captions, wording, transcript quality), prefer discuss or
 follow_up over asset tools. Adjustment requests are follow_up on the active session —
 examples: 'mach Szene 2 kürzer', 'anderes Intro', 'zeig das volle Bild', 'die Captions
 sind zu klein'. If the user agrees ('ja', 'mach das', 'genau') right after an assistant
 message containing a line starting with 'Vorschlag:', choose follow_up with the active
 session and use the text AFTER 'Vorschlag:' as the follow-up text — never the bare
 'ja'.
```

5. Neue öffentliche Funktion direkt unter `_default_runner`:

```python
def build_one_shot_runner(config: AgentConfig) -> Callable[[str], str]:
    """Public facade over the router's one-shot agent runner — the discuss handler
    (chat/executor.py) runs its grounded answer through the same single-agent,
    wall-clock-capped machinery instead of growing a second LLM client path."""
    return _default_runner(config)
```

- [ ] **Step 4: Grün + Gates** — `uv run pytest tests/test_chat_router.py`, bare `uv run mypy`, `uv run ruff check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/chat/router.py services/local-api/tests/test_chat_router.py
git commit -m "feat(chat): discuss tool + result-priority and proposal rules in the router"
```

---

### Task FE2: Executor — gegrundeter Discuss-Handler mit LLM-Seam

**Files:**
- Modify: `services/local-api/src/laura/chat/executor.py` (neuer Handler `_handle_discuss`, Helfer `_matching_segments`, `_discuss_context`; Dispatch in `execute_decision`; neuer Keyword-Param `discuss_runner`)
- Modify: `services/local-api/src/laura/api/chat.py` (Seam-Durchreiche `discuss_runner=getattr(request.app.state, "discuss_runner", None)` beim `execute_decision`-Aufruf)
- Test: `services/local-api/tests/test_chat_executor.py` (ergänzen)

**Interfaces:**
- Consumes: `build_one_shot_runner` (FE1); `_resolve_session_id(messages, session_ref)` — für discuss OHNE `session_ref`-Arg: die Session ist die letzte Action-Karte mit `refs.session_id` im Thread (eigener kleiner Helfer `_latest_session_id(messages) -> str | None`, gleiche Iterationsrichtung wie `_resolve_session_id`); Board-Lesen wie `_handle_approve_script` (lokale Imports `Board`/`board_root_for`); Transkript-Lesen wie die review_transcript-Handler (bestehende Helfer der Datei wiederverwenden — die Segment-Beschaffung existiert dort bereits).
- Produces: `execute_decision(..., discuss_runner: Callable[[str], str] | None = None)`; `_handle_discuss` liefert genau eine Text-Nachricht. FE3 hängt nicht davon ab; FE4 testet nur Frontend.

- [ ] **Step 1: Failing Tests schreiben** (in `tests/test_chat_executor.py`; die Datei hat etablierte Helfer zum Seeden von Konversationen/Sessions/Boards und Assets mit Transkript — DIESE wiederverwenden, Namen an den realen Bestand anpassen; die Verhaltens-Asserts sind bindend)

```python
def test_discuss_answers_via_injected_runner_with_grounded_context(seeded) -> None:
    captured: list[str] = []

    def runner(task: str) -> str:
        captured.append(task)
        return "Das ist die rohe Whisper-Transkription.\nVorschlag: ersetze in Segment 87 'Konfix' durch 'Configs'\nAntworte 'ja', dann setze ich das um — oder beschreib es anders."

    messages = _run_decision(  # helper style of the file: build decision, call execute_decision
        seeded, tool="discuss", args={"text": "warum steht Konfix im transkript?"},
        discuss_runner=runner,
    )
    [msg] = messages
    assert msg["kind"] == "text" and "Vorschlag:" in msg["content"]["text"]
    task = captured[0]
    assert "Konfix" in task                       # transcript hit made it into the context
    assert "resume_point" in task or "Status" in task  # board summary present


def test_discuss_matches_segments_by_bigram_and_by_explicit_number() -> None:
    from laura.chat.executor import _matching_segments

    segments = [
        {"index": 86, "text": "Dele herzunehmen, also welche Modelle du haben willst."},
        {"index": 87, "text": "Du kannst Konfix aktuell halten, uns weiter und sofort."},
        {"index": 88, "text": "Was haben wir noch?"},
    ]
    hits = _matching_segments("warum steht Konfix aktuell da drin", segments)
    assert [h["index"] for h in hits] == [87]
    hits = _matching_segments("Segment 88 macht keinen Sinn", segments)
    assert [h["index"] for h in hits] == [88]
    assert _matching_segments("völlig anderes thema", segments) == []


def test_discuss_without_session_still_answers(seeded_no_session) -> None:
    messages = _run_decision(
        seeded_no_session, tool="discuss", args={"text": "was kannst du eigentlich?"},
        discuss_runner=lambda task: "Ich baue Shorts aus deinen Videos.",
    )
    [msg] = messages
    assert msg["content"]["text"].startswith("Ich baue Shorts")


def test_discuss_runner_failure_falls_back_deterministically(seeded) -> None:
    def broken(task: str) -> str:
        raise TimeoutError("model down")

    messages = _run_decision(
        seeded, tool="discuss", args={"text": "hm?"}, discuss_runner=broken,
    )
    [msg] = messages
    assert "nichts Fundiertes" in msg["content"]["text"]


def test_discuss_empty_runner_reply_falls_back(seeded) -> None:
    messages = _run_decision(
        seeded, tool="discuss", args={"text": "hm?"}, discuss_runner=lambda t: "   ",
    )
    [msg] = messages
    assert "nichts Fundiertes" in msg["content"]["text"]
```

- [ ] **Step 2: FAIL laufen lassen** — `uv run pytest tests/test_chat_executor.py -k discuss`.

- [ ] **Step 3: Implementieren**

In `executor.py`:

```python
_DISCUSS_FALLBACK_TEXT = (
    "Dazu kann ich gerade nichts Fundiertes sagen — beschreib konkret, "
    "was am Video anders sein soll."
)

_DISCUSS_SYSTEM = (
    "You are Laura's editor-side voice. Answer the user's question or critique about "
    "their video project, in the user's language, short and concrete. Use ONLY the "
    "provided context — never invent transcript content, scenes, or status. If the "
    "critique is actionable as a production change, end with EXACTLY one line starting "
    "with 'Vorschlag: ' containing the concrete follow-up instruction, followed by the "
    "sentence \"Antworte 'ja', dann setze ich das um — oder beschreib es anders.\" "
    "If nothing is actionable, end without a Vorschlag line."
)


def _latest_session_id(messages: list[dict[str, Any]]) -> str | None:
    """The most recent action card's refs.session_id — discuss has no session_ref arg;
    the active session is simply the last one the thread talked about."""
    for message in reversed(messages):
        content = message.get("content") or {}
        refs = content.get("refs") or {}
        session_id = refs.get("session_id")
        if isinstance(session_id, str) and session_id:
            return session_id
    return None


_SEGMENT_NUMBER_RE = re.compile(r"[Ss]egment\s+(\d+)")


def _matching_segments(
    text: str, segments: list[dict[str, Any]], *, limit: int = 3
) -> list[dict[str, Any]]:
    """Transcript grounding for discuss: explicit 'Segment N' mentions win; otherwise
    case-insensitive word-bigrams of the message (words > 2 chars, consecutive pairs)
    contained in a segment's text, scored by hit count, top `limit`."""
    explicit = {int(n) for n in _SEGMENT_NUMBER_RE.findall(text)}
    if explicit:
        return [s for s in segments if s.get("index") in explicit][:limit]
    words = [w for w in re.findall(r"\w+", text.lower()) if len(w) > 2]
    bigrams = {f"{a} {b}" for a, b in zip(words, words[1:], strict=False)}
    if not bigrams:
        return []
    scored: list[tuple[int, dict[str, Any]]] = []
    for seg in segments:
        seg_text = str(seg.get("text", "")).lower()
        hits = sum(1 for bg in bigrams if bg in seg_text)
        if hits:
            scored.append((hits, seg))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [seg for _hits, seg in scored[:limit]]
```

`_discuss_context(db, messages, session_id) -> str`: baut den Task-Text — Reihenfolge und
Auslassungs-Regel („jeder Baustein fehler-tolerant, fehlende Teile weggelassen"):

1. `_DISCUSS_SYSTEM` als Kopf.
2. Board-Kompakt (nur mit Session): lokale Imports wie in `_handle_approve_script`
   (`Board`, `board_root_for`); `status = board.status()` → Zeilen
   `Status: resume_point=<board.resume_point(...)>, gate=<pending/approved>,
   export=<ja/nein>`; QA-Findings aus `board.load("qa_report")` falls vorhanden
   (Verdict + Findings-Texte, je ≤ 200 Zeichen).
3. Script-Zeilen (falls vorhanden): `Kapitel <c> · Szene <s> · <text>` — dieselbe
   Projektion, die der Gate-Karten-Payload nutzt (bestehenden Helfer wiederverwenden).
4. Transkript-Treffer: Asset der Session (`repos.get_production_session` →
   `asset_id`) → Segment-Liste über den BESTEHENDEN Beschaffungsweg der
   review_transcript-Handler → `_matching_segments(user_text, segments)` → Zeilen
   `Segment <index>: <text>`.
5. Thread-Tail: die letzten 6 Nachrichten über `_compact_message` (Import aus
   `router.py` existiert dort als benutzte Form — die kompakte Ein-Zeilen-Projektion
   der Router-Kontexte; genau die wiederverwenden).
6. Abschluss: `User message: <text>` + Anweisung „Answer now."

`_handle_discuss(db, conversation_id, messages, decision, now_utc, discuss_runner)`:

```python
    text = str(decision["args"]["text"])
    session_id = _latest_session_id(messages)
    task = _discuss_context(db, messages, session_id, user_text=text)
    runner = discuss_runner
    if runner is None:
        from .router import build_one_shot_runner
        from ..short_creator.providers import resolve_from_env

        runner = build_one_shot_runner(resolve_from_env())
    try:
        reply = (runner(task) or "").strip()
    except Exception:  # noqa: BLE001 — a chat turn never fails on the model
        logger.warning("discuss runner failed; deterministic fallback", exc_info=True)
        reply = ""
    if not reply:
        reply = _DISCUSS_FALLBACK_TEXT
    return [_append_text(db, conversation_id, reply, now_utc)]
```

Dispatch in `execute_decision`: Signatur um `discuss_runner: Callable[[str], str] | None = None`
erweitern (Docstring-Absatz analog zum `principal`-Absatz), Zweig
`if tool == "discuss": return _handle_discuss(db, conversation_id, messages, decision, now_utc, discuss_runner)`
— an der Stelle, an der `messages` bereits geladen sind (wie bei `follow_up`).

In `api/chat.py`: beim `execute_decision`-Aufruf
`discuss_runner=getattr(request.app.state, "discuss_runner", None),` ergänzen
(Zeile ~169-172, direkt neben dem bestehenden `principal=principal`).

- [ ] **Step 4: Grün + Gates** — `uv run pytest tests/test_chat_executor.py tests/test_chat_api.py tests/test_chat_router.py`, bare `uv run mypy`, `uv run ruff check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/chat/executor.py services/local-api/src/laura/api/chat.py services/local-api/tests/test_chat_executor.py
git commit -m "feat(chat): grounded discuss handler with injectable one-shot runner"
```

---

### Task FE3: Active-Session-Zeile im Router-Kontext

**Files:**
- Modify: `services/local-api/src/laura/chat/router.py` (`compose_context` um `active_session` erweitern)
- Modify: `services/local-api/src/laura/api/chat.py` (best-effort Ermittlung vor dem Router-Call)
- Test: `services/local-api/tests/test_chat_router.py`, `services/local-api/tests/test_chat_api.py`

**Interfaces:**
- Consumes: `_latest_session_id` (FE2, aus `executor.py` importieren — eine Quelle für „aktive Session", kein Duplikat); Board-Lesen wie überall (`Board.open(board_root_for(...))`).
- Produces: `compose_context(..., active_session: dict[str, str] | None = None)`; Zeile `Active production session: <id> (<state>)` mit `state` ∈ {`done+export`, `awaiting-approval`, `running`, `failed`, `in-progress`}.

- [ ] **Step 1: Failing Tests schreiben**

`tests/test_chat_router.py`:

```python
def test_compose_context_renders_active_session_line() -> None:
    ctx = compose_context(
        project={"name": "P", "id": "p1"}, running_jobs=0, messages=[],
        asset_names=["A"], active_session={"id": "s1", "state": "awaiting-approval"},
    )
    lines = ctx.splitlines()
    videos_idx = next(i for i, l in enumerate(lines) if l.startswith("Videos:"))
    assert lines[videos_idx + 1] == "Active production session: s1 (awaiting-approval)"


def test_compose_context_omits_session_line_when_none() -> None:
    ctx = compose_context(project={"name": "P", "id": "p1"}, running_jobs=0, messages=[])
    assert "Active production session" not in ctx
```

`tests/test_chat_api.py` (Stil der Datei: TestClient-Turns mit gefaktem Runner, der den
empfangenen TASK zurückspiegeln kann): ein Test, der eine Konversation mit einer
Action-Karte (refs.session_id auf ein GESEEDETES Board mit script_gate pending) führt und
über einen Runner, der den Task-Text captured, prüft, dass
`Active production session: <sid> (awaiting-approval)` im Router-Kontext ankam; plus ein
Test mit kaputtem Board (Session-Ref auf nicht existierendes Board) → KEINE Session-Zeile,
Turn antwortet trotzdem.

- [ ] **Step 2: FAIL laufen lassen.**

- [ ] **Step 3: Implementieren**

`compose_context`: Parameter `active_session: dict[str, str] | None = None`; nach der
Videos-Zeile (bzw. nach der Projekt-Zeile, wenn keine Videos):

```python
        if active_session is not None:
            lines.append(
                f"Active production session: {active_session['id']} "
                f"({active_session['state']})"
            )
```

`api/chat.py` vor dem `compose_context`-Aufruf:

```python
    active_session: dict[str, str] | None = None
    try:
        from ..chat.executor import _latest_session_id
        from ..short_creator.board import Board
        from ..short_creator.production_orchestrator import board_root_for

        session_id = _latest_session_id(messages)
        if session_id is not None:
            session = repos.get_production_session(db, session_id)
            if session is not None:
                board = Board.open(board_root_for(db, str(session["asset_id"]), session_id))
                status_payload = board.status()
                gate = status_payload.get("script_gate") or {}
                job = repos.get_job(db, str(session["latest_job_id"])) if session.get("latest_job_id") else None
                job_status = (job or {}).get("status")
                if job_status in ("queued", "running"):
                    state = "running"
                elif gate.get("pending"):
                    state = "awaiting-approval"
                elif board.meta().status == "failed":
                    state = "failed"
                elif board.meta().status == "complete":
                    state = "done+export"
                else:
                    state = "in-progress"
                active_session = {"id": session_id, "state": state}
    except Exception:  # noqa: BLE001 — the line is best-effort, the turn always runs
        logger.warning("active-session context line skipped", exc_info=True)
    context = compose_context(
        project=project, running_jobs=running_jobs, messages=messages,
        asset_names=asset_names, active_session=active_session,
    )
```

(`logger` existiert in `api/chat.py` — falls nicht, Modul-Logger nach Projektmuster
anlegen. `board.meta().status`-Feldname gegen `BoardMeta` verifizieren — die Werte
`failed`/`complete`/`active` setzt `run_production` via `set_status`.)

- [ ] **Step 4: Grün + Gates** — `uv run pytest tests/test_chat_router.py tests/test_chat_api.py`, bare `uv run mypy`, `uv run ruff check src tests`.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/chat/router.py services/local-api/src/laura/api/chat.py services/local-api/tests/test_chat_router.py services/local-api/tests/test_chat_api.py
git commit -m "feat(chat): active-session line grounds the router context"
```

---

### Task FE4: Fertig-Karten-Hinweis + volle Gates + Doku

**Files:**
- Modify: `apps/desktop/src/components/chat/ActionCard.tsx` (Done-Zweig mit `result.exportId`)
- Test: `apps/desktop/src/components/chat/ActionCard.test.tsx`
- Modify: `docs/00-overview.md` (ein Satz im Chat-Absatz), `tasks/todo.md` (Block im etablierten Stil)

- [ ] **Step 1: Failing Test** (Stil der Datei: bestehende Done-Zustand-Tests erweitern)

```tsx
it("the done state invites further adjustment", async () => {
  // bestehenden Done-Fixture-Aufbau der Datei wiederverwenden (exportId gesetzt)
  expect(
    screen.getByText(
      "Weiter anpassen: sag z. B. ‚mach den Hook kürzer' — oder frag einfach.",
    ),
  ).toBeTruthy();
});

it("pending and failed states do not show the adjustment hint", async () => {
  expect(screen.queryByText(/Weiter anpassen/)).toBeNull();
});
```

- [ ] **Step 2: FAIL laufen lassen** — `pnpm test -- --run src/components/chat/ActionCard.test.tsx` (aus `apps/desktop`).

- [ ] **Step 3: Implementieren** — im `result.exportId !== null`-Zweig unter dem „▶ ansehen"-Button:

```tsx
<div className="mt-0.5 text-content-faint">
  Weiter anpassen: sag z. B. ‚mach den Hook kürzer' — oder frag einfach.
</div>
```

- [ ] **Step 4: Volle Gates** — Backend voll `uv run pytest -p no:cacheprovider` (VORDERGRUND, ~12 min, echte Summary-Zeile), bare `uv run mypy`, `uv run ruff check src tests`; Desktop `pnpm test -- --run` + `pnpm typecheck`.

- [ ] **Step 5: Doku** — `docs/00-overview.md`: im Chat-Absatz einen Satz ergänzen (deutsch): Fragen und Kritik beantwortet Laura gegrundet und schlägt die Änderung als `Vorschlag:` vor; „ja" setzt sie um. `tasks/todo.md`: Block „Follow-up-Erlebnis (discuss + Session-Grounding)" `[x]` im Stil der Nachbarn (FE1-FE4-Bullets, **Verifiziert:**, **Exit:**).

- [ ] **Step 6: Manuelle Prüfliste in den Task-Report** (je Zeile „manuell zu prüfen"): (a) der Live-Vorfall als Regressionsfall — freie Kritik mit „Transkript" im Wortlaut bei aktiver Session → discuss-Antwort statt Transkript-Karte; (b) „ja" auf eine `Vorschlag:`-Zeile → Follow-up-Lauf startet (Karte „⚙ läuft"); (c) Fertig-Karte zeigt die Hinweiszeile. App-Neustart mit detachtem Backend nötig.

- [ ] **Step 7: Commit**

```bash
git add apps/desktop/src/components/chat/ActionCard.tsx apps/desktop/src/components/chat/ActionCard.test.tsx docs/00-overview.md tasks/todo.md
git commit -m "feat(desktop): done card invites adjustment + follow-up docs"
```

---

## Self-Review (Plan gegen Spec)

- **Spec-Abdeckung:** discuss-Tool+Prioritätsregel+Beispiele (FE1) ✓; gegrundeter Handler mit Bausteinen/Fallback/Seam (FE2) ✓; „ja"-Regel = Router-Prompt (FE1) ✓ (kein neuer Zustand — Spec §3); Active-Session-Zeile (FE3) ✓; Fertig-Karten-Hinweis (FE4) ✓; Fehlerbild (Fallback-Text wörtlich in Global Constraints + FE2) ✓; „Bewusst NICHT" braucht keine Tasks ✓.
- **Platzhalter:** Testcode referenziert Fixture-ROLLEN (`seeded`, `_run_decision`) — die Dateien haben etablierte Äquivalente, Implementer folgt deren Stil (explizit so angewiesen); keine leeren Schritte.
- **Typkonsistenz:** `build_one_shot_runner(config) -> Callable[[str], str]` (FE1→FE2); `discuss_runner: Callable[[str], str] | None` durchgängig; `active_session: dict[str, str] | None` (FE3 Producer=Consumer); `_latest_session_id` einmal definiert (FE2), FE3 importiert.
