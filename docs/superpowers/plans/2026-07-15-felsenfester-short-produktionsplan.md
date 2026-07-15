# Felsenfester Short-Produktionsplan (Iteration 1/3)

> Ziel: Aus jedem Quellvideo **zuverlässig** gute Shorts — mit Evidenz statt Hoffnung.
> Basis: die 6 Live-Erkenntnisse aus Session `1b578c7e` (38,5s-Fassung 9/10, 180s-Experiment)
> und der TikTok/Instagram-Leitfaden des Users. Status: Entwurf, wird in 3 Iterationen gehärtet.

## Die 6 Erkenntnisse als Plan-Fundament

| # | Erkenntnis | Konsequenz im Plan |
|---|-----------|--------------------|
| 1 | Team de-eskaliert unmögliche Ziele selbst | Decke **vor** der Storyline berechnen, Ziel materialbewusst setzen (P1) |
| 2 | Checkpoints tragen über Job-Tode; 1h-Runtime-Limit killt lange Aufträge | Lease/Runtime konfigurierbar bzw. Kette in <1h-Pakete schneiden (P3) |
| 3 | Re-Reviews reproduzieren alte Fenster | Multi-Window statt „nochmal schauen" (läuft als Task) |
| 4 | VLM hat keinen Personen-/Privacy-Sensor (Webcam mit hook 7!) | Privacy-Feld im Review + Storyline-Gate; bis dahin Sichtungspflicht (P1) |
| 5 | Stimm-Formel `Wörter × 0,55s + Zeilen × 0,38s` trifft exakt | Formel + Rechenprobe fest in den Task-Contract, nicht per Message (P1) |
| 6 | Mehr Länge = dünnere Qualität | **Serie (4-6 × 15-30s) statt Langform** (P2) |

Dazu aus dem Leitfaden: 3-Sekunden-Hook, Watch-Time zuerst, Konsistenz schlägt Qualität,
Bild belegt den gesprochenen Satz, Mensch früh im Bild.

## Phase 0 — Fundament zusammenführen (JETZT, Reihenfolge ist bindend)

Drei Feature-Stränge berühren dieselben Dateien (`production_tools.py`, `board_models.py`,
`board.py`). Merge-Reihenfolge, um Konflikte deterministisch zu halten:

1. **Cutlist-Audio-Sync** — ✅ **GEMERGED** (`13da9d3`, in `feat/generate-ui`). Segment-Dauern
   folgen den Kapitel-Sprechfenstern; Bild↔Stimme-Drift + abgeschnittenes Schluss-Kapitel behoben.
2. **Multi-Window-Reviews** — ✅ **GEMERGED** (`f8d4a46..d84d2f5`, via `origin`, lokal rebased
   auf `8d18f27`). Reale Schema-Form (verifiziert in `board_models.py`):
   `SceneReview.windows: list[BestWindow]` (1-4, nicht überlappend, `windows[0] == best_window`;
   alte Review-JSONs ohne `windows` werden auf `[best_window]` zurückgefüllt);
   Storyline-Eintrag = `(scene_number, window_index)`, plain int = Fenster 0;
   `_no_duplicate_scene_windows` erlaubt **dieselbe Szene mit verschiedenen Fenstern**, verbietet
   das exakt gleiche `(scene, window)` doppelt; `build_cutlist` schneidet das referenzierte Fenster.
   → Das ist die Materialdecken-Aufhebung UND die Antwort auf „keine Dopplungen": Wiederholung nur
   mit anderem Ausschnitt, im Code erzwungen.
3. **Kontaktbogen-Checkpoint** (Task läuft noch): baut auf 1+2 auf. Review → Merge.
4. Nach JEDEM Merge: Backend-Neustart (laufender Prozess hält alten Code) + Smoke-Lauf.
   ✅ erledigt für 1+2: mypy 462 clean, 71 Produktions-/Multi-Window-/Caption-Tests grün,
   Backend neu auf `8d18f27`, Session-Endpoint 200.

**Gate P0:** `uv run pytest` + `uv run mypy` (voller Scope) grün nach jedem Merge;
ein Team-Render auf `workspace-livetest` erzeugt Kontaktbogen + synchrones Video.
→ Offen bis Kontaktbogen (Schritt 3) gemerged ist.

## Phase 1 — Qualitätsregeln vom Message-Text in den Code

Was sich per /message bewährt hat, wird Standard im Task-Contract
(`build_production_task`) und in den Validatoren — damit es OHNE Regie-Message gilt:

- **Stimm-Formel + Rechenprobe** als feste Autoren-Regel im Contract (Erkenntnis 5).
- **Decke-zuerst:** Vor `save_storyline` berechnet das Team `Σ min(budget, fenster, szene)`
  und setzt das Längenziel darauf; Ziel > Decke wird im red_thread dokumentiert (Erkenntnis 1).
