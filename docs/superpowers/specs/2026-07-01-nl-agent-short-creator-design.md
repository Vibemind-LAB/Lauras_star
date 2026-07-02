# NL-Agent Short-Creator — Design (AutoGen 0.4, Magentic-One-first)

_Datum: 2026-07-01 · Status: Entwurf, Iteration 1/9 (User-Validierung) · Ziel-Branch: eigener `feat/`-Branch_

## Ziel

Ein Sprachbefehl — „mach mir einen 60s-Short über X" — erzeugt aus einem **hochgeladenen Video**
_oder_ dem **Rough-/Fine-Cut** automatisch einen fertigen 9:16-Short. Ein **Multi-Agent-System auf
AutoGen 0.4** orchestriert dafür Lauras **bereits vorhandene** Fähigkeiten (visuelle Embeddings,
CLIP-Suche, VLM, Shorts-Scoring, Transcript, Übergänge, Render) statt sie neu zu bauen.

## Nicht-Ziele (YAGNI)

- **Keine neue CV/ML.** Embeddings, VLM, Scoring, Render existieren — wir orchestrieren nur.
- **Kein Ersatz** für den manuellen Feinschnitt. Der Agent liefert einen Entwurf, den der Mensch
  weiter bearbeitet (Ergebnis ist ein normaler Rough-Cut/Timeline + gerenderter Short).
- **Kein Zwang zur Cloud.** Local-first bleibt Default; Cloud/Router ist opt-in.
- **Kein Web-/Datei-Surfing** (Magentic-Ones Standard-Domäne) — unsere Agenten arbeiten nur auf
  Lauras eigenen Tools.

## Bestehende Bausteine (Wiederverwendung, nicht Neubau)

| Vision-Element | Vorhandenes Laura-Modul |
|---|---|
| Visuelle Embeddings | `analysis/visual_embed.py`, `embeddings_store.py` (SqliteVectorStore) |
| „Sagt was zu sehen ist" | VLM `transition_review.py` (qwen3-vl via Ollama) **+** CLIP `search_visual_moments` |
| Szenen zu Thema X finden | `visual_query.search_visual_moments` (NL-Query → Frames) |
| Transcript an exakter Stelle ±15s | ASR words/segments (frame-genau) |
| Kandidaten wählen + scoren | `shorts.extract` + `shorts_score.score_candidates` (visuelle Kontinuität/Shift + Speech-Density) |
| Übergänge via Embedding-Ähnlichkeit | `shorts_score._visual_continuity/_visual_shift_at` + `transition_review` |
| Agent-operabel | MCP-Tools: `search_visual_moments`, `extract_shorts`, `similar_segments`, `visual_hook`, `build_roughcut`, `render_timeline`, `next_action` (in `mcp/server.py`) |

**Kernerkenntnis:** ~80 % ist Orchestrierung. Der neue Code ist die Agenten-Schicht + die
Provider-/Team-Verdrahtung.

## Architektur

### Optionales Extra (nicht verhandelbar)

AutoGen ist ein **optionales Extra** (`pip install laura[autoshort]`). Der Backend **startet ohne
AutoGen** und kann weiter ingest/probe/proxy/export. Alle AutoGen-Importe sind **lazy** (erst im
Job-Handler), damit `create_app` ohne die Extra-Dependency lädt. Fehlt das Extra, liefert der
Endpoint `503 feature_unavailable` mit Installationshinweis — wie der VLM-Pfad.

### Provider-Schicht (`short_creator/providers.py`)

Eine **Model-Client-Factory** löst per Env den Motor der Agenten auf. Alle Optionen sind
OpenAI-kompatibel oder haben einen nativen AutoGen-Client:

| Provider | Env | AutoGen-Client | Zweck |
|---|---|---|---|
| **`ollama`** (Default) | `LAURA_AGENT_PROVIDER=ollama` | `OllamaChatCompletionClient(model=…)` | local-first, offline, kostenlos |
| **`9router`** | `LAURA_AGENT_PROVIDER=9router` | `OpenAIChatCompletionClient(base_url="http://localhost:20128/v1", api_key=…, model=…, model_info=…)` | Gateway zu 40+ Providern/100+ Modellen; stärkeres Modell für den Orchestrator |
| **`openai-compat`** | `LAURA_AGENT_PROVIDER=openai-compat` | `OpenAIChatCompletionClient(base_url=$LAURA_AGENT_BASE_URL, …)` | generischer OpenAI-kompatibler Endpoint |

