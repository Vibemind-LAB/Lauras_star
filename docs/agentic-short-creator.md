# Agentic Short-Creator (NL-Agent)

„Mach mir einen 60s-Short über X" → automatischer 9:16-Short aus einem analysierten Video, gebaut
von einem **Multi-Agent-System (AutoGen 0.4)**, das Lauras **bestehende** Fähigkeiten orchestriert
(CLIP-Suche, VLM, Shorts-Scoring, Transcript, Render). Design:
[`docs/superpowers/specs/2026-07-01-nl-agent-short-creator-design.md`](superpowers/specs/2026-07-01-nl-agent-short-creator-design.md).

## Installation (optionales Extra)

```bash
uv sync --extra autoshort
```

Das Backend startet **auch ohne** das Extra — der `auto-short`-Endpoint liefert dann `503`
(feature unavailable). Nichts von AutoGen wird beim Modul-Laden importiert.

## Voraussetzungen

- **Asset analysiert** (Shots + Transcript; für die visuellen Tools zusätzlich `shorts.embed_frames`).
- **Ollama lokal** für die Reasoning-Agenten und den VLM-Describe:
  - Agenten-Modell: z.B. `qwen2.5` (Default `LAURA_AGENT_MODEL`).
  - VLM (Frame-Beschreibung): `qwen3-vl:8b` via `LAURA_VLM_MODEL` (sonst degradiert der Describer
    graceful zu „keine Beschreibung").

## Nutzung

```
POST /assets/{asset_id}/auto-short
{ "topic": "…", "target_seconds": 60 }        →  { "job_id": "…" }
```

Fortschritt über `GET /jobs/{job_id}` pollen. Ergebnis ist eine bearbeitbare Timeline/Rough-Cut +
ein gerenderter 9:16-Short — alles über die bestehenden Tabellen/Endpoints, kein neuer Projektzustand.

## Orchestrierung — die Eskalations-Leiter

- **Stufe A (lokal, gratis, Provider `ollama`):** Magentic-One → bei hartem Fehlschlag automatisch
  GraphFlow (deterministischer Graph: `scout → {describer ∥ transcript} → director → editor → qa`,
  QA-„weak" schleift zurück zum Director).
- **Stufe B (Provider `9router`):** greift, wenn Stufe A **„zu schlecht"** ist — *hart* (beide
  Teams scheitern) → automatisch; *weich* (QA verwirft nach N Runden) → per Default **manuell**
  (du siehst den Stand + QA-Score und flippst selbst), oder automatisch mit
  `LAURA_AGENT_AUTO_ESCALATE=1`.

## Konfiguration (alles über Env, Zero-Config-Default lokal)

| Env | Default | Wirkung |
|---|---|---|
| `LAURA_AGENT_PROVIDER` | `ollama` | Stufe-A-Provider: `ollama` \| `9router` \| `openai-compat` |
| `LAURA_AGENT_MODEL` | `qwen2.5` | Modell der Alltags-Agenten |
| `LAURA_ORCHESTRATOR_MODEL` | = `LAURA_AGENT_MODEL` | Modell des Magentic-One-Orchestrators (darf stärker sein) |
| `LAURA_AGENT_ORCHESTRATION` | `magentic` | `magentic` (primär) \| `graph` (nur GraphFlow erzwingen) |
| `LAURA_AGENT_ESCALATE_PROVIDER` | `9router` | Stufe-B-Provider |
| `LAURA_AGENT_ESCALATE_MODEL` | `cc/claude-sonnet-4-5` | Stufe-B-Modell |
| `LAURA_AGENT_AUTO_ESCALATE` | `0` | `1` = auch weiche Qualität automatisch eskalieren |
| `LAURA_AGENT_QA_MAX_ROUNDS` | `2` | QA-Runden bevor „weich zu schlecht" gilt |
| `LAURA_9ROUTER_BASE_URL` | `http://localhost:20128/v1` | 9router-Endpoint (OpenAI-kompatibel) |
| `LAURA_9ROUTER_API_KEY` | — | 9router-Key (aus dem Dashboard) |
| `LAURA_AGENT_BASE_URL` / `LAURA_AGENT_API_KEY` | — | generischer `openai-compat`-Endpoint |
| `LAURA_VLM_MODEL` | — | VLM fürs Frame-Describe (Ollama, z.B. `qwen3-vl:8b`) |

## 9router (optional, für stärkere/günstigere Orchestrator-Modelle)

[9router](https://github.com/decolua/9router) ist ein selbst-gehostetes, OpenAI-kompatibles Gateway
(`localhost:20128/v1`) zu 40+ Providern. Setup:

```bash
npm install -g 9router      # läuft auf Port 20128
# Key im Dashboard (localhost:20128/dashboard) erzeugen, dann:
export LAURA_AGENT_ESCALATE_PROVIDER=9router
export LAURA_9ROUTER_API_KEY=<key>
export LAURA_AGENT_ESCALATE_MODEL=cc/claude-sonnet-4-5   # oder z.B. GLM (billig)
```

Damit läuft die Stufe-B-Eskalation über 9router; die Stufe-A-Agenten bleiben lokal/gratis auf Ollama.
**Hinweis:** Abo-Reuse (Claude Code / Copilot) via Proxy kann gegen die ToS des Anbieters verstoßen —
vor produktiver Nutzung prüfen.

## Manuell zu verifizieren (echter Lauf)

CI/Sandbox haben **kein** Ollama/AutoGen/Modell — daher ist der echte End-to-End-Lauf manuell:

1. `uv sync --extra autoshort` + Ollama mit `qwen2.5` und `qwen3-vl:8b` (`ollama pull …`).
2. Ein **echtes Sprechvideo** importieren + analysieren (Shots + Transcript + `shorts.embed_frames`).
3. `POST /assets/{id}/auto-short { "topic": "…" }` → `/jobs/{id}` pollen bis `succeeded`.
4. Prüfen: das Ergebnis-Result nennt `stage` (A/B), `team` (magentic/graph), `weak`, `escalated`;
   die erzeugte Timeline öffnen + den 9:16-Short ansehen.

Alle Nicht-Modell-Pfade (Provider-Factory, Tool-Bridge, Roster, Team-Zusammenbau, Eskalations-Logik,
Endpoint/Guard) sind mit gemockten Clients getestet (`tests/test_short_creator_*.py`).
