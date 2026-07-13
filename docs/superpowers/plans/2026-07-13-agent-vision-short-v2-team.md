# Agent-Vision-Short v2 — Team (Slice 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slice 3 der Spec [2026-07-13-agent-vision-short-v2-design.md](../specs/2026-07-13-agent-vision-short-v2-design.md): das **v2-Produktionsteam** — 5 Spezialisten (Vision-Reviewer, Story-Architekt, Szenen-Autor, Coding-Agent, QA-Reviewer) unter Magentic-One, die ausschließlich über das **Production Board** (Slice 1) arbeiten, plus **Modell-Pool-Rotation** für Free-Tier-Resilienz. Sessions/API = Slice 4 (nicht hier).

**Architecture:** Drei neue Module neben dem unangetasteten v1-Team: `production_tools.py` (board-gebundene ToolSpecs — Wahrnehmung via VLM, Artefakt-Writes pydantic-validiert, deterministische Coding-Agent-Werkzeuge), `production_agents.py` (AgentSpecs + Team-Builder nach dem Muster von `agents.py`/`magentic.py`), `production_orchestrator.py` (Viral-Arc-Task-Vertrag, resume-bewusst, `execute`-Seam, Stage-A/B-Leiter). Rotation: `RotatingChatClient` in `providers.py` (Pool pro Rolle, sticky failover) und Modell-Pool im `OpenRouterDescribeBackend`.

**Tech Stack:** Python 3.11 · uv · pytest · pydantic v2 · AutoGen 0.4+ (lazy, optional `autoshort`) · bestehende Board-/Render-/Voice-Schicht aus Slice 1+2.

## Global Constraints

- **v1 bleibt unangetastet:** `agents.py`, `graph.py`, `orchestrator.py`, `toolset.py`, `stream.py`, bestehende Endpunkte — keine Verhaltensänderung. v2 ist additiv.
- **Autogen lazy:** kein Modul importiert autogen auf Modulebene; Backend startet ohne `autoshort`.
- **Agenten urteilen, Tools führen aus:** jede inhaltliche Entscheidung im Prompt/Agent; Frames/Trims/Render/Hashes deterministisch im Tool. Board-Writes sind pydantic-validiert; Fehler gehen als Tool-Result zurück (Selbstkorrektur), nie Exception an den Agenten-Loop.
- **Board-Kette** (`scene_reviews → storyline → script → voice → cutlist → render_report → qa_report`) und Frames-Invarianten (Ganzzahl-Frames, end-exclusive) gelten überall.
- VLM-/Voice-/Render-Abhängigkeiten sind **injectable** (Tests LLM-/Netz-/ffmpeg-frei); `describe()`-Backends geben `""` zurück statt zu werfen; `review_scene` degradiert auf Transkript (`degraded=true`), nie Crash.
- Zoom-Option-Format an `tool_render_segments`: `zoom=[{"roi": {x,y,w,h}, "zoom_start_s": s} | None, ...]` index-aligned; `fit="blur"`, `vertical=True`, `out_size=(1080,1920)` sind die v1-Produktions-Defaults.
- Typecheck-Gate ist **voller Scope**: bare `uv run mypy` (CI prüft src+tests) → 0 Fehler; ruff sauber; kein `print`.
- Commits: Conventional Commits, explizite `git add`-Pfade; Codex-Sperrgebiet (`services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py`) tabu.
- Doku-Prosa Deutsch; Code/Identifier/Kommentare/Commits Englisch.

**Arbeitsverzeichnis aller Kommandos:** `services/local-api/`.

**Referenz-Signaturen (Stand HEAD, verifiziert):** `AgentSpec(name, description, system_message, tool_names, max_tool_iterations=1)` (agents.py:28); `ToolSpec(name, description, func)` + Closure-über-`db`-Muster (toolset.py:170); `build_magentic_team(db, config, *, stage) -> MagenticOneGroupChat` (magentic.py:24); `StageOutcome(status, weak, summary, team, stage)` + `ExecuteFn` + `_run_stage`/`_safe_execute`/`_result` (orchestrator.py); `Board`/`board_models` (Slice 1); `context._scene_src_ranges(db, asset_id, ...)`, `context.scene_transcripts`, `context._default_extract`, `analysis/transition_review.extract_frames`; `resolve_voice_backend().synthesize(text, out_path) -> {"ok", "path", "timings_path"?}` mit Sidecar `{"words": [{"text","start_s","end_s"}]}`; `tool_render_segments(db, asset_id, segments, *, captions, fit, vertical, out_size, voiceover_path, voiceover_text, zoom)` (mcp/tools.py, Slice 2); `RetryingChatClient` + `plan_client`/`build_model_client(config, *, role, stage)` (providers.py).

---

### Task 1: Modell-Pool + `RotatingChatClient` (providers.py)

**Files:**
- Modify: `services/local-api/src/laura/short_creator/providers.py`
- Modify: `.env.example` (Doku-Zeilen)
- Test: `services/local-api/tests/test_providers_rotation.py` (neu)

**Interfaces:**
- Consumes: bestehendes `AgentConfig` (frozen dataclass), `plan_client`, `RetryingChatClient`, `build_model_client`.
- Produces: `AgentConfig.model_pool: tuple[str, ...] = ()` (neues Feld mit Default — bestehende Konstruktionen bleiben gültig); `_parse_model_pool(raw: str | None, first: str) -> tuple[str, ...]`; `_is_flaky(exc: BaseException) -> bool`; `class RotatingChatClient` (sticky failover über vorgebaute Clients); `build_model_client` liefert bei Pool>1 (Stage A, kind != "ollama") einen `RotatingChatClient` aus je einem `RetryingChatClient` pro Pool-Modell. Env: `LAURA_AGENT_MODEL_POOL` (Komma-Liste; das aktive `LAURA_AGENT_MODEL` wird dedupliziert vorangestellt).

- [ ] **Step 1: Write the failing tests**

