# Szenen-Shorts: Relevanz, Multi-Format, Voice-Neufassung (Design)

_Datum: 2026-07-08 · Status: Richtung vom User freigegeben (Formate + Voice-Scope) · Branch: `feat/generate-ui`_

## Ziel (User-Vision, gespiegelt)

1. **Relevanz aus dem Transkript**: Szenen werden nach inhaltlicher Relevanz zum Thema gerankt —
   das Transkript jeder Szene entscheidet mit, nicht nur Visual-/Audio-Scores.
2. **„Szene 1, 2, 19, 47 → ein Short für Insta, X und LinkedIn"**: Szenen per **Nummer** wählen;
   **ein Auftrag** rendert **drei Plattform-Formate**: **Insta 9:16 · X 16:9 · LinkedIn 1:1**.
3. **Transcript-Master-Agent (ElevenLabs)**: am Ende wird die Tonspur **neu eingesprochen** — der
   Agent schreibt aus dem Transkript ein neues Skript (der User gibt Richtung/Ton vor), ElevenLabs
   synthetisiert die Stimme, sie **ersetzt** den O-Ton; Captions folgen dem neuen Skript.

## Slices (einzeln lauffähig, in dieser Reihenfolge)

### Slice 1 — Szenen-Relevanz aus dem Transkript

- `scene_transcripts(asset_id)`: pro Szene (order_index) der Transkript-Text (Szenen-Frame-Range ×
  Segmente; reine DB-Arbeit auf Vorhandenem).
- `rank_scenes_by_topic(asset_id, topic, k=10)`: lexikalisches Scoring (Token-Overlap, deutsch-
  tolerant lowercase) von Szenen-Text gegen das Thema → Top-k mit Score + Snippet.
  **Semantik-Ausbaustufe** (optional, später): vorhandene Embedding-Infra.
- Director/Analyst bekommen beide Tools; Director-Prompt: erst ranken, dann wählen.

### Slice 2 — Szenen per Nummer + Plattform-Formate

- Format-Presets: `insta` = 1080×1920 (9:16, vorhanden) · `x` = 1920×1080 (16:9, vorhanden) ·
  `linkedin` = 1080×1080 (**1:1, neu** in `render_clips_mp4`: Center-Crop oder fit+blur wie 9:16).
- `render_scenes(asset_id, scene_numbers, formats, hook_text, fit)`: Szenen-Nummern → Frame-Ranges
  (order_index der Rough-Cut-Szenen) → EIN Aufruf erzeugt **einen Export pro Format** (gleiche
  Segmente, gleiche Captions, anderes Framing). Rückgabe: Liste `{format, export_id, job_id}`.
- Agent-Tool + Editor-Prompt: „Szene N, M, …" aus dem Task → `render_scenes`; `fit="blur"` bei
  Screen-Content bleibt.
- Captions: wie heute pro Segment versetzt; ASS-PlayRes pro Format.

### Slice 3 — Transcript Master + ElevenLabs (Voice-Neufassung)

- **Neuer Agent `transcript_master`**: schreibt aus den gewählten Szenen-Transkripten ein neues,
  dichtes Skript in der Task-Sprache; die **User-Richtung** (Ton, Länge, CTA) kommt als Teil des
  Chat-Auftrags („… und sprich es locker/seriös/energisch neu ein").
- `synthesize_voice(text, voice_id)`: ElevenLabs-Backend (stdlib-HTTP wie vlm_ollama), Env:
  `LAURA_ELEVENLABS_API_KEY`, `LAURA_ELEVENLABS_VOICE` (Default-Voice). **Optional/graceful**:
  ohne Key `ok=False` mit Grund — Backend startet/arbeitet ohne.
- `replace_voiceover(export|segments, wav)`: Render-Variante, die die Original-Audiospur durch die
  synthetisierte ersetzt (Video-Trims wie gehabt; Audio = neue Spur, Länge auf Video gedehnt/gekürzt
  wird NICHT — v1: Video schneidet auf Voice-Länge bzw. Voice kürzer als Video ist ok, Rest stumm
  mit sanftem Fade). Captions v1: Zeilen-Captions aus dem neuen Skript (gleichmäßig verteilt);
  Karaoke-Worttimings via ElevenLabs-Timestamps als Ausbaustufe.
- Provenance: Export mit `ai_effect="voiceover_elevenlabs"` kennzeichnen (KI-Kennzeichnungspflicht
  der Export-Pipeline bleibt).

## Invarianten

- Ganzzahl-Frames, end-exclusive; Szenen-Nummern = `order_index + 1` (menschlich 1-basiert, im
  Tool dokumentiert). OTIO bleibt Source of Truth; Renders sind Exporte.
- Alles optional/graceful: ElevenLabs nur mit Key; 1:1 ohne neue Pflicht-Dependencies.
- Codex-Subtree unangetastet.

## Tests

- Slice 1: pure Ranker-Tests (Overlap-Scoring, k, leeres Transkript graceful); scene_transcripts
  gegen echte In-Memory-DB.
- Slice 2: Format-Preset-Mapping (Namen → Maße), render_scenes erzeugt N Exporte mit korrekten
  Optionen (gemockter Renderer), 1:1-Zweig in render_clips_mp4 (Arg-Assertions, kein ffmpeg).
- Slice 3: Skript-Agent im Roster (Spec-Tests), ElevenLabs-Backend gemockt (Payload/URL), graceful
  ohne Key; Audio-Replace-Optionen im Handler (gemockter Renderer).

## Offen (nicht blockierend)

- ElevenLabs-Key: für den echten TTS-Lauf brauchst du `LAURA_ELEVENLABS_API_KEY` (Konto nötig).
- Karaoke-Timings aus ElevenLabs (`with_timestamps`) als späterer Feinschliff.
