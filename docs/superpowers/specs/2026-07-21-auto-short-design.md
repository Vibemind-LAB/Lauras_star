# Auto-Short: Topic rein, bestes Material raus — mit begründeter Agenten-Auswahl

**Datum:** 2026-07-21 · **Status:** vom User freigegeben · **Scope:** Punkt E (#114),
Phase 1. Phase 2 („Auto-Overview" über mehrere Videos) ist als Folge-Zyklus dokumentiert,
nicht Teil dieser Spec.

## Vision & Entscheidungen (User)

1. **Topic über ALLE Videos:** Der User gibt nur ein Thema an — kein Asset-Picken. Laura
   findet das Material selbst über die Transkripte aller Projekt-Assets.
2. **Phase 1 = ein bestes Video:** Der Short schneidet aus EINEM Asset (die bewährte
   Pro-Asset-Produktion bleibt unangetastet). **Phase 2 = Auto-Overview**, das Material aus
   mehreren Videos mischt — über die bestehende SEQUENZ-Maschinerie (Zusammenfügen kann
   heute schon multi-asset), NICHT über einen Multi-Asset-Umbau des Production Boards. Die
   Discovery-Schicht aus Phase 1 wird dort 1:1 wiederverwendet.
3. **Backend-only:** Endpoint + Discovery jetzt; der UI-Einstieg (ChatPanel-Auto-Modus o.ä.)
   ist ein späterer Zyklus.
4. **Ansatz B — agentische Auswahl:** Ein Scout-Agent wählt Asset + Szenen und BEGRÜNDET die
   Wahl, statt dass blanke Cosine-Scores entscheiden (A „rein deterministisch" verworfen;
   C „Discovery als Job-Kind" verworfen: Async-Plumbing für eine Sekunden-Operation). Das
   Henne-Ei-Problem (Session/Board brauchen das Asset VOR dem Team-Lauf) löst die tragfähige
   B-Form: der **Scout läuft VOR der Session-Erstellung**, nicht im Produktions-Team.

## Design

### 1. Discovery-Tool: `search_material`

Deterministischer Kern (auch Phase-2-Fundament), als Funktion + Agenten-Tool:

- Sucht über die Transkript-Segmente ALLER Assets des Projekts: **semantisch** (der
  bestehende `laura/semantic.py`-Index, Qdrant + fastembed), wenn `semantic_available()`
  und der Index Treffer liefert; sonst **Keyword-Fallback** über die bestehende
  Transkript-Textsuche (repos). Die Herkunft (semantic/keyword) steht im Ergebnis.
- Mappt Treffer-Segmente über ihre Frames auf die **Szenen des Asset-Rough-Cuts** (den der
  Autopilot nach jeder Analyse garantiert baut; Assets ohne Rough-Cut/Szenen fallen aus dem
  Ranking, mit Grund).
- Ergebnis: pro Asset `{asset_id, display_name, score, scene_hits: [{scene_number,
  snippet, score}]}`, absteigend gerankt; leeres Ergebnis ist ein strukturiertes leeres
  Ranking, nie ein Fehler.

### 2. Scout-Agent

- **Ein einzelner** leichter Agent (Tool-Call-Loop, Sekunden — KEIN Magentic-Team), Client
  über dieselbe Provider-Auflösung wie die Produktion (`resolve_from_env`; Preflight und
  `config_warnings` greifen unverändert — ein lokaler 7B-Scout warnt genauso).
- Tools: `search_material(topic)`, `list_project_assets()`, `get_scene_context(asset_id,
  scene_number)` (bestehende Bausteine, als in-process FunctionTools wie im v1-Muster).
- Auftrag: bestes Asset + relevanteste Szenen für das Topic wählen und die Wahl in 1-3
  Sätzen begründen. Strukturierte Antwort `{asset_id, scene_numbers, rationale}`.
- **Validierung statt Vertrauen:** halluzinierte Asset-IDs/Szenen-Nummern → EIN Retry mit
  Fehlerhinweis; danach **Fallback auf den Top-Score der deterministischen Suche** (Szenen =
  dessen scene_hits, rationale = "automatic fallback: top search score"). Der Endpoint
  stirbt nie an Agenten-Launen.
- Timeout-begrenzt; Scheitern (Timeout, leere Antwort, kein Client) → derselbe Fallback.

### 3. Endpoint: `POST /projects/{project_id}/auto-short`

Body `{topic: str, target_seconds?: float, format?: str, language?: str}` (Defaults wie die
bestehende Session-Erstellung). Ablauf synchron im Request:

1. Projekt unbekannt → 404. `_require_autoshort` + `_require_usable_agent_config` wie bei
   der Session-Erstellung (503-Fälle identisch).
2. `search_material` vorab: **gar kein Material** (kein Asset mit Transkript-Treffern und
   Szenen) → **422** mit dem (leeren bzw. schwachen) Ranking als Begründung — es wird keine
   Session angelegt.
3. Scout wählt (oder Fallback greift).
4. Session auf dem gewählten Asset — exakt der bestehende Erstellungspfad (Session-Row,
   `production.run`-Enqueue, max_attempts=1). Der **Task-Text** wird komponiert aus: Topic,
   den gewählten Szenen als Hinweis („focus on scenes …, transcript hits: …") und der
   **Scout-Rationale** — das Team erfährt, warum dieses Material gewählt wurde.
5. Response 202: `{session_id, job_id, asset_id, scene_numbers, rationale, ranking,
   warnings}` (`warnings` = `config_warnings`, wie die anderen Enqueue-Responses).

Ab da ist es eine normale Session: Status, Chips, Revert, Provenienz, QA — alles Bestehende.

### 4. Fehler & Robustheit

- Semantic-Extra fehlt → Keyword-Fallback, kein Fehler (Herkunft im Ranking sichtbar).
- Scout-Ausfall jeder Art → deterministischer Fallback (§2), im Response als solcher
  gekennzeichnet.
- Kein verwertbares Material → 422 VOR jeder Session-Erstellung (keine Leichen-Sessions).
- Der Scout-LLM-Call läuft synchron im Request mit Timeout; die Produktions-Semantik
  (ein Job, nicht retried) bleibt unverändert.

### 5. Tests

- `search_material`: Keyword-Pfad mit echtem DB-Seed (Segmente + Rough-Cut-Szenen, Treffer
  gemappt + gerankt); Asset ohne Szenen fällt raus; leeres Ranking bei Null-Treffern;
  semantischer Pfad mit in-memory-Index, wenn das Extra installiert ist (sonst skip wie die
  bestehenden semantic-Tests).
- Scout: mit injiziertem Fake-Client/Executor — gültige Antwort wird übernommen; ungültige
  Asset-ID → Retry → Fallback; Timeout → Fallback (kein echter LLM in Tests).
- Endpoint: Happy-Path (Session entsteht auf dem Scout-Asset, Task-Text trägt Topic +
  Szenen + Rationale, Response-Shape), Fallback-Pfad, 422 ohne Material, 404 Projekt,
  503-Preflight, `warnings` im Response.

## Abgrenzung zum bestehenden v1-Endpoint

Es existiert bereits `POST /assets/{asset_id}/auto-short` (+ `/stream`): der **v1-Pfad**, der
für ein VOM USER GEWÄHLTES Asset den v1-NL-Agenten (`short_creator.run`) enqueued. Er bleibt
unangetastet. Das hier ist etwas anderes: `POST /projects/{project_id}/auto-short` wählt das
Asset SELBST (Topic über alle Videos, Scout) und startet die v2-Produktion — das
Routen-Prefix (`projects/` vs. `assets/`) trägt den Unterschied.

## Nicht in diesem Scope

- UI-Einstieg (Entscheidung 3) und Phase 2 Auto-Overview (Entscheidung 2).
- Multi-Asset-Cutlists/Board-Umbau (explizit verworfen — Phase 2 nutzt Sequenzen).
- Kein neuer Job-Kind; keine Änderung an Produktions-Team, Board oder Provenienz.
- Kein Umbau der semantischen Indizierung (der Index wird genutzt, wie er ist).
