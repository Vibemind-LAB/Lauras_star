# Szenen-Auswahl (Gate S) + Voice pro Szene — Design

Datum: 2026-08-06 · Branch: `feat/generate-ui` · Status: Entwurf zur Review

## 1. Kontext & Problem

Der erste englische Film aus der Chat-Produktion (Export `3f4e2758`) zeigte drei
Qualitätsprobleme, die der User live benannt hat:

1. **Deutsch/Englisch gemischt** — der Sprachwechsel-Rewrite erwischte nicht alle Zeilen;
   die Charter-Regel „never leave chapters behind" ist nur Prompt, kein Guard.
2. **Bild ≠ Ton** („du zeigst n8n, sprichst über Rowboat") — eine durchgehende Voice-Spur
   wird über eine Szenenliste gelegt; Text und Bild sind nur auf Kapitel-Ebene gekoppelt.
   Verschärft durch degradierte VLM-Reviews (Ollama war down → blinde Szenenwahl).
3. **Keine Kontrolle über die Szenenwahl** — der User sieht erst im fertigen Film, welche
   Szenen Laura gewählt hat.

Entscheidungen des Users (2026-08-06, AskUserQuestion):

- Szenen-Vorschläge **nach Transkript-Bestätigung (Gate A), vor dem Script** — das Script
  wird nur für gewählte Szenen geschrieben.
- Auswahl per **Chat-Karte mit anklickbaren Thumbnail-Kacheln**; Chat-Text („nimm 2 und 5
  statt 4") geht zusätzlich immer.
- **Pflicht-Gate mit Vorauswahl**: Die Produktion wartet immer; Lauras Empfehlung ist
  vor-angehakt, ein Klick „Auswahl übernehmen" reicht.
- Voice-over **pro Szene** (Vorschlag des Users): jede Script-Zeile bekommt ihren eigenen
  Voice-Clip, das Videosegment wird auf dessen Länge gebunden.

## 2. Ziele / Nicht-Ziele

**Ziele**

- Neues Pflicht-Gate S zwischen Gate A und Script: Kandidaten-Vorschlag → User-Auswahl →
  strukturell erzwungene Beschränkung von Storyline/Script auf die Auswahl.
- Voice pro Szene: Synthese pro Script-Zeile, Segmentlänge = Clip-Länge, Audio↔Bild-Sync
  per Konstruktion.
- Grounding: Script-Zeilen werden mit den Fakten ihrer Szene (Transkript-Ausschnitt +
  VLM-Beschreibung) im Prompt geschrieben; Secondbrain-Faktencheck in der Charter.

**Nicht-Ziele**

- Kein neues Renderer-Feature: der bestehende Ein-Spur-Pfad (`_replace_audio` +
  ASS-Karaoke) bleibt unverändert; die Spur wird vorher konstruiert.
- Keine automatische Spracherkennung pro Zeile (Chip `task_e15419c6` bleibt separat).
- Kein Umbau von Gate A/B; kein Umbau des v1-Pfads (`/assets/{aid}/auto-short`).
- Bilder-in-den-Chat bleibt der nächste eigene Arc.

## 3. Architektur-Überblick

Neuer Ablauf einer Auto-Short-Session:

```
Gate A (Transkript bestätigen)                          [unverändert]
  → VLM-Scene-Reviews + Kandidaten-Vorschlag            [Team-Phase 1]
  → Gate S: SceneSelectionCard, User wählt              [NEU — Pflicht]
  → Storyline + Script NUR für gewählte Szenen          [Team-Phase 2, strukturell begrenzt]
  → Gate B (Script freigeben)                           [unverändert]
  → Voice pro Szene → Cutlist → Sheet → Render          [deterministischer Tail, erweitert]
```

Zwei getrennt umsetzbare Pläne unter diesem Spec:

- **Plan 1 — Gate S**: funktioniert auch mit der heutigen Ein-Spur-Voice.
- **Plan 2 — Voice pro Szene**: unabhängig von Gate S lauffähig; zusammen ergeben sie
  den szenen-gebundenen Pfad.

## 4. Plan 1: Gate S — Szenen-Auswahl

### 4.1 Board-Artefakt `scene_selection`

Neue Ketten-Wurzel vor `storyline` (`board.py`: `_CHAIN` wird
`("scene_selection", "storyline", "script", …)`, `_SINGLETONS` ergänzt). Modell in
`board_models.py`:

```python
class SceneCandidate(BaseModel):
    scene_number: int
    src_start_frame: int
    src_end_frame_exclusive: int
    thumb_frame: int              # Szenenmitte, fürs Frontend via assetFrameUrl
    description: str              # VLM ("was man sieht"); bei degraded: Hinweis-Text
    transcript_snippet: str       # gekürzt aus get_scene_transcript ("was gesagt wird")
    rationale: str                # warum vorgeschlagen
    recommended: bool             # Lauras Vorauswahl

class SceneSelection(BaseModel):
    version: int = 0
    candidates: list[SceneCandidate]
    selected_scene_numbers: list[int] = []   # leer bis Bestätigung
    confirmed_utc: str | None = None         # None = Gate offen
    parents: dict[str, str] = {}             # Wurzel: bleibt leer
```

- `content_hash` läuft über den bestehenden generischen Mechanismus.
- Der Vorschlag wird von einem neuen Tool `propose_scene_selection` geschrieben
  (`confirmed_utc=None`); die Bestätigung schreibt **nur der Server** (Confirm-Endpoint
  bzw. Chat-Weg), nie ein Agent. Ein erneuter Vorschlag (`Board.save`) invalidiert
  downstream — gewollt.

### 4.2 Vorschlags-Erzeugung (Team-Phase 1)

- Nach Transkript-Bestätigung reviewt das Team die Szenen wie bisher (`review_scene`,
  degraded-tolerant). Neues Tool `propose_scene_selection(candidates)` validiert:
  Szenennummern existieren, Frame-Ranges stimmen mit `_resolve_scene` überein,
  `transcript_snippet` nicht leer (aus `get_scene_transcript` gekürzt), mindestens
  1 `recommended`.
- Bei degradiertem VLM: `description` = „(keine Bildanalyse verfügbar)" — der Vorschlag
  blockiert nie auf dem VLM; das Transkript-Snippet trägt dann die Auswahl-Info.
- Nach erfolgreichem `propose_scene_selection` endet die Team-Phase (analog zur
  Gate-B-Pause); `resume_point` zeigt auf die Storyline-Phase.

### 4.3 Gate-Durchsetzung (strukturell, nicht Prompt — I2-Lektion)

- `save_storyline` (production_tools.py:1648): lehnt ab, wenn (a) keine bestätigte
  `scene_selection` existiert oder (b) ein `scene_numbers`-Eintrag außerhalb
  `selected_scene_numbers` liegt. Stempelt
  `parents={"scene_selection": _content_hash(selection)}` beim Save (:1697) und in der
  Re-Stempel-Passage (:1725–1748).
- `save_script_chapter`: zusätzlicher Guard — Zeilen dürfen nur gewählte Szenen
  referenzieren (Fehlertext nennt die verbotene Szene).
- `run_production` / Orchestrator: solange `confirmed_utc is None`, endet jeder Lauf nach
  dem Vorschlag mit einer Pause-Nachricht (kein Endlos-Retry). `deterministic_eligible`
  bleibt unberührt (greift erst nach Gate B).

### 4.4 API + Router

- `GET`-Bedarf: `Board.status()` (und damit `ProductionStatus`) bekommt
  `scene_gate: {enabled, pending, confirmed, candidates?, recommended?, selected?}` —
  das Frontend liest alles aus dem Status; die `asset_id` für `assetFrameUrl` kommt aus
  dem vorhandenen `status.meta.asset_id`, kein eigener Fetch.
- **Confirm-Endpoint**: `POST /production/{session_id}/scene-selection:confirm` mit
  `{"scene_numbers": [int, ...]}`. Validierung: nicht leer, Teilmenge der Kandidaten,
  Gate offen. Schreibt `selected_scene_numbers` + `confirmed_utc` atomar (Board-Lock),
  enqueued dann den Resume-Lauf (wie `approve_script`, inkl. Busy-Guard
  `_production_job_busy`).
- **Chat-Weg**: neues Router-Tool `select_scenes` mit `{"scene_numbers": [int, ...]}`
  (Beispiele im System-Prompt: „nimm 2 und 5 statt 4" → Kandidatenliste aus dem
  Kontext ableiten: erwähnte Nummern togglen relativ zur Empfehlung). Der Executor-Handler
  ruft dieselbe Confirm-Logik. Bei offenem Gate + unklarer Nachricht → `clarify` mit
  Verweis auf die Karte.

### 4.5 Frontend `SceneSelectionCard`

- Neue Komponente in `apps/desktop/src/components/chat/` (Dispatch im
  `ProductionActionCard`-Umfeld wie der Gate-B-Zweig, ActionCard.tsx:372–384 als Muster).
- Kacheln: `client.assetFrameUrl(assetId, thumb_frame)` (api.ts:1313, Blob→ObjectURL) +
  Beschreibung + Snippet; Klick toggelt lokale Auswahl (initialisiert aus `recommended`);
  Button „Auswahl übernehmen" → neue Client-Methode
  `client.confirmSceneSelection(sessionId, sceneNumbers)`.
- Erste klickbare Kachel-Karte der App — bewusst schlicht (Grid, Tailwind, Checkbox-Optik
  über Rahmen/Halbtransparenz), kein Drag & Drop.
- `deriveTarget`/UnknownActionLine-Falle beachten: **jeder** String-Leser des neuen
  Tool-Namens wird im Plan explizit aufgeführt (die „zweiter Leser"-Bug-Klasse traf
  diese Session zweimal).

## 5. Plan 2: Voice pro Szene

### 5.1 Synthese pro Zeile

`synthesize_script_voice` (production_tools.py:2109) wird auf Zeilen-Clips umgestellt:

- Pro Script-Zeile (Storyline-Reihenfolge, wie heute `_lines_in_storyline_order`) ein
  ElevenLabs-Call → `{workspace}/voiceovers/lines/{line_hash}.mp3` + eigener
  Timings-Sidecar. `line_hash` = Hash über Zeilentext + Voice-Einstellungen →
  **Zeilen-Cache**: Follow-ups synthetisieren nur geänderte Zeilen neu.
- Danach **Konstruktion der Gesamtspur**: Concat der Clips mit einem festen
  Pausen-Budget `INTER_SCENE_GAP_S = 0.35` zwischen Zeilen (ffmpeg concat + `anullsrc`
  bzw. `adelay`-Mix; Implementierungsdetail im Plan). Wort-Timings der Zeilen werden mit
  ihren Offsets zu **einem** gemergten Sidecar (`{"words": [...]}`) verschoben — Format
  identisch zu heute (voice.py:116).
- `VoiceArtifact` (board_models.py:311) wächst um
  `segments: list[VoiceSegment] | None = None`:

```python
class VoiceSegment(BaseModel):
    scene_number: int
    chapter: int
    line_hash: str
    mp3_path: str
    duration_s: float
    offset_s: float      # Startposition in der konstruierten Gesamtspur
```

- `mp3_path`/`timings_path`/`voice_s` auf Artefakt-Ebene zeigen weiter auf die
  konstruierte Gesamtspur → **Renderer, Captions, QA lesen unverändert**.
  `segments=None` = Legacy-Ein-Spur (alte Boards bleiben lesbar).

### 5.2 Cutlist-Kopplung

`build_cutlist` (production_tools.py:2209) bekommt einen Segment-Pfad:

- Wenn `voice.segments` vorhanden: Segment i (bereits heute 1:1 Szenen-Eintrag) dauert
  exakt `segments[i].duration_s + INTER_SCENE_GAP_S`; das LETZTE Segment exakt
  `duration_s` ohne Gap — so ist Video-Summe == Audio-Summe und `-shortest` schneidet
  nichts ab (ein Tail hinter dem letzten Clip würde vom Mux wieder entfernt).
  Startframes im Quellmaterial wie bisher aus `best_window`/Storyline-Window.
- Die Kapitel-Fenster-Arithmetik (`chapter_audio_windows`, `_scale_chapter_durations`)
  wird auf diesem Pfad **nicht** durchlaufen; sie bleibt für Legacy-Boards
  (`segments=None`) unverändert bestehen.
- Kapazitäts-Guard wandert nach vorn: `save_script_chapter` prüft pro Zeile
  „geschätzte Sprechdauer ≤ Szenen-Kapazität" (Wortrate aus der bestehenden
  Kalibrierung; Kapazität aus Frame-Range/fps). Ablehnung nennt Szene, Ist- und
  Maximal-Dauer → der Fehler landet beim Autor, nicht erst im Tail.
  `build_cutlist` behält einen zweiten, harten Guard (echte Clip-Dauer > Kapazität →
  Abbruch mit Handlungsanweisung „Zeile für Szene N kürzen").

### 5.3 Sync per Konstruktion

Weil (a) die Gesamtspur aus den Clips mit exakt den Pausen der Segmentgrenzen gebaut
wird und (b) jedes Videosegment auf die Länge seines Clips gebunden ist, gilt:
`offset_s` jedes Voice-Segments == Startzeit seines Videosegments im Film. Kein
Drift möglich; `line_starts`-basiertes Zoom-Timing (production_tools.py:789) liest den
gemergten Sidecar und funktioniert unverändert.

### 5.4 Grounding + Secondbrain (Teil von Plan 2, Charter-Arbeit)

- `build_production_task`: pro **gewählter** Szene ein Fakten-Block (Transkript-Snippet +
  VLM-Beschreibung aus den Kandidaten) direkt in der Aufgabe; Charter-Regel für
  `scene_author`: „Die Zeile beschreibt, was ihre Szene zeigt/sagt — kein freies
  Marketing über das Thema."
- Charter-Zeile Secondbrain: „Produkt-/Eigennamen vor dem Schreiben per `brain_search`
  prüfen, wenn verfügbar" (Tools sind env-gated über `LAURA_SECONDBRAIN_PATH`; seit
  2026-08-06 im Livetest-Setup gesetzt).

## 6. Fehlerbehandlung

- **VLM down/degraded**: Vorschlag kommt trotzdem (Snippet-basiert), `description`
  kennzeichnet die Lücke. Kein Blockieren, kein Crash.
- **ElevenLabs-Fehler bei einer Zeile**: 1 Retry, dann harter Tool-Fehler mit
  Zeilen-/Szenen-Referenz (deterministischer Tail eskaliert ehrlich wie bisher).
- **Leere Auswahl / unbekannte Szene beim Confirm**: 422 mit klarer Meldung; Gate bleibt
  offen.
- **Auswahl ändern nach Bestätigung**: erneuter `POST …:confirm` bei bereits
  bestätigtem Gate ist erlaubt, solange kein Produktionsjob läuft (Busy-Guard);
  er schreibt eine neue Artefakt-Version → Staleness-Kaskade erledigt den Rest.
- **Kein Kandidat sprechbar** (Script-Kapazitäts-Guard schlägt überall an): Fehlertext
  empfiehlt kürzere Zeilen oder mehr Szenen wählen.

## 7. Tests (Kernfälle)

- Board: `scene_selection` als Chain-Wurzel — Auswahl-Änderung invalidiert
  storyline→…→qa_report; `restore_coherent_suffix` mit neuem Glied.
- Guards: `save_storyline`/`save_script_chapter` lehnen unbestätigte/fremde Szenen ab.
- Confirm-Endpoint: Happy Path, leere Liste, fremde Nummer, Busy-Guard, Re-Confirm.
- Router: `select_scenes`-Klassifikation („nimm 2 und 5 statt 4", „passt so").
- Voice: Zeilen-Cache (unveränderte Zeile → kein Neu-Call), Merge-Sidecar-Offsets exakt,
  `segments=None`-Legacy-Pfad unverändert.
- Cutlist: Segmentdauer == Clip-Dauer + Gap; Kapazitäts-Guard beidseitig; Legacy-Pfad
  regressionsfrei.
- Frontend (vitest): Karte rendert Kandidaten, Toggle, Confirm-Call; `deriveTarget` und
  alle Tool-String-Leser kennen `select_scenes`.
- Volle Gates wie üblich: `uv run pytest`, bare `uv run mypy`, `pnpm test` +
  typecheck + bundle.

## 8. Offene Punkte (bewusst außerhalb)

- Automatische Sprachprüfung pro Zeile (Chip `task_e15419c6` / eigener Arc).
- Bilder-in-den-Chat (nächster Arc; Entscheidungen liegen fest).
- Übergangs-Feinschliff (Audio-Crossfades zwischen Szenen-Clips statt harter Pausen) —
  erst bewerten, wenn der erste per-Szene-Film hörbar ist.