```python
"""Model-pool rotation: sticky failover across per-model clients."""

from typing import Any

import pytest

from laura.short_creator.providers import (
    RotatingChatClient,
    _is_flaky,
    _parse_model_pool,
)


class _Exhausted(Exception):
    """Stands in for the error a RetryingChatClient re-raises after its retries."""

    def __init__(self, status_code: int) -> None:
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class _FakeClient:
    def __init__(self, name: str, fail_times: int = 0) -> None:
        self.name = name
        self.fail_times = fail_times
        self.calls = 0

    async def create(self, *args: Any, **kwargs: Any) -> str:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise _Exhausted(429)
        return f"answer from {self.name}"


def test_parse_model_pool_dedupes_and_prepends_first() -> None:
    pool = _parse_model_pool(" b , a,c ,a", first="a")
    assert pool == ("a", "b", "c")
    assert _parse_model_pool(None, first="a") == ("a",)
    assert _parse_model_pool("  ", first="a") == ("a",)


def test_is_flaky_classification() -> None:
    assert _is_flaky(TypeError("choices=None"))
    assert _is_flaky(_Exhausted(503))
    assert _is_flaky(RuntimeError("empty completion content (reasoning-only reply)"))
    assert not _is_flaky(RuntimeError("boom"))
    assert not _is_flaky(_Exhausted(401))


@pytest.mark.asyncio
async def test_rotating_client_advances_and_sticks() -> None:
    first = _FakeClient("m1", fail_times=99)
    second = _FakeClient("m2")
    client = RotatingChatClient([first, second])
    assert await client.create() == "answer from m2"
    assert (first.calls, second.calls) == (1, 1)
    # sticky: the next call goes straight to m2
    assert await client.create() == "answer from m2"
    assert (first.calls, second.calls) == (1, 2)


@pytest.mark.asyncio
async def test_rotating_client_reraises_when_pool_exhausted() -> None:
    client = RotatingChatClient([_FakeClient("m1", fail_times=9), _FakeClient("m2", fail_times=9)])
    with pytest.raises(_Exhausted):
        await client.create()


@pytest.mark.asyncio
async def test_rotating_client_reraises_non_flaky_immediately() -> None:
    class _Auth(_FakeClient):
        async def create(self, *args: Any, **kwargs: Any) -> str:
            raise _Exhausted(401)

    second = _FakeClient("m2")
    client = RotatingChatClient([_Auth("m1"), second])
    with pytest.raises(_Exhausted):
        await client.create()
    assert second.calls == 0


def test_rotating_client_rejects_empty_pool() -> None:
    with pytest.raises(ValueError):
        RotatingChatClient([])
```

Hinweis: prüfe, ob `pytest-asyncio` im Projekt konfiguriert ist (`grep -rn "asyncio_mode\|pytest.mark.asyncio" pyproject.toml tests/ | head`). Falls nicht vorhanden: statt der Marker `asyncio.run(...)`-Wrapper in synchronen Tests verwenden (`def test_...(): assert asyncio.run(client.create()) == ...`) — Verhalten identisch, keine neue Dependency einführen.

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_providers_rotation.py -v`
Expected: FAIL — `ImportError: cannot import name 'RotatingChatClient'`

- [ ] **Step 3: Write the implementation**

3a. `AgentConfig` um ein Feld erweitern (Default hält alle bestehenden Aufrufe gültig): `model_pool: tuple[str, ...] = ()`.

3b. In `resolve_from_env()` (dort, wo `model` gelesen wird) ergänzen:

```python
    model_pool = _parse_model_pool(os.environ.get("LAURA_AGENT_MODEL_POOL"), first=model)
```

und `model_pool=model_pool` in die `AgentConfig`-Konstruktion aufnehmen.

3c. Modul-Funktionen (bei den bestehenden Helpers platzieren):

```python
def _parse_model_pool(raw: str | None, first: str) -> tuple[str, ...]:
    """Comma-separated model pool; the active model always leads, duplicates dropped."""
    names = [first] + [part.strip() for part in (raw or "").split(",")]
    out: list[str] = []
    for name in names:
        if name and name not in out:
            out.append(name)
    return tuple(out)


def _is_flaky(exc: BaseException) -> bool:
    """The failure classes RetryingChatClient retries — i.e. 'this model/day is bad',
    not 'this request is wrong'. Used by the pool to decide whether to fail over."""
    if isinstance(exc, TypeError):
        return True
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and status in {408, 429, 500, 502, 503, 504}:
        return True
    return isinstance(exc, RuntimeError) and "empty completion content" in str(exc)


