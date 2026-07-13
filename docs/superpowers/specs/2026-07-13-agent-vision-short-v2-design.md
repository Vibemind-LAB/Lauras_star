# Agent-Vision-Short v2 — „Vibe-Editing für Videos" (Design)

Datum: 2026-07-13 · Branch: `feat/generate-ui` · Status: Entwurf zur Review

## 1. Ziel

Ein voll-agentisches, **interaktives** Produktionssystem für Shorts: Agenten, die Szenen
wirklich *ansehen*, danach schneiden, pro Szene texten und eine Storyline bauen — und mit dem
der User danach **weiterarbeitet** wie mit einem Vibe-Coding-Tool: nachjustieren („Kapitel 2
andere Szene"), Schritte zurücknehmen (Revert auf Artefakt-Versionen), neu rendern — ohne von
vorn zu beginnen.

Auslöser (User-Feedback zum v1-Short, 2026-07-13):

- Ergebnis wirkte **unsortiert**, für Erstseher nicht verständlich → Storyline fehlt.
- Transkript-Qualität mittel → Texte müssen am *Gesehenen* geerdet sein, energetisch.
- 16:9-Screen-Recording füllt 9:16 nicht („nutzt den Platz nicht") → **Zoom** statt nur
  Blur-Letterbox.
- Explizit: **agent-only** — Urteile treffen Agenten, nicht deterministische Skripte; plus ein
  Coding-Agent, der ausführen kann „wie Claude es tat".
- /goal: App-Fähigkeiten als Agenten-Tools; Team produziert auf Zuruf; Arbeit ist interaktiv
  (revert + adjust) — „vibe coding tool for videos".

### Nicht-Ziele (v1 dieser Stufe)

- Multi-Format-Fan-out (x/linkedin) innerhalb einer Session — existiert separat, kommt später.
- Musik, generative B-Roll, UI-Timeline-Scrubbing im ChatPanel.
- Whisper-large-v3-Re-Analyse (separat, braucht User-Go).
- Mehrere Quell-Assets pro Session.

## 2. Entscheidungen aus dem Brainstorm (User-fixiert)

| Frage | Entscheidung |
|---|---|
| Zoom-Verhalten | **Hybrid**: Szene startet voll (Blur-Rahmen), zoomt dann auf die ROI, über die das Voiceover gerade spricht |
| Story-Struktur | **Fester Viral-Arc**: Hook (2–3s) → Problem/Versprechen → 3–4 Kapitel Feature-Tour → Payoff + CTA |
| Modelle | **Alles free** (OpenRouter free / 9router) → Resilienz ist Designprinzip |
| Architektur | **C: Voll-autonom** — Magentic-One-Orchestrator dirigiert Spezialisten |
| Team-Refinement | 3 Iterationen (User-/loop): Cutter-Rolle aufgeteilt (Urteil→Vision-Reviewer, Ausführung→Coding-Agent), Shared Context via Production Board, Coding-Agent fest im Team |

## 3. Architektur-Überblick

```
User ↔ ChatPanel ↔ POST /assets/{id}/production  (Session anlegen, Job)
                    POST /production/{sid}/message (adjust / revert / status)
                              │
                    Magentic-One-Orchestrator (Task = Viral-Arc-Vertrag)
                              │  dirigiert, relayt aber KEINE Inhalte
        ┌───────────┬─────────┼──────────┬─────────────┐
  Vision-Reviewer  Story-   Szenen-   Coding-Agent   QA-Reviewer
  (sieht Frames)   Architekt  Autor   (führt aus)    (sieht Ergebnis)
        └───────────┴─────────┴──────────┴─────────────┘
                              │  Inhalte fließen ausschließlich über das
                    Production Board  (versioniert, persistiert, validiert)
                              │
              Deterministische Werkzeuge (bestehende App-APIs):
              Frame-Grab · frame-genaue Trims · zoom_hybrid-Render ·
              ElevenLabs+Timings · ffprobe-Checks · Export/Jobs
```

Prinzip: **Agenten urteilen, Tools führen aus.** Jede inhaltliche Entscheidung (was man sieht,
was geschnitten wird, was gesagt wird, in welcher Reihenfolge) trifft ein Agent; alles
Mechanische (Frames, Render, Längen) bleibt deterministisch und testbar.

## 4. Team (6 Rollen — jede ein eigener Urteilstyp)

| Agent | Urteilstyp | liest (Board) | schreibt (Board) | Tools |
|---|---|---|---|---|
| Orchestrator (Magentic-One) | Koordination | Board-Status | Task-Ledger | `board_status`, Spezialisten |
| Vision-Reviewer | Wahrnehmung | — | `scene_reviews/*` | `grab_scene_frames`, `save_scene_review` |
| Story-Architekt | Narrativ | Reviews, Transkript-Überblick | `storyline.json` | `get_board`, `save_storyline` |
| Szenen-Autor | Sprache (pro Kapitel) | Reviews, Storyline, Szenen-Transkripte | `script.json` | `get_board`, `save_script_chapter` |
| Coding-Agent | Ausführung | Storyline, Skript, Reviews | `cutlist.json`, `render_report.json` | `trim_segment`, `synthesize_voiceover`, `render_production`, `probe_export`, `get_board` |
| QA-Reviewer | Verifikation | alles + gerenderte Frames | `qa_report.json` | `grab_export_frames`, `save_qa_report` |

Rollen-Verträge (Prompt-Kern, je Agent):

- **Vision-Reviewer**: bekommt pro Szene 2–3 Frames (Anfang/Mitte/Ende) über das VLM-Tool.
  Liefert pro Szene: Beschreibung (was sichtbar ist, was passiert), `hook_score` 0–10,
  `best_window` (Offset+Dauer des stärksten Moments), `roi` (normierte Bounding-Box der
  Kern-Region fürs Zoomen), Lesbarkeits-Notizen. Ein Review = ein Call = ein Board-Write
  (Checkpoint). Szenen sind unabhängig → Reviews einzeln retry-bar.
- **Story-Architekt**: füllt den festen Arc mit konkreten Szenen (nur solche mit Review),
  definiert pro Kapitel die Botschaft und den roten Faden. Erstseher-Regel im Prompt: jedes
  Kapitel muss ohne Vorwissen verständlich sein und auf dem vorigen aufbauen.
- **Szenen-Autor**: schreibt pro Kapitel (2–3 Szenen) 1–2 Sätze pro Szene in der Sprache des
  Videos (Deutsch), energetisch, **bild-geerdet**: der Text muss zu dem passen, was laut Review
  sichtbar ist. Kein Marketing-Nebel; konkrete Nutzen.
- **Coding-Agent**: baut aus Storyline+Skript+Reviews die Cutlist (frame-genau via Tool),
  löst die Synthese aus, ruft den Render, prüft Zahlen (ffprobe). **Charta**: mechanische
  Abweichungen darf er autonom fixen (z. B. Stimme 61,9s > Video 60s → Segmente verlängern,
  nie Stimme kappen); **Inhalts**-Abweichungen meldet er dem Orchestrator, statt sie
  umzudeuten.
- **QA-Reviewer**: sieht gerenderte Frames (Hook, Kapitelanfänge, CTA) via VLM + den
  Render-Report. Prüft: Story-Fluss für Erstseher, Caption-Sync, Zoom-Lesbarkeit, volle
  Stimme enthalten. Verdikt `ship` oder `revise` mit konkreten Findings; Orchestrator fährt
  **maximal eine** Revisionsrunde (v1).

## 5. Production Board

Ablage: `<workspace>/agent-runs/<session_id>/board/`. Jedes Artefakt ist JSON mit
`version`-Feld; alte Versionen wandern append-only nach `versions/<name>.v<k>.json`.
Alle Writes sind **pydantic-validiert** — kaputter Agent-Output wird mit Fehlerhinweis
abgelehnt (Agent bekommt den Validierungsfehler als Tool-Result und korrigiert), nie still
weitergereicht.

```
board/
  meta.json              {session_id, asset_id, created_utc, task, format, target_seconds, status}
  scene_reviews/
    scene_<n>.json       {scene_number, src_start_frame, src_end_frame_exclusive,
                          description, whats_happening, hook_score, best_window{offset_s,duration_s},
                          roi{x,y,w,h} (normiert 0–1), legibility_notes, degraded(bool),
                          model, version, created_utc}
  storyline.json         {version, red_thread, arc:[{chapter, role(hook|problem|feature|payoff_cta),
                          message, scene_numbers[], target_seconds}]}
  script.json            {version, language, lines:[{chapter, scene_number, text}]}
  cutlist.json           {version, segments:[{order, scene_number, start_frame,
                          end_frame_exclusive, roi, zoom_start_s}]}
  render_report.json     {version, export_id, video_s, voice_s, width, height,
                          checks:[{name, ok, note}]}
  qa_report.json         {version, verdict(ship|revise), findings:[{severity, where, note}]}
  versions/
```

**Abhängigkeitsgraph** (Invalidierung läuft immer downstream):

```
scene_reviews → storyline → script → voice(cache) → cutlist → render → qa
```

- **Checkpoints/Resume**: jedes Artefakt wird sofort nach Entstehung geschrieben. Ein neuer
  Run auf derselben Session beginnt beim ersten fehlenden/invalidierten Artefakt (Szenen-
  Reviews einzeln!). Free-Tier-Tod (leere Antworten, Tagesquote) kostet nur den offenen Schritt.
- **Frames/Samples-Invarianten**: alle Schnittdaten in Ganzzahl-Frames, end-exclusive; `roi`
  normiert; Sekundenwerte nur in Reports/Anzeige (Projektion).
- Voice-Cache: Synthese keyed auf Hash von `script.json`-Text — Adjust ohne Textänderung
  synthetisiert nicht neu.

## 6. Interaktivität — Sessions, Adjust, Revert

- **Session = Job-basierter Lauf mit Board**; `session_id` bleibt über App-Reloads bestehen.
  Endpunkte:
  - `POST /assets/{id}/production` → legt Session an, startet Produktions-Job, liefert
    `session_id` + `job_id`.
  - `POST /production/{session_id}/message` `{text}` → Folge-Auftrag als Job (adjust oder
    revert; der Orchestrator interpretiert den Freitext).
  - `GET /production/{session_id}` → Board-Zusammenfassung inkl. Artefakt-Versionen.
- **Adjust**: Der Orchestrator mappt die Nachricht auf das **höchste betroffene Artefakt**
  („Hook kürzer" → script/storyline; „Szene 3 ohne Zoom" → cutlist; „Kapitel 2 andere Szene"
  → storyline). Betroffenes Artefakt wird neu erstellt (neue Version), Downstream invalidiert
  und neu gebaut; alles Upstream (insb. Szenen-Reviews) bleibt Cache.
- **Revert**: „zurück zu Storyline v1" → `storyline.json` ← `versions/storyline.v1.json`,
  Downstream invalidiert. Jede Board-Änderung erzeugt Versionen, nichts wird gelöscht.
- **Events**: Session-Jobs schreiben dieselben NDJSON-Run-Logs (`agent-runs/<utc>_<asset8>.ndjson`)
  und streamen Board-Updates als `artifact`-Events → ChatPanel zeigt Chips („storyline v2",
  „export c4a6… ready") und bleibt nach Reload attach-bar (Job-Polling statt Stream-Leiche).

## 7. Renderer — Fit-Modus `zoom_hybrid`

Pro Segment (aus `cutlist.json`): `{roi, zoom_start_s}`.

- Phase 1 (voll): Quellbild komplett sichtbar, Blur-Fill-Rahmen (bestehender
  `reel_blur_fill`-Pfad).
- Übergang: ab `zoom_start_s` (relativ zum Segmentstart) geeaster Crop-Flug (~0,6s,
  smoothstep) in das ROI-Fenster.
- Phase 2 (Zoom): ROI-Fenster füllt 1080×1920 exakt.

ROI→Fenster-Mathematik (deterministisch, rein, getestet):

1. Bounding-Box (normiert) → Pixel im Quellformat (1920×1080).
2. Fenster auf **exakt 9:16** erweitern (zentriert um die Box), dabei:
   - minimale Fensterhöhe = 55 % der Quellhöhe (gegen Pixelbrei; ~1,8× Zoom max bei 1080p),
   - vollständig in den Quellrahmen geklemmt.
3. Upscale Lanczos auf 1080×1920.

Invarianten: Ganzzahl-Frames, end-exclusive; Zoom endet an der Segmentgrenze (kein Überhang);
`zoom_start_s` stammt aus den Voiceover-Wort-Timings (Start des ersten Wortes, das inhaltlich
zur Region gehört — v1-Heuristik: Start der Szenen-Textzeile + fester Lead 0,4s); fehlende oder
ungültige ROI → Segment läuft komplett in Phase 1 (Blur voll), **nie** Crash oder Center-Crop.
Captions (Karaoke) liegen über beiden Phasen; Safe-Area unten bleibt frei.

## 8. Stimme & Captions (unverändert, jetzt Board-gespeist)

`script.json` → Volltext (Kapitel-Reihenfolge) → ElevenLabs `/with-timestamps` → MP3 +
`<mp3>.timings.json` (Schema `{words:[{text,start_s,end_s}]}`) → Karaoke-Captions wortgenau;
`-shortest`-Regel bleibt: **Videolänge ≥ Stimmlänge** ist ein Coding-Agent-Check
(`render_report.checks`), sonst verlängert er Segmente vor dem Render.

## 9. Free-Tier-Resilienz

- Bestehend: `RetryingChatClient` (TypeError/408/429/5xx/leere Completions, Reset-Hint-Pausen).
- **Neu: Modell-Rotation** — `LAURA_AGENT_MODEL_POOL` (Komma-Liste; Default = gesetztes
  `LAURA_AGENT_MODEL`, dann `openrouter/nvidia/nemotron-3-super-120b-a12b:free`,
  `openrouter/google/gemma-4-31b:free`, `openrouter/tencent/hunyuan-a13b-instruct:free`)
  und `LAURA_VLM_MODEL_POOL` analog (Default = `LAURA_VLM_MODEL`, dann
  `nvidia/nemotron-nano-12b-v2-vl:free`). Nach erschöpfter Retry-Leiter mit ausschließlich leeren
  Completions rotiert der Client zum nächsten Pool-Modell (Serving-Varianz ist pro Modell/Tag).
- **Checkpoints statt Held**: der Run darf sterben — das Board macht ihn billig wiederaufnehmbar
  (`resume` = Standardpfad jedes Session-Jobs, kein Sonderfall).
- Degradierte Reviews: schlägt das VLM für eine Szene endgültig fehl, entsteht ein Review mit
  `degraded=true` (Beschreibung aus dem Transkript); die Pipeline läuft weiter, QA sieht die
  Degradierung.

## 10. Fehlerbehandlung

- Board-Write-Validierung (pydantic) → Fehler als Tool-Result an den Agenten (Selbstkorrektur).
- Orchestrator-Budget: hartes Turn-Budget pro Session-Job (Default 60, env-konfigurierbar);
  Überschreitung → Job endet mit Board-Stand + klarer Meldung (resume-bar), nie Endlosschleife.
- Render-/ffmpeg-Fehler → `render_report.checks` + Job-Fehler mit Log-Pfad.
- QA `revise` → genau eine Revisionsrunde (v1); zweites `revise` liefert trotzdem den Export
  mit QA-Findings als Warnung (ehrlich, nicht blockierend).

## 11. Tests & Verifikation

- **Pure-Function-Tests**: Board-Graph (Invalidierung, Versionierung, Resume-Punkt),
  ROI→9:16-Fenster (Klemmen, Mindesthöhe, Rundung auf ganze Pixel), Zoom-Timing (Wort→Frame),
  Modell-Rotation (Pool-Reihenfolge, Rotationstrigger).
- **Agent-Tests mit injizierten Fake-Clients** (bestehendes Muster): Team produziert aus
  Fixtures ein vollständiges Board; Render wird mit erwarteten Parametern aufgerufen; adjust/
  revert invalidieren exakt die richtigen Artefakte.
- **Renderer-Golden-Checks**: ffprobe (Dauer/Auflösung) + Frame-Grabs an Zoom-Übergängen.
- mypy strict + ruff; `uv run pytest` grün.
- **Live-Validierung** (manuell zu prüfen): Session auf dem Overview-Asset; Erst-Produktion;
  „Kapitel 2 andere Szene" (nur Downstream neu); „zurück zu Storyline v1"; Ergebnis-Sicht.

## 12. Slices (Implementierungsreihenfolge)

1. **Board-Kern**: Schemas, Store, Versionierung, Invalidierungsgraph, Resume-Punkt (pur, TDD).
2. **Renderer `zoom_hybrid`**: ROI-Mathematik + ffmpeg-Filterbau + Golden-Checks.
3. **Team v2**: AgentSpecs, Board-Tools, Frame-Grab-Tools, Coding-Agent-Werkzeugkiste,
   Orchestrator-Task (Viral-Arc-Vertrag), Modell-Rotation.
4. **Sessions**: Endpunkte, Session-Jobs mit Resume-Standardpfad, adjust/revert-Routing.
5. **ChatPanel**: Session-Attach (Job-basiert), Folge-Nachrichten, Artefakt-Chips.
6. **Live-Validierung + Feinschliff** (inkl. Doku `docs/agentic-short-creator.md`-Update).

Slice 1+2 sind ohne LLM voll testbar; 3–5 nutzen das Fake-Client-Muster. Codex-Sperrgebiet
(`services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py`) wird nicht berührt.