- Modelle konfigurierbar: `LAURA_AGENT_MODEL` (Alltags-Agenten) und `LAURA_ORCHESTRATOR_MODEL`
  (Magentic-One-Orchestrator, darf stärker sein — z.B. via 9router `cc/claude-sonnet-4-5`).
- Für Nicht-OpenAI-Modelle über `OpenAIChatCompletionClient` wird `model_info` (Capabilities:
  `function_calling`, `vision`, `json_output`) explizit gesetzt.
- **9router ist selbst nur eine Provider-Option**, kein Zwang: Default bleibt lokal.

### Orchestrierung: Magentic-One **first**, GraphFlow **fallback**

Bewusste User-Entscheidung: **zuerst Magentic-One**, GraphFlow nur wenn Magentic-One versagt.

- **Primär — `MagenticOneGroupChat`** (`short_creator/magentic.py`): der Orchestrator plant
  (Task-Ledger) und delegiert Subtasks an die Spezialisten, reflektiert (Progress-Ledger) und
  planzt bei Stillstand um. Passt exakt zur Vision („einer schaut, einer beschreibt, einer
  Transcript"). Braucht ein **starkes Orchestrator-Modell** → dafür ist die 9router-Option da.
- **Fallback — `GraphFlow`/`DiGraphBuilder`** (`short_creator/graph.py`): deterministischer Graph
  (unten). Der Graph steuert die Reihenfolge, das LLM nur das Urteil pro Node → **robust mit
  schwachem lokalem Modell**. Greift, wenn Magentic-One fehlschlägt (Exception, Nicht-Terminierung
  bis `MaxMessageTermination`, oder leeres/ungültiges Ergebnis).
- **`short_creator/orchestrator.py`** kapselt die Fallback-Logik:
  `run(topic, source) → try magentic() except/invalid → graph()`. Beide Teams teilen **dieselben
  Agenten + Tools** (Magentic-One ist „simply an AgentChat team"), also kostet der Fallback nur den
  Team-Zusammenbau, nicht neue Agenten.

### Eskalations-Leiter: erst gratis-lokal, dann 9router

Zwei Fallback-Achsen (Orchestrierung + Provider) komponiert zu **einer Kosten-Leiter** — erst alles
Gratis-Lokale ausreizen, dann eskalieren:

- **Stufe A (gratis, lokal, Provider `ollama`):** Magentic-One → bei hartem Fehlschlag GraphFlow.
  Beides auf dem lokalen Modell → Ergebnis für 0 €.
- **Stufe B (Eskalation, Provider `9router`):** greift, wenn Stufe A **„zu schlecht"** ist. Retry
  der Pipeline einmal mit 9router — Magentic-One bekommt endlich das starke Modell, das es braucht.

**„Zu schlecht" — wer entscheidet den A→B-Sprung:**
- **Harter Fehlschlag → automatisch** eskalieren: Magentic-One **und** GraphFlow scheitern/terminieren
  nicht auf Ollama. Stufe A konnte gar nichts liefern → kein Kosten-Abwägen nötig.
- **Weiche Qualität → per Default manuell:** Pipeline läuft durch, aber das QA-Gate verwirft das
  Ergebnis nach N Runden. Dann wird der schwache Stand + QA-Score angezeigt, der Mensch entscheidet
  „auf 9router neu". Grund: 9router kann **Geld kosten / ein Abo mit ToS-Fragen** nutzen — **kein
  automatisches Geldausgeben ohne Zustimmung.** Opt-in `LAURA_AGENT_AUTO_ESCALATE=1` macht auch die
  weiche Eskalation automatisch.

`orchestrator.py` kapselt beide Achsen: `run(topic, source) → Stufe A (ollama) → bei „zu schlecht"
Stufe B (9router)`.

### Konfiguration — alles über Env (Zero-Config-Default)

Läuft **ohne eine einzige Env-Variable** (Default: lokal, gratis, manuelle Eskalation). Jede Env
überschreibt nur; nichts ist hart im Code verdrahtet:

| Env | Default | Wirkung |
|---|---|---|
| `LAURA_AGENT_PROVIDER` | `ollama` | Stufe-A-Provider: `ollama` \| `9router` \| `openai-compat` |
| `LAURA_AGENT_MODEL` | `qwen2.5` | Modell der Alltags-Agenten (Scout/Director/Transcript/Editor-Reasoning) |
| `LAURA_ORCHESTRATOR_MODEL` | = `LAURA_AGENT_MODEL` | Modell des Magentic-One-Orchestrators (darf stärker sein) |
| `LAURA_AGENT_ORCHESTRATION` | `magentic` | Orchestrierung erzwingen: `magentic` (primär) \| `graph` (nur GraphFlow) |
| `LAURA_AGENT_ESCALATE_PROVIDER` | `9router` | Stufe-B-Provider bei „zu schlecht" |
| `LAURA_AGENT_ESCALATE_MODEL` | `cc/claude-sonnet-4-5` | Stufe-B-Modell (Abo-Reuse; sonst z.B. GLM) |
| `LAURA_AGENT_AUTO_ESCALATE` | `0` | `1` = auch *weiche* Qualität automatisch eskalieren (hart eskaliert immer) |
| `LAURA_AGENT_QA_MAX_ROUNDS` | `2` | QA-Runden bevor „weich zu schlecht" gilt |
| `LAURA_9ROUTER_BASE_URL` | `http://localhost:20128/v1` | 9router-Endpoint (OpenAI-kompatibel) |
| `LAURA_9ROUTER_API_KEY` | — | 9router-Key (aus dem Dashboard) |
| `LAURA_AGENT_BASE_URL` / `LAURA_AGENT_API_KEY` | — | generischer `openai-compat`-Endpoint/Key |

- Der **VLM-Describer** nutzt weiter den bestehenden `LAURA_VLM_MODEL` (qwen3-vl via Ollama) — nicht
  doppelt konfigurieren.
- Alle Werte werden **einmal beim Job-Start** über `providers.resolve_from_env()` gelesen — so ist
  wirklich jeder Schalter per Env erreichbar, ohne Code-Änderung.

### Agenten-Besetzung + bestätigter Graph

Jeder Agent = `AssistantAgent(name, model_client=<provider>, workbench=<Laura-MCP>)`. Die
**Schwerarbeit** machen Lauras MCP-Tools; die Agenten sind dünn.

```
        Scout ──▶ (pro Kandidat)  Describer ┐
      (CLIP+shorts)                          ├─▶ Join ─▶ Director ─▶ Editor ─▶ render
                                 Transcript ─┘         (wählt+ordnet)  (Cut+Übergänge)
                                                            ▲                 │
                                                            └──── QA schwach ─┘  (bedingte Kante)
```

| Agent | Tut | Nutzt (MCP-Tool / Modul) |
|---|---|---|
| **Scout** | findet Kandidaten-Momente zu Thema X | `search_visual_moments`, `extract_shorts` |
| **Describer** | sagt pro Kandidat, was zu sehen ist | VLM (`transition_review`/qwen3-vl) |
| **Transcript-Analyst** | fasst Transcript ±15s um jeden Kandidaten zusammen | ASR words/segments |
| **Director** | wählt + ordnet Segmente zu kohärentem Short | Reasoning über Describer+Transcript |
| **Editor** | baut Cut, richtet Übergänge (Embedding-Ähnlichkeit) aus, rendert | `build_roughcut`, `transition_review`, `render_timeline` |
| **QA-Gate** | prüft Short gegen Thema X; „schwach" → zurück zum Director | `shorts_qa`/`qa_metrics` |

- **Fan-out/Join**: Describer ∥ Transcript-Analyst laufen pro Kandidat parallel, Join sammelt.
  (Im `GraphFlow` explizite Kanten; in Magentic-One delegiert der Orchestrator.)
- **Bedingte Kante**: QA-Gate „schwach" → zurück zum Director (max. N Runden, dann bester Stand).

### Tool-Wiederverwendung — in-Prozess `FunctionTool` (entschieden, Iteration 3)

Die Agenten laufen im **selben Backend-Prozess** wie `db` + die `tool_*`-Funktionen. Statt den
stdio-MCP-Server (`mcp/server.py`) zu starten und über eine Pipe zu round-trippen, wrappen wir
dieselben `tool_*`-Funktionen **in-Prozess** als AutoGen-`FunctionTool`s — mit `db`-Injektion genau
wie der MCP-Server (`short_creator/toolset.py`, spiegelt dessen Wrapper). `build_tool_specs(db)` ist
**pur** (kein autogen, getestet gegen echte In-Memory-DB); `build_function_tools(db)` importiert das
Extra lazy. Die Agenten rufen `search_visual_moments`/`extract_shorts`/`build_roughcut`/
`render_timeline` etc. **direkt** — kein Roundtrip, kein Subprozess. (Der externe MCP-Server bleibt
für externe Clients wie Claude Desktop; er ist von diesem Pfad unabhängig.)

### Job- + API-Integration

- **Job-Handler `short_creator.run`** (`short_creator/handlers.py`, via `register_*_handlers` in
  `main.py.create_app` — additive Registrierung, wie die anderen). Läuft asynchron im Job-Runner
  (langer LLM-Lauf darf den Request nicht blocken).
- **Endpoint** `POST /assets/{asset_id}/auto-short` **oder** `POST /timelines/{id}/auto-short`
  (`api/short_creator.py`, neu) mit `{ topic, target_seconds, source: "asset"|"roughcut"|"finecut" }`
  → enqueued den Job, gibt `job_id`. Status über das bestehende `job`-Polling.
- **Permission**: `timeline:edit` (wie Auto-Pilot).

## Datenfluss

1. **Input**: `asset_id` (analysiert: shots + embeddings + transcript vorhanden) **oder** ein
   bestehender Rough-/Fine-Cut (Szenen als Kandidaten-Pool). Precondition-Check vorab; fehlt die
   Analyse, klare Fehlermeldung (kein stiller Fehlschlag).
2. **Scout** → Kandidatenliste (frame-genaue Ranges, end-exclusive).
3. **Fan-out** Describer ∥ Transcript pro Kandidat → **Join**.
4. **Director** → geordnete Segment-Auswahl (Ziel-Länge).
5. **Editor** → `build_roughcut` aus den Segmenten, Übergänge via `transition_review`, `render_timeline`.
6. **QA-Gate** → ok → fertig; „schwach" → zurück zu (4), max. N Runden.
7. **Output**: eine Timeline/Rough-Cut (bearbeitbar) **+** gerenderter 9:16-Short. Alles über die
   bestehenden Tabellen/Endpoints — kein neuer Projektzustand.

## Fehlerbehandlung

- **Magentic-One versagt** (Exception / Nicht-Terminierung / leeres Ergebnis) → automatischer
  **GraphFlow-Fallback**; beide protokollieren, welcher Pfad lief.
- **Tool-Fehler** (z.B. `render_timeline` schlägt fehl): der Agent meldet, der Job endet `failed`
  mit klarer Ursache; kein Teil-Artefakt als „fertig" markiert.
- **QA-Schleife** ist begrenzt (N Runden) → nie Endlos-Loop; danach bester Stand + Hinweis.
- Fehlt das Extra: `503 feature_unavailable` (+ Installationshinweis).

## Invarianten (nicht verhandelbar)

- **Frame-genau bleibt frame-genau.** Agenten produzieren Ganzzahl-Frame-Ranges, end-exclusive;
  keine Float-Sekunden als Zustand.
- **OTIO bleibt Source of Truth.** Der Agent baut über `build_roughcut`/Timeline-Ops, nicht neben
  dem Interchange.
- **MCP-Tools unverändert.** Der Agent ruft dieselben `tool_*`; keine Signatur-Änderung.
- **Local-first / optionales Extra.** Backend startet ohne AutoGen; Default-Provider lokal.
- **Idempotenz** der Backend-Ops unberührt; der Agent-Lauf selbst ist nicht idempotent (LLM), aber
  seine Outputs sind normale, reproduzierbare Timeline-Zustände.
- **Codex-Subtree unangetastet:** kein Anfassen von `services/ai-runtimes/`, `ai/runtime_*`,
  `api/ai_runtimes.py`. Neues Modul lebt unter **`laura/short_creator/`** (frischer Namespace);
  `main.py`-Registrierung additiv + koordiniert.

## Modulstruktur (neu, kollisionsfrei)

```
services/local-api/src/laura/short_creator/
  __init__.py
  providers.py       # Model-Client-Factory (ollama | 9router | openai-compat)
  toolset.py         # in-Prozess FunctionTool-Bridge zu Lauras tool_* (db-injiziert)
  describe.py        # optionaler VLM-Describe-Backend (Ollama, graceful ohne Modell)
  context.py         # transcript_window (±Frame-Fenster) + describe_moment (Frame-VLM)
  agents.py          # Scout/Describer/Transcript/Director/Editor/QA (AssistantAgent + Tools)
  magentic.py        # MagenticOneGroupChat (primär)
  graph.py           # GraphFlow/DiGraphBuilder (Fallback)
  orchestrator.py    # run(): Magentic-One → Fallback GraphFlow; teilt Agenten
  handlers.py        # Job-Handler short_creator.run (lazy AutoGen-Import)
services/local-api/src/laura/api/short_creator.py   # POST .../auto-short
docs/agentic-short-creator.md                        # Setup + Provider/9router-Doku
```
pyproject: `[project.optional-dependencies] autoshort = ["autogen-agentchat>=0.4", "autogen-ext[openai,ollama]>=0.4"]`.

## Tests

- **providers.py**: Factory gibt pro Env den richtigen Client-Typ; `model_info` gesetzt; kein
  Netzzugriff im Test (Client nur konstruiert, nicht gerufen).
- **agents.py**: jeder Agent wird mit **gemocktem Model-Client** (scripted Antworten) + Fake-MCP-
  Workbench gebaut; Tool-Auswahl korrekt (Scout ruft `search_visual_moments`, Editor `render_timeline`).
- **graph.py**: `DiGraphBuilder` erzeugt die erwartete Struktur (Kanten Scout→{Describer,Transcript}
  →Join→Director→Editor; bedingte QA-Kante); Entry-Point Scout.
- **orchestrator.py**: Magentic-One-Erfolg → GraphFlow **nicht** gerufen; Magentic-One wirft/leer →
  GraphFlow gerufen; Ergebnis identischer Typ.
- **Kein harter Modell-/Netz-Zwang** in Tests (alle LLM-Clients gemockt); Backend-Start-Test ohne
  Extra bleibt grün.
- Python: mypy strikt, ruff; `uv run pytest`.

## Iterationsplan (x−1 = 9 Runden, Validierung vom User)

1. **Spec** (dieses Dokument) — _jetzt, warte auf deine Validierung._
2. `providers.py` + Tests (Provider-Factory ollama/9router/openai-compat).
3. In-Prozess Tool-Bridge (`toolset.py`): `tool_*` → `FunctionTool` (db-injiziert) + Smoke-Test gegen echte DB. ✅
4. `agents.py`: Roster (`agent_specs`, pur) + `build_agents` (lazy), an die 10 Tools verdrahtet. ✅
4b. Spezial-Tools: `describe_moment` (VLM-Frame-Beschreibung, injizierbar/graceful) +
    `transcript_window` (±Frame-Fenster über `get_transcript`) → Describer/Transcript verdrahtet. ✅
5. `magentic.py`: `MagenticOneGroupChat` (Roster + Orchestrator-Client, max_turns-Cap). ✅
6. `graph.py`: `GraphFlow` (Fallback) + Struktur-Tests.
7. `orchestrator.py`: Eskalations-Leiter (Magentic→GraphFlow auf Ollama; A→B auf 9router) + Tests.
8. `handlers.py` + `api/short_creator.py`: Job + Endpoint + optional-extra-Guard.
9. End-to-end (echter lokaler Lauf gegen ein reales Sprechvideo) + Doku + Review.

Jede Runde: klein, getestet, dann **deine Validierung** bevor die nächste startet.

## Offene Punkte (für deine Validierung)

- **Ziel-Länge / Format**: fix 60s 9:16, oder Parameter (`target_seconds`, Ratio)?
- **Input-Fokus v1**: zuerst „aus analysiertem Asset" (Scout sucht) — Rough-/Fine-Cut-Quelle als
  Runde später, oder beide gleich?
- **Weiche Eskalation**: bei zu schwacher Qualität per Default **manuell** (Mensch flippt auf
  9router) oder gleich **automatisch** (`LAURA_AGENT_AUTO_ESCALATE=1`)? Harte Fehlschläge eskalieren
  immer auto.
- **9router-Modell** für Stufe B: `cc/claude-sonnet-4-5` (Abo-Reuse, ~0 Grenzkosten) als Default,
  sonst GLM (billig) — was ist dir lieber?