- **Privacy-Gate (noch offen):** `SceneReview` bekommt `person_visible: bool` + `privacy_risk: bool`
  (Review-Prompt erweitert); der Storyline-Validator lehnt `privacy_risk`-Szenen mit
  agent-korrigierbarem Fehler ab (Erkenntnis 4). **Andockpunkt jetzt ideal:** Das Multi-Window-
  Review-Schema (`windows`-Liste, erweiterter Prompt in `production_tools.py`) ist frisch gemerged
  — die Privacy-Felder gehören in denselben Review-Vertrag. Bis dahin gilt: **keine ungesichtete
  Szene in eine Storyline** (Kontaktbogen/Controller-Sichtung ist Pflicht-Gate).
- **Caption-Regeln** (≤8 Wörter/Zeile, ≤14 Zeichen/Wort) stehen im Contract; der
  Breiten-Umbruch ist bereits Code (`c6727d7`).
- **Feinregel-Band** als Standard-Charter: Ziel `voice_s ∈ [video_s − 1, video_s]`,
  max 3 Zyklen, nur EINE Variable pro Zyklus (Pendel-Verbot).

**Gate P1:** Ein frischer Produktionslauf OHNE Regie-Message erfüllt: voice_fits, Captions
im Rahmen, keine Verbots-Szene, Kontaktbogen vorhanden.

## Phase 2 — Serien-Modus (Design-Runde mit User, dann SDD)

Das strategische Format (Erkenntnis 6 + Leitfaden): **eine Serie statt einer Langform.**

- Ein Auftrag „Serie aus N Teilen" → N Teil-Produktionen unter gemeinsamem rotem Faden;
  Teil-Anzahl = `Decke / ~25s`, jedes Teil 15-30s.
- Jedes Teil: eigener 3s-Hook, 4-8 starke Fenster, CTA mit Cliffhanger („Teil n+1: …").
- Fenster-Pool wird über die Teile verteilt — **kein Fenster doppelt in der Serie**
  (Multi-Window liefert den Pool).
- Kontaktbogen-Gate pro Teil vor dem Render.
- Offene Design-Fragen (für die Brainstorming-Runde): eine Session mit N Boards vs.
  N Sessions mit Serien-Klammer; Nummerierungs-/Branding-Overlay; Posting-Reihenfolge.

**Gate P2:** Design-Doc vom User abgesegnet, dann Plan → SDD.

## Phase 3 — Betriebsablauf pro Produktion (der felsenfeste Loop)

1. Import → Auto-Analyse → Reviews (Multi-Window).
2. Decke berechnen → Zielformat festlegen (Standard: Serie; Einzel-Short nur bei Decke < 45s).
3. Storyline (Privacy-Gate aktiv) → Skript (Formel) → Voice → Cutlist (Audio-Sync)
   → **Kontaktbogen → Freigabe** (User im ChatPanel oder Controller-Sichtung) → Render → QA.
4. Vibe-Iterationen per /message; jede Version revert-bar.
- **Runtime-Limit (Erkenntnis 2):** kurzfristig gilt die bewiesene Nudge-Praxis;
  mittelfristig `production.run`-Lease konfigurierbar machen oder die Kette als
  Folge-Jobs (< 1h je Paket) schneiden. Entscheidung in Iteration 2 dieses Plans.

## Anti-Regeln (Verstöße = Plan-Bruch)

- Kein blindes Re-Review (Erkenntnis 3).
- Keine ungesichtete Szene in eine Storyline, solange das Privacy-Gate fehlt.
- Kein Wortbudget ohne Formel-Rechenprobe.
- Eine Variable pro Feinregel-Zyklus.
- Modell-Rezept dokumentiert nutzen (gpt-5-mini via openai-compat); free-tier nur mit
  Rotations-Erwartungsmanagement.

## Später (nach P2)

- **Mensch-Faktor:** präsentabler Selfie-Clip als neues Asset + Multi-Asset-Produktion
  (Talking-Head-Hook vor Screen-Material).
- QA-Vision dauerhaft auf verlässlichem VLM (Fehldeutungen wie „Karaoke = Tippfehler" abstellen).

## Iterations-Log

- **Iteration 1 (2026-07-15):** Erstfassung aus den 6 Erkenntnissen + Leitfaden-Kriterien.
- **Iteration 2 (2026-07-15):** Cutlist-Sync (`13da9d3`) und Multi-Window (`f8d4a46..d84d2f5`)
  sind gemerged und lokal integriert (rebase auf `8d18f27`), Gate grün, Backend neu gestartet.
  Reale Multi-Window-Schema-Form eingearbeitet; „keine Dopplungen" ist jetzt im Code erzwungen
  (`_no_duplicate_scene_windows`). Privacy-Gate-Andockpunkt präzisiert (gehört in den
  Multi-Window-Review-Vertrag). **Offen für Iteration 3:** Kontaktbogen-Merge (Task läuft),
  dann Gate P0 vollständig; Runtime-Limit-Entscheidung (Erkenntnis 2); Serien-Design-Runde.
- Iteration 3 (wartet auf Kontaktbogen-Task): Endhärtung — Gate P0 komplett verifizieren
  (Kontaktbogen + synchrones Video im Smoke-Lauf), Runtime-Limit entscheiden, Serien-Design
  als konkreten nächsten Auftrag ausformulieren.