class RotatingChatClient:
    """Sticky failover across a pool of per-model clients.

    Each pool entry is expected to be a RetryingChatClient (its own retries
    exhausted before an exception reaches us).  On a flaky-class failure the
    pool advances to the next model and retries the SAME request; the index is
    sticky for the rest of the process (free-tier serving variance is per
    model per day — a bad model stays skipped).  Non-flaky errors re-raise
    immediately; an exhausted pool re-raises the last flaky error.
    """

    def __init__(self, clients: Sequence[Any]) -> None:
        if not clients:
            raise ValueError("model pool must not be empty")
        self._clients = list(clients)
        self._index = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._clients[self._index], name)

    async def create(self, *args: Any, **kwargs: Any) -> Any:
        while True:
            try:
                return await self._clients[self._index].create(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - classify, then advance or re-raise
                if not _is_flaky(exc) or self._index >= len(self._clients) - 1:
                    raise
                self._index += 1
                _log.warning(
                    "model pool: advancing to client %d/%d after flaky failure: %s",
                    self._index + 1,
                    len(self._clients),
                    exc,
                )
```

(`Sequence` aus `collections.abc` importieren; `Any` aus `typing`, falls nicht vorhanden.)

3d. `build_model_client` — im Nicht-Ollama-Zweig (dort, wo heute der einzelne `RetryingChatClient` gebaut wird):

```python
    pool = config.model_pool if stage == "A" and len(config.model_pool) > 1 else (spec.model,)
    clients = []
    for pool_model in pool:
        client_kwargs = dict(kwargs)
        client_kwargs["model"] = pool_model
        clients.append(RetryingChatClient(OpenAIChatCompletionClient(**client_kwargs)))
    if len(clients) == 1:
        return clients[0]
    return RotatingChatClient(clients)
```

(Exakte Variablennamen an den Bestand anpassen — `kwargs`/`spec` heißen ggf. anders; Verhalten: Stage B und Ollama bleiben unverändert Single-Client.)

3e. `.env.example` — im bestehenden Agent-Block dokumentieren:

```
# Optional: comma-separated failover pool for the agent/orchestrator model (stage A).
# The active LAURA_AGENT_MODEL always leads; on repeated empty/5xx/429 failures the
# run advances to the next model and stays there (free-tier serving varies per model/day).
# LAURA_AGENT_MODEL_POOL=openrouter/nvidia/nemotron-3-super-120b-a12b:free,openrouter/google/gemma-4-31b:free,openrouter/tencent/hunyuan-a13b-instruct:free
```

- [ ] **Step 4: Run tests to verify they pass (+ Bestandsschutz)**

Run: `uv run pytest tests/test_providers_rotation.py tests/test_short_creator_providers.py -q`
Expected: alle grün (bestehende Provider-Tests unverändert).

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/short_creator/providers.py tests/test_providers_rotation.py
uv run mypy
git add services/local-api/src/laura/short_creator/providers.py services/local-api/tests/test_providers_rotation.py .env.example
git commit -m "feat(short-creator): model-pool failover (RotatingChatClient, LAURA_AGENT_MODEL_POOL)"
```

---

### Task 2: VLM-Modell-Pool (describe.py)

**Files:**
- Modify: `services/local-api/src/laura/short_creator/describe.py`
- Modify: `.env.example`
- Test: `services/local-api/tests/test_describe_pool.py` (neu)

**Interfaces:**
- Consumes: `OpenRouterDescribeBackend` (bestehend: `__init__(*, api_key, model=None)`, `describe(frames, prompt) -> str`, 2 Versuche, gibt `""` bei Fehlern), `resolve_describe_backend()`.
- Produces: `OpenRouterDescribeBackend(*, api_key, model=None, models: Sequence[str] | None = None)` — `models` (dedupliziert, `model`/Default führt) wird der Reihe nach probiert, **sticky** auf dem zuletzt erfolgreichen Index; `resolve_describe_backend` liest `LAURA_VLM_MODEL_POOL` (Komma-Liste). Rückgabesemantik unverändert: nie werfen, `""` = alles fehlgeschlagen.

- [ ] **Step 1: Write the failing tests**

Vorbild für die bestehende Testtechnik: `grep -rn "OpenRouterDescribeBackend" tests/ | head` — vorhandene describe-Tests zeigen, wie HTTP gemockt wird (urllib-Monkeypatch). Die Pool-Logik selbst wird über einen internen Seam getestet: extrahiere den Ein-Modell-Versuch in eine Methode `_attempt(self, model: str, frames: list[bytes], prompt: str) -> str`, die die Tests monkeypatchen.

```python
"""VLM model-pool failover on the OpenRouter describe backend."""

from laura.short_creator.describe import OpenRouterDescribeBackend


def _backend(models: list[str]) -> OpenRouterDescribeBackend:
    return OpenRouterDescribeBackend(api_key="test-key", models=models)


def test_pool_advances_on_empty_and_sticks(monkeypatch) -> None:
    backend = _backend(["m1", "m2"])
    calls: list[str] = []

    def fake_attempt(model: str, frames: list[bytes], prompt: str) -> str:
        calls.append(model)
        return "" if model == "m1" else f"described by {model}"

    monkeypatch.setattr(backend, "_attempt", fake_attempt)
    assert backend.describe([b"x"], "what?") == "described by m2"
    assert calls == ["m1", "m2"]
    # sticky: next call starts at m2
    assert backend.describe([b"x"], "again?") == "described by m2"
    assert calls == ["m1", "m2", "m2"]


def test_pool_returns_empty_when_all_fail(monkeypatch) -> None:
    backend = _backend(["m1", "m2"])
    monkeypatch.setattr(backend, "_attempt", lambda model, frames, prompt: "")
    assert backend.describe([b"x"], "what?") == ""


def test_single_model_default_unchanged(monkeypatch) -> None:
    backend = OpenRouterDescribeBackend(api_key="test-key")
    seen: list[str] = []
    monkeypatch.setattr(
        backend, "_attempt", lambda model, frames, prompt: seen.append(model) or "ok"
    )
    assert backend.describe([b"x"], "what?") == "ok"
    assert len(seen) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_describe_pool.py -v`
Expected: FAIL — `TypeError: ... unexpected keyword argument 'models'` (bzw. fehlendes `_attempt`).

- [ ] **Step 3: Write the implementation**

- `__init__`: `models`-Param aufnehmen; interne Liste = dedupliziert aus `[model or LAURA_VLM_MODEL-Default] + list(models or [])`; `self._preferred = 0`.
- Bestehende `describe`-Logik (Request bauen, 2 Versuche, Fehler loggen, `""`) unverändert in `_attempt(self, model, frames, prompt) -> str` verschieben.
- Neues `describe`: iteriere `offset in range(len(self._models))`, `idx = (self._preferred + offset) % len(self._models)`; erster nicht-leerer Text setzt `self._preferred = idx` und returnt; sonst `""`.
- `resolve_describe_backend`: `pool_raw = os.environ.get("LAURA_VLM_MODEL_POOL")` → `models=[p.strip() for p in pool_raw.split(",") if p.strip()]` an den Backend-Konstruktor (nur im OpenRouter-Zweig).
- `.env.example`: `# LAURA_VLM_MODEL_POOL=nvidia/nemotron-nano-12b-v2-vl:free,qwen/qwen2.5-vl-72b-instruct:free` mit kurzem Kommentar.

- [ ] **Step 4: Run tests to verify they pass (+ Bestandsschutz)**

Run: `uv run pytest tests/test_describe_pool.py -q && uv run pytest tests/ -q -k "describe"`
Expected: neue + alle bestehenden describe-Tests grün.

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/short_creator/describe.py tests/test_describe_pool.py
uv run mypy
git add services/local-api/src/laura/short_creator/describe.py services/local-api/tests/test_describe_pool.py .env.example
git commit -m "feat(short-creator): VLM model pool with sticky failover (LAURA_VLM_MODEL_POOL)"
```

---

### Task 3: `production_tools.py` A — Infrastruktur, Board-Lese-Tools, `review_scene`

**Files:**
- Create: `services/local-api/src/laura/short_creator/production_tools.py`
- Test: `services/local-api/tests/test_production_tools_review.py` (neu)

**Interfaces:**
- Consumes: `Board`/`board_models` (Slice 1); `context.scene_transcripts`, `context._scene_src_ranges`, `context._frame_rate`; `describe.DescribeBackend`-Protokoll; `analysis/transition_review.extract_frames`-Muster (Frames via ffmpeg vom Proxy).
- Produces (Grundgerüst, das Task 4-6 erweitern):

```python
RenderSegmentsFn = Callable[..., dict[str, Any]]  # signature of mcp.tools.tool_render_segments

@dataclass
class ProductionDeps:
    """Injectable seams — tests pass fakes, production passes None (=resolve real)."""
    describe_backend: DescribeBackend | None = None
    frame_extract: Callable[[Database, str, list[int]], list[bytes]] | None = None
    voice_backend: VoiceBackend | None = None          # used from Task 5 on
    render_segments: RenderSegmentsFn | None = None    # used from Task 6 on

def build_production_tool_specs(db: Database, board: Board, *, asset_id: str,
                                deps: ProductionDeps | None = None) -> list[ToolSpec]
```

Tools dieses Tasks: `board_status()`, `get_scene_context(scene_number)`, `review_scene(scene_number)`, `get_reviews()`. `ToolSpec` wird aus `toolset` importiert (gleiches Closure-Muster, `db`+`board` gebunden; Docstring = Tool-Beschreibung).

**Verhaltensvertrag `review_scene`** (Kern von Slice 3):
1. Szene → Quell-Frame-Range über `context._scene_src_ranges`; fehlt die Szene → `{"ok": False, "reason": "unknown scene"}`.
2. 3 Frames (Start/Mitte/Ende der Szene, ganzzahlig) über `deps.frame_extract` (Default: ffmpeg vom Proxy, wie `context._default_extract`, aber Liste von Frames in einem Aufruf).
3. VLM-Prompt (unten) an `deps.describe_backend.describe(frames, prompt)`.
4. Antwort-Parsing `_parse_review_reply(text) -> dict[str, Any] | None`: Code-Fences strippen, Substring erstes `{` bis letztes `}`, `json.loads`; `None` bei jedem Fehler.
5. Clamping: `hook_score` → int 0–10; `best_window.offset_s` ≥ 0 und < Szenendauer, `duration_s` > 0 und auf Szenenrest geklemmt; `roi` nur übernehmen, wenn x/y/w/h nach Klemmen auf [0,1] gültig bleiben (sonst `None`).
6. Backend `None` / leere Antwort / Parse-Fehler → **degraded Review** aus dem Transkript: `description` = Transkript-Snippet (max. 300 Zeichen), `whats_happening=""`, `hook_score=5`, `best_window=(0.0, min(4.0, dauer))`, `roi=None`, `degraded=True`.
7. `SceneReview` bauen (`src_start_frame`/`src_end_frame_exclusive` aus der Range, `model` = Backend-Klassenname oder `""`), `board.save_scene_review(...)` → Rückgabe `{"ok": True, "scene_number": n, "version": v, "degraded": bool, "hook_score": int, "roi": roi_dict_or_None}`.

**VLM-Prompt (verbatim in den Code):**

```python
_REVIEW_PROMPT = (
    "You are reviewing {n} frames (start/middle/end) of scene {scene} from a screen "
    "recording ({duration_s:.1f}s). Transcript of the scene: \"{snippet}\".\n"
    "Reply ONLY with a JSON object, no prose, no code fences:\n"
    "{{\"description\": str (what is on screen),\n"
    "  \"whats_happening\": str (what changes across the frames),\n"
    "  \"hook_score\": int 0-10 (how visually gripping for a cold viewer),\n"
    "  \"best_window\": {{\"offset_s\": float, \"duration_s\": float}} (strongest moment, relative to scene start),\n"
    "  \"roi\": {{\"x\": float, \"y\": float, \"w\": float, \"h\": float}} | null (normalized 0-1 box around the ONE region a viewer must read; null if the whole frame matters),\n"
    "  \"legibility_notes\": str}}"
)
```

- [ ] **Step 1: Write the failing tests**

DB-Fixture: spiegle den Aufbau aus `tests/test_shorts_segments.py` bzw. `tests/test_short_creator_toolset.py` (Projekt + Asset + Analysis-Run + Transkript + Rough-Cut/Szenen in einer Tmp-SQLite — `grep -rn "def _seed\|rough" tests/test_shorts_segments.py` zeigt das Muster). Der Board-Root ist `tmp_path / "board"`.

Kern-Tests (vollständig ausschreiben, Fixture-Helfer aus den genannten Dateien adaptieren):

```python
def test_review_scene_writes_validated_review(tmp_path, ...) -> None:
    # fake describe backend returns the JSON contract verbatim
    class _Vlm:
        def available(self) -> bool: return True
        def describe(self, frames: list[bytes], prompt: str) -> str:
            assert len(frames) == 3
            return ('{"description": "agent dashboard", "whats_happening": "list scrolls", '
                    '"hook_score": 8, "best_window": {"offset_s": 1.0, "duration_s": 3.0}, '
                    '"roi": {"x": 0.1, "y": 0.2, "w": 0.5, "h": 0.4}, "legibility_notes": "small text"}')
    deps = ProductionDeps(describe_backend=_Vlm(), frame_extract=lambda db, a, frames: [b"jpg"] * len(frames))
    specs = {s.name: s for s in build_production_tool_specs(db, board, asset_id=ASSET, deps=deps)}
    out = specs["review_scene"].func(scene_number=1)
    assert out["ok"] and out["degraded"] is False and out["hook_score"] == 8
    reviews = board.scene_reviews()
    assert reviews[0].roi is not None and reviews[0].roi.w == 0.5


def test_review_scene_degrades_without_backend(...) -> None:
    deps = ProductionDeps(describe_backend=None, frame_extract=lambda db, a, frames: [])
    out = specs["review_scene"].func(scene_number=1)
    assert out["ok"] and out["degraded"] is True
    assert board.scene_reviews()[0].degraded is True and board.scene_reviews()[0].roi is None


def test_review_scene_degrades_on_garbage_reply(...) -> None:  # backend returns "not json {" → degraded


def test_review_scene_clamps_out_of_range_values(...) -> None:
    # hook_score 99 → 10; best_window offset beyond scene → clamped; roi x+w>1 → roi None


def test_review_scene_unknown_scene(...) -> None:  # {"ok": False, "reason": "unknown scene"}


def test_board_status_and_get_reviews(...) -> None:
    # board_status enthält resume_point (expected scenes aus scene_transcripts) + artifacts
    # get_reviews liefert [{scene_number, hook_score, degraded, has_roi, description<=200}]


def test_get_scene_context_returns_transcript_and_range(...) -> None:
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_production_tools_review.py -v`
Expected: FAIL — `ModuleNotFoundError: laura.short_creator.production_tools`

- [ ] **Step 3: Write the implementation**

Gerüst (Auszug — Modul-Docstring erklärt Board-Bindung + Fallback-Vertrag):

```python
def build_production_tool_specs(
    db: Database, board: Board, *, asset_id: str, deps: ProductionDeps | None = None
) -> list[ToolSpec]:
    d = deps or ProductionDeps()

    def board_status() -> dict[str, Any]:
        """Current production-board state: artifact versions and the resume point."""
        expected = _expected_scenes(db, asset_id)
        status = board.status()
        status["resume_point"] = board.resume_point(expected)
        status["expected_scenes"] = expected
        return {"ok": True, **status}

    def review_scene(scene_number: int) -> dict[str, Any]:
        """Look at 3 real frames of a scene and write the SceneReview to the board. ..."""
        ...
```

`_expected_scenes` = Szenennummern aus `context.scene_transcripts(db, asset_id)`. Frame-Default-Extractor: ein ffmpeg-Aufruf pro Frame über den Proxy (Pfad + Rate wie `context._default_extract`; bei fehlendem Proxy → `[]` → degraded). Alle Tool-Funktionen fangen unerwartete Exceptions und geben `{"ok": False, "reason": str(exc)[:200]}` zurück (Agenten-Loop darf nie an einem Tool sterben).

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_production_tools_review.py -v`
Expected: PASS (7 Tests)

- [ ] **Step 5: Lint + typecheck + commit**

```bash
uv run ruff check src/laura/short_creator/production_tools.py tests/test_production_tools_review.py
uv run mypy
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_review.py
git commit -m "feat(short-creator): production tools — board status/context + VLM scene review with degraded fallback"
```

---

### Task 4: `production_tools.py` B — Storyline- und Skript-Writes

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py`
- Test: `services/local-api/tests/test_production_tools_write.py` (neu)

**Interfaces:**
- Produces (Tools): `save_storyline(red_thread: str, chapters: list[dict[str, Any]])`, `get_storyline()`, `save_script_chapter(chapter: int, lines: list[dict[str, Any]])`, `get_script()`.
- Verträge: Writes validieren über die pydantic-Modelle; `ValidationError` → `{"ok": False, "errors": [<je "loc: msg", max 5>]}` (Agent korrigiert sich); Erfolg → `{"ok": True, "version": v, ...}`. `save_storyline` prüft zusätzlich: jede referenzierte Szene hat ein Review (`board.scene_reviews()`), sonst `{"ok": False, "reason": "scenes without review: [...]"}`. `save_script_chapter` **merged**: bestehende `script.json`-Zeilen anderer Kapitel bleiben, Zeilen des Kapitels werden ersetzt, Sortierung (chapter, Reihenfolge der Liste); `language` default `"de"` beim Erst-Write.

- [ ] **Step 1: Write the failing tests**

```python
def test_save_storyline_happy_and_versioned(...) -> None:
    # 1 Review für Szene 1 anlegen; chapters=[{"chapter":1,"role":"hook","message":"stop",
    # "scene_numbers":[1],"target_seconds":3.0}] → ok, version 1; zweiter save → version 2

def test_save_storyline_rejects_invalid_role(...) -> None:
    # role "outro" → ok False, errors enthalten "role"

def test_save_storyline_rejects_unreviewed_scenes(...) -> None:
    # scene_numbers=[7] ohne Review → ok False, reason nennt 7

def test_save_script_chapter_merges_per_chapter(...) -> None:
    # Kapitel 1 mit 2 Zeilen, Kapitel 2 mit 1 Zeile, dann Kapitel 1 mit 1 NEUEN Zeile:
    # get_script zeigt 2 Zeilen gesamt-Kapitel-1-ersetzt (1+1), Reihenfolge (1,*),(2,*)
    # und die script-Version ist gestiegen; downstream (voice) wäre invalidiert (board.load("voice") is None)

def test_save_script_chapter_validation_error(...) -> None:
    # line ohne text → ok False, errors

def test_get_storyline_and_script_roundtrip(...) -> None:
```

- [ ] **Step 2: Run** `uv run pytest tests/test_production_tools_write.py -v` — Expected: FAIL (Tools existieren nicht)

- [ ] **Step 3: Implementation** — `save_storyline` baut `Storyline(red_thread=..., arc=[Chapter(**c) for c in chapters])` in `try/except ValidationError`; Review-Check davor. `save_script_chapter`: bestehendes `Script` laden (oder leeres Gerüst), Zeilen `[l for l in old if l.chapter != chapter] + [ScriptLine(chapter=chapter, **l) for l in lines]`, sortiert nach `(chapter, laufende Reihenfolge)`; `board.save("script", ...)`. Fehlerformat: `{"ok": False, "errors": [f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors()[:5]]}`.

- [ ] **Step 4: Run** — Expected: PASS (6 Tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/laura/short_creator/production_tools.py tests/test_production_tools_write.py
uv run mypy
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_write.py
git commit -m "feat(short-creator): storyline/script board writes with agent-facing validation errors"
```

---

### Task 5: `production_tools.py` C — Voice-Synthese (Hash-Cache) + Cutlist-Builder

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py`
- Test: `services/local-api/tests/test_production_tools_cutlist.py` (neu)

**Interfaces:**
- Consumes: `voice.resolve_voice_backend` (injectable via `deps.voice_backend`), Timings-Sidecar `{"words": [{"text","start_s","end_s"}]}`, `board_models.VoiceArtifact/Cutlist/CutSegment`, `context._scene_src_ranges`, `context._frame_rate`.
- Produces (Tools): `synthesize_script_voice()`, `build_cutlist(transition_lead_s: float = 0.4)`.
- Pure Helpers (modul-öffentlich, einzeln getestet):

```python
def script_text(script: Script) -> str            # Zeilen in (chapter, list order) mit " " gejoint
def script_hash(script: Script) -> str            # sha256 über script_text
def line_starts(script: Script, words: list[dict[str, Any]]) -> dict[tuple[int, int], float]
    # ordnet jeder Zeile (chapter, scene_number) den start_s ihres ERSTEN Wortes zu:
    # Sidecar-Wörter sind Whitespace-Tokens desselben Textes → Token-Zählung pro Zeile.
```

**Verhaltensverträge:**
- `synthesize_script_voice`: Script vom Board (fehlt → `{"ok": False, "reason": "no script on the board"}`); vorhandenes `voice`-Artefakt mit gleichem `script_hash` → `{"ok": True, "cached": True, ...}` ohne Synthese. Sonst `deps.voice_backend.synthesize(text, <workspace>/voiceovers/<new_id()>.mp3)`; `ok=False` vom Backend wird durchgereicht. Erfolg: `voice_s` = letztes `end_s` aus dem Sidecar (Sidecar fehlt → `None`), `VoiceArtifact(script_hash, mp3_path, timings_path, voice_s)` → `board.save("voice", ...)`.
- `build_cutlist`: Voraussetzungen Storyline + Script + Voice auf dem Board (fehlt → `ok=False` mit Hinweis, was zuerst zu tun ist). Deterministisch:
  1. Szenen in Arc-Reihenfolge (Kapitel, dann `scene_numbers`-Reihenfolge).
  2. Pro Szene: Quellrange aus `_scene_src_ranges`; Review liefert `best_window` + `roi`.
  3. Segmentdauer = `clamp(chapter.target_seconds / len(chapter.scene_numbers), 2.0, best_window.duration_s wenn > 2.0 sonst 2.0)`, zusätzlich auf die Szenendauer geklemmt.
  4. `start_frame = src_start + round(best_window.offset_s * fps)`, geklemmt so dass `start_frame + dur_frames <= src_end`; `end_frame_exclusive = start_frame + max(dur_frames, 1)`.
  5. `zoom_start_s` pro Segment: `line_start = line_starts(script, words)[(chapter, scene)]` (Zeile fehlt → `None` → kein Zoom); Video-Startzeit des Segments = kumulierte Dauer der vorigen Segmente; `zoom_start_s = max(0.0, line_start - video_start + transition_lead_s)`; ist `zoom_start_s >= segment_dauer - 0.7` → `None` (Zoom lohnt nicht mehr). `roi` aus dem Review (None → kein Zoom).
  6. `Cutlist(segments=[CutSegment(order=i, scene_number=…, start_frame=…, end_frame_exclusive=…, roi=…, zoom_start_s=…)])` → `board.save("cutlist", ...)` → `{"ok": True, "segments": n, "total_seconds": …, "with_zoom": k}`.

- [ ] **Step 1: Write the failing tests** — vollständig ausschreiben:

```python
def test_script_text_and_hash_stable() -> None: ...
def test_line_starts_maps_tokens_to_word_times() -> None:
    # script: K1/S1 "Stopp dein Team" (3 Tokens), K1/S2 "Ein Klick" (2 Tokens)
    # words: 5 Einträge mit start_s 0.0,0.4,0.8,1.5,1.9 → S1→0.0, S2→1.5
def test_synthesize_uses_cache_on_same_hash(...) -> None:
    # fake voice backend zählt Aufrufe; 2x synthesize_script_voice → 1 Synthese, 2. cached True
def test_synthesize_reports_backend_failure(...) -> None:  # backend ok False → ok False
def test_build_cutlist_requires_prereqs(...) -> None:      # ohne voice → ok False, reason nennt synthesize
def test_build_cutlist_deterministic_segments_and_zoom(...) -> None:
    # 2 Szenen à 10s (300 Frames @30fps), Reviews mit best_window/roi, Script 2 Zeilen,
    # fake voice sidecar mit bekannten Wortzeiten → exakte start/end_frames, zoom_start_s
    # des 2. Segments = line_start - seg1_dauer + 0.4 (per Hand vorgerechnet im Test)
def test_build_cutlist_clamps_inside_scene(...) -> None:   # best_window am Szenenende → end <= src_end
```

- [ ] **Step 2: Run** — Expected: FAIL (Helpers/Tools fehlen)

- [ ] **Step 3: Implementation** — wie oben spezifiziert; `import hashlib`; fps über `context._frame_rate`; Sidecar lesen mit `json.loads(Path(timings_path).read_text(encoding="utf-8"))["words"]` in try/except → `[]`.

- [ ] **Step 4: Run** — Expected: PASS (7 Tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/laura/short_creator/production_tools.py tests/test_production_tools_cutlist.py
uv run mypy
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_cutlist.py
git commit -m "feat(short-creator): script voice with hash cache + deterministic cutlist builder (zoom timing from word starts)"
```

---

### Task 6: `production_tools.py` D — Render, Export-Sichtung, QA-Report

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py`
- Test: `services/local-api/tests/test_production_tools_render.py` (neu)

**Interfaces:**
- Consumes: `mcp.tools.tool_render_segments` (Slice-2-Signatur mit `zoom=`), `repos.get_export`, `toolset.RENDER_WAIT_SECONDS`-Muster, `board_models.RenderReport/RenderCheck/QaReport/QaFinding`.
- Produces (Tools): `render_production()`, `review_export(at_seconds: list[float] | None = None)`, `save_qa_report(verdict: str, findings: list[dict[str, Any]])`. Plus Typalias `RenderSegmentsFn = Callable[..., dict[str, Any]]` und `ProductionDeps.render_segments` (Default = echtes `tool_render_segments`, Tests injizieren Fakes) sowie interner Helper `_grab_video_frames(path: Path, at_seconds: list[float]) -> list[bytes]` (ffmpeg `-ss <s> -frames:v 1 -f image2pipe` pro Zeitpunkt; Fehler → weniger/keine Frames, nie Exception).

**Verhaltensverträge:**
- `render_production`: Cutlist + Voice vom Board (fehlt → `ok=False` mit Hinweis). `segments=[(s.start_frame, s.end_frame_exclusive) …]`, `zoom=[{"roi": {...}, "zoom_start_s": z} wenn roi und zoom_start_s sonst None …]`; Aufruf `deps.render_segments(db, asset_id, segments, captions=True, fit="blur", vertical=True, out_size=(1080, 1920), voiceover_path=voice.mp3_path, voiceover_text=script_text(script), zoom=zoom)`. Danach Export-Poll (max. `RENDER_WAIT_SECONDS`, 2s-Intervall) bis `ready`/`error`. Checks: `voice_fits` (`video_s + 0.05 >= voice_s`, video_s = Summe Segmentframes/fps), `export_ready`, `has_voice_timings`. `RenderReport(export_id=…, video_s=…, voice_s=…, width=1080, height=1920, checks=[…])` → board → `{"ok": <export ready und alle checks ok>, "export_id": …, "checks": […]}`. **Coding-Agent-Charta im Tool-Docstring**: bei `voice_fits=False` NICHT die Stimme kürzen — Cutlist verlängern (build_cutlist mit größerem Budget) und neu rendern.
- `review_export`: letzter `render_report` vom Board (fehlt → ok False); Export-Pfad via `repos.get_export`; Default-`at_seconds` = `[1.0, video_s/2, video_s-1.5]`; Frames → `deps.describe_backend.describe([frame], QA-Prompt)` einzeln; Rückgabe `{"ok": True, "notes": [{"at_s": s, "note": text}]}`; ohne Backend → `{"ok": True, "notes": [], "degraded": True}`.
- `save_qa_report`: validiert `verdict` ∈ {ship, revise} + Findings über `QaFinding`; wie Task 4 mit `errors`-Rückgabe.

- [ ] **Step 1: Write the failing tests** — u. a.:

```python
def test_render_production_passes_zoom_and_reports(...) -> None:
    # fake render_segments captured kwargs + legt Export-Row 'ready' an (repos direkt)
    # → zoom-Liste index-aligned (Segment ohne roi → None), RenderReport auf dem Board,
    #   ok True, checks alle ok
def test_render_production_voice_fit_check_fails(...) -> None:
    # voice_s künstlich > video_s → ok False, check voice_fits ok=False, Report trotzdem gespeichert
def test_render_production_requires_cutlist(...) -> None:
def test_review_export_collects_notes(...) -> None:      # fake backend, fake _grab via monkeypatch
def test_save_qa_report_validates(...) -> None:           # verdict "maybe" → errors; happy path version 1
```

- [ ] **Step 2: Run** — Expected: FAIL

- [ ] **Step 3: Implementation** — wie spezifiziert; Export-Poll analog `toolset.export_status` (repos.get_export-Schleife); `time.sleep(2.0)` zwischen Polls (Tests: Export ist sofort `ready`, kein Sleep nötig — Schleife prüft Status VOR dem ersten Sleep).

- [ ] **Step 4: Run** — Expected: PASS (5 Tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/laura/short_creator/production_tools.py tests/test_production_tools_render.py
uv run mypy
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_production_tools_render.py
git commit -m "feat(short-creator): render_production with zoom passthrough + export review + qa report"
```

---

### Task 7: `production_agents.py` — Team-Roster + Builder

**Files:**
- Create: `services/local-api/src/laura/short_creator/production_agents.py`
- Test: `services/local-api/tests/test_production_agents.py` (neu)

**Interfaces:**
- Consumes: `AgentSpec` (aus `agents.py` importieren, NICHT duplizieren), `build_production_tool_specs`, `build_model_client`, Magentic-Muster aus `magentic.py` (lesen und spiegeln: Orchestrator-Client + `MagenticOneGroupChat(participants=…, model_client=…, …)` — exakte Parameter beim Implementieren aus `magentic.py` übernehmen).
- Produces:

```python
def production_agent_specs() -> list[AgentSpec]          # pur, kein autogen/db
def build_production_team(db: Database, board: Board, config: AgentConfig, *,
                          asset_id: str, stage: Stage = "A",
                          deps: ProductionDeps | None = None) -> "MagenticOneGroupChat"
```

**Roster (Urteilstyp → Tools → max_tool_iterations):**

| name | tools | iters |
|---|---|---|
| `vision_reviewer` | board_status, get_scene_context, review_scene, get_reviews | 10 |
| `story_architect` | get_reviews, get_scene_context, save_storyline, get_storyline, board_status | 4 |
| `scene_author` | get_storyline, get_reviews, get_scene_context, save_script_chapter, get_script | 6 |
| `coding_agent` | board_status, get_storyline, get_script, get_reviews, synthesize_script_voice, build_cutlist, render_production | 8 |
| `qa_reviewer` | board_status, get_storyline, get_script, review_export, save_qa_report | 5 |

**System-Prompts (verbatim im Code, englisch; Kernsätze pro Agent — vollständige Prompts schreibt der Implementer aus diesen Verträgen, je 5-10 Sätze):**
- vision_reviewer: "You judge only what you SEE. For every expected scene without a review, call review_scene. Never invent visual details; the tool's review is the record. Report which scenes are strongest (hook_score) when done."
- story_architect: "Fill the FIXED viral arc: hook (2-3s) → problem/promise → 3-4 feature chapters (2-3 scenes each, building on each other) → payoff+CTA. Use ONLY reviewed scenes; a first-time viewer must follow every step. Save via save_storyline; fix validation errors it returns."
- scene_author: "Write 1-2 sentences per scene, per chapter, in the video's language (German). Ground every sentence in what the review says is VISIBLE. Energetic, concrete value, no marketing fog, no sleepy phrasing. Save chapter by chapter via save_script_chapter."
- coding_agent: "You execute; you do not change content decisions. Pipeline: synthesize_script_voice → build_cutlist → render_production. Read the checks: if voice does not fit, rebuild the cutlist with a longer budget and re-render — NEVER cut the voice. Report export_id and checks verbatim."
- qa_reviewer: "Judge the RENDERED result: call review_export, check story flow for a cold viewer, caption sync, zoom legibility, full voice present. Verdict ship or revise with concrete findings via save_qa_report. Findings name where and what, no vague words."
- [ ] **Step 1: Write the failing tests**

```python
def test_roster_shape_and_tool_names_resolve(...) -> None:
    specs = production_agent_specs()
    assert [s.name for s in specs] == ["vision_reviewer", "story_architect", "scene_author", "coding_agent", "qa_reviewer"]
    tool_names = {t.name for t in build_production_tool_specs(db, board, asset_id=ASSET, deps=deps)}
    for spec in specs:
        missing = set(spec.tool_names) - tool_names
        assert not missing, f"{spec.name} references unknown tools {missing}"

def test_specs_are_pure_no_autogen(monkeypatch) -> None:
    # production_agent_specs() funktioniert auch, wenn autogen-Import scheitern würde
    # (kein Import auf Modulebene — siehe agents.py-Muster)

def test_prompts_carry_contracts() -> None:
    by_name = {s.name: s for s in production_agent_specs()}
    assert "review_scene" in by_name["vision_reviewer"].system_message
    assert "viral arc" in by_name["story_architect"].system_message.lower()
    assert "german" in by_name["scene_author"].system_message.lower()
    assert "never cut the voice" in by_name["coding_agent"].system_message.lower()
    assert "ship or revise" in by_name["qa_reviewer"].system_message.lower()
```

(`build_production_team` selbst wird nicht ohne autogen getestet — Import-Pfad + RuntimeError-Meldung analog `agents.build_agents` genügt; ein `test_build_team_raises_clear_error_without_autoshort` nur, falls das Repo autogen NICHT installiert hat — prüfen mit `uv run python -c "import autogen_agentchat"`; ist es installiert, stattdessen `test_build_team_constructs` mit fake model client via monkeypatch auf `build_model_client`.)

- [ ] **Step 2: Run** — Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implementation** — Specs als Literale (Muster agents.py); `build_production_team` spiegelt `magentic.build_magentic_team`: lazy import, `build_production_tool_specs` → `FunctionTool`s (Muster `toolset.build_function_tools`: lazy `from autogen_core.tools import FunctionTool`), ein geteilter Agent-Client via `build_model_client(config, role="agent", stage=stage)`, Orchestrator-Client via `role="orchestrator"` — exakt wie in magentic.py.

- [ ] **Step 4: Run** — Expected: PASS

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/laura/short_creator/production_agents.py tests/test_production_agents.py
uv run mypy
git add services/local-api/src/laura/short_creator/production_agents.py services/local-api/tests/test_production_agents.py
git commit -m "feat(short-creator): v2 production roster (5 specialists) + magentic team builder"
```

---

### Task 8: `production_orchestrator.py` — Task-Vertrag, Resume, Leiter, Ergebnis

**Files:**
- Create: `services/local-api/src/laura/short_creator/production_orchestrator.py`
- Test: `services/local-api/tests/test_production_orchestrator.py` (neu)

**Interfaces:**
- Consumes: `orchestrator._safe_execute`, `orchestrator.StageOutcome`, `orchestrator.ExecuteFn` (importieren — gleiches Paket, gleiche Semantik). **NICHT `_run_stage` importieren**: das enthält den v1-Graph-Fallback; v2 implementiert eine eigene kleine Stage-Schleife (A → bei `hard_fail` B, ausschließlich `team="magentic"`). Außerdem `Board`, `context.scene_transcripts`, `repos` (asset/project für `workspace_root`).
- Produces:

```python
def board_root_for(db: Database, asset_id: str, session_id: str) -> Path
    # <project.workspace_root>/agent-runs/<session_id>/board

def build_production_task(db: Database, board: Board, *, asset_id: str, task: str,
                          target_seconds: int) -> str
    # Viral-Arc-VERTRAG + Board-Resume-Status + Asset-Fakten + Pflicht-Reihenfolge (deterministisch)

def run_production(db: Database, config: AgentConfig, *, asset_id: str, session_id: str,
                   task: str, target_seconds: int = 60,
                   execute: ExecuteFn | None = None,
                   deps: ProductionDeps | None = None) -> dict[str, Any]
```

**Verhaltensverträge:**
- `run_production`: Asset fehlt → `{"ok": False, "error": "asset not found", ...}`. Board open-or-create (`BoardMeta(session_id=…, asset_id=…, created_utc=<utcnow isoformat>, task=…, target_seconds=…)`). Task-Text bauen; Stage-Leiter wie v1 (`_run_stage`-Semantik), aber **team="magentic" only** (kein Graph-Fallback für v2); `execute`-Default baut `build_production_team` und führt aus (Muster `orchestrator._default_execute`, `asyncio.run`). Ergebnis = v1-`_result`-Felder **plus** `{"session_id": …, "board": board.status(), "export_id": <aus render_report, sonst None>, "resume_point": …}`.
- `build_production_task` enthält (Reihenfolge fix, deterministisch — Lektion: Verträge in Task-Text): (1) Ziel + Format + `target_seconds`; (2) den **festen Viral-Arc** als Struktur-Vertrag; (3) Board-Stand: `resume_point`, vorhandene Artefakt-Versionen, Anzahl Reviews vs. erwartete Szenen — mit dem expliziten Satz "Artifacts already on the board are DONE — do not redo them; continue at the resume point."; (4) Pflicht-Reihenfolge: reviews → storyline → script → voice+cutlist+render (coding_agent) → qa; (5) Sprachregel (Skript Deutsch) und die Coding-Agent-Charta (voice_fits); (6) QA-Limit: nach einem `revise`-Verdikt maximal EINE Revisionsrunde, danach liefern mit den Findings als Warnung (Spec §10).

- [ ] **Step 1: Write the failing tests**

```python
def test_board_root_under_workspace(...) -> None:
def test_task_text_contains_contract_and_resume(...) -> None:
    # frisches Board → "scene_reviews:1"; nach save_scene_review(1..n)+storyline → task nennt
    # "storyline" DONE-Hinweis + resume "script"; enthält "viral arc", "do not redo"
def test_run_production_creates_board_and_reports(...) -> None:
    # execute-Fake gibt StageOutcome(status="ok", weak=False, summary="done", team="magentic", stage="A")
    # → result ok True, session_id, board-status dict, resume_point vorhanden, export_id None
def test_run_production_reopens_existing_board(...) -> None:
    # vorab Board mit 1 Review anlegen → run_production überschreibt nichts (Review-Version bleibt 1)
def test_run_production_escalates_on_hard_fail(...) -> None:
    # execute-Fake: Stage A hard_fail, Stage B ok → escalated True, stage "B"
def test_run_production_export_id_from_render_report(...) -> None:
    # RenderReport aufs Board legen → result.export_id gesetzt
def test_run_production_never_raises(...) -> None:
    # execute wirft → ok False, status hard_fail (über _safe_execute-Semantik)
```

- [ ] **Step 2: Run** — Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implementation** — wie spezifiziert. `created_utc` via `datetime.now(timezone.utc).isoformat(timespec="seconds")`. Keine autogen-Imports auf Modulebene (Default-`execute` importiert lazy im Funktionskörper).

- [ ] **Step 4: Run** — Expected: PASS (7 Tests)

- [ ] **Step 5: Commit**

```bash
uv run ruff check src/laura/short_creator/production_orchestrator.py tests/test_production_orchestrator.py
uv run mypy
git add services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/tests/test_production_orchestrator.py
git commit -m "feat(short-creator): production orchestrator — resume-aware task contract + magentic ladder"
```

---

### Task 9: Gesamt-Verifikation Slice 3

**Files:** keine neuen.

- [ ] **Step 1:** `uv run pytest -q` → Expected: exit 0 (alles grün; die zoom-E2E-Goldens laufen mit).
- [ ] **Step 2:** `uv run mypy` → 0 Fehler · `uv run ruff check src tests` → clean.
- [ ] **Step 3:** Ledger-Eintrag in `.superpowers/sdd/progress.md`; kein Commit nötig, wenn nichts geändert wurde.
