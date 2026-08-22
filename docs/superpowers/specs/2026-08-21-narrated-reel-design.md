# Narrated Reel — Produktvideo-Collagen nativ in Laura

_Design-Spec, 2026-08-21. Kontext: Die sechs VibeMind-Produktvideos wurden über Lauras
HTTP-API geschnitten und gerendert, aber drei Schritte liefen außerhalb: TTS (Chatterbox
standalone / ElevenLabs via short_creator-Code), Caption-Burn für Autoren-Text (manueller
ffmpeg-Pass) und der Collage-Aufbau (Beat-Liste → Timeline als externes Skript). Diese Spec
schließt die drei Lücken, damit der Workflow „narrated product reel" komplett in-product läuft._

## Ziele

1. **TTS-Backends:** ElevenLabs und Chatterbox als Voiceover-Backends nutzbar —
   ohne harte Abhängigkeit im Core (Invariante: schwere Modelle sind optionale Extras).
2. **Natural-Length-Synthese:** Ein Voiceover kann seine Clip-Länge aus der gesprochenen
   Zeile ableiten statt in einen vorgegebenen Span gepresst zu werden.
3. **Narration-Captions:** Der bestehende Karaoke-Caption-Pfad kann Wort-Timings aus den
   synthetisierten Voiceover-Clips ziehen (Autoren-Text, nicht ASR-Text).
4. **Collage-Builder:** Eine Beat-Liste `[(Zeile, Asset, src_in), …]` wird serverseitig zu
   einer fertigen Timeline (Clips + Crossfades + Voice-Spur), optional direkt gerendert.
5. **Härtung:** Der wiederholt getroffene SQLite-Lock im History-Checkpoint-Pfad und die
   fehlenden Defaults beim Projekt-Anlegen.

Nicht-Ziele: VLM-Beat-QA (separater Arc), Drive-Upload, UI-Flächen (API/MCP-first; die UI
kann später andocken).

## Architektur-Überblick

Alle Bausteine docken an Bestehendes an:

- `ai/voiceover_backend.py` bekommt ein **ElevenLabs-Backend** (stdlib-HTTP gegen
  `api.elevenlabs.io`, Key/Voice aus Env) und behält den **Sidecar** als Chatterbox-Pfad —
  Chatterbox selbst wird ein eigenständiger Sidecar-Server (neues `services/tts-sidecar/`),
  der den dokumentierten Kontrakt (`GET /healthz`, `POST /voiceover` → WAV) erfüllt.
- Das `VoiceoverBackend`-Protokoll bekommt einen Fit-Parameter: `fit_to_slot=True` (heute)
  vs. `fit_to_slot=False` (natürliche Länge; Backend schreibt die volle Sprech-WAV).
- `ai/handlers.py::handle_voiceover` unterstützt `fit="natural"`: synthesize ohne Slot-Fit,
  Länge messen (ffprobe), `seq_out = seq_in + frames + pad`, danach unverändert
  Overlap-Delete + Platzierung. Beim Synthese-Erfolg wird ein **Wort-Timing-Sidecar**
  (`<wav>.words.json`) geschrieben: Whisper-Timings (optional, falls faster-whisper
  installiert) mit Autoren-Text-Mapping (1:1 bei gleicher Wortzahl, sonst proportional);
  Fallback ohne Whisper: gleichverteilte Wort-Slots über die gemessene Dauer.
- `render/captions_source.py` bekommt `voiceover_caption_words(db, timeline_id)`: Wörter aus
  den Sidecars aller Voiceover-Audio-Clips, auf Sequence-Frames gemappt. Der Render-Handler
  wählt bei `opts["captions"]` die Quelle: Voiceover-Wörter, falls vorhanden, sonst wie
  bisher Transkript-Wörter (`caption_source: "auto" | "voiceover" | "transcript"`).
- Neuer Job `ai.narrated_reel` + `POST /projects/{project_id}/narrated-reel`: baut aus der
  Beat-Liste die Timeline (append_clip, Crossfades, letzter Clip Fade-out), synthetisiert
  pro Beat natural-length, platziert die Voice-Clips (`replace_original`) und stößt optional
  den Render (mit Captions) an. Ein MCP-Tool `build_narrated_reel` spiegelt den Endpoint.

## Verträge (genau)

### 1. ElevenLabs-Backend (`ai/voiceover_backend.py`)

```python
class ElevenLabsVoiceoverBackend:
    name = "elevenlabs"
```

- Env: `LAURA_ELEVENLABS_API_KEY` (Pflicht), `LAURA_ELEVENLABS_VOICE` (Default-Voice-ID;
  `voice_id`-Argument überschreibt), `LAURA_ELEVENLABS_MODEL` (Default
  `eleven_multilingual_v2`).
- `available()`: Key vorhanden (kein Netz-Roundtrip; Fehler schlagen beim Synthese-Call auf,
  wie beim Sidecar üblich).
- `synthesize(...)`: stdlib `urllib` POST auf
  `https://api.elevenlabs.io/v1/text-to-speech/{voice}` (`xi-api-key`-Header), Antwort-MP3
  in ein Tempfile, dann ffmpeg → mono `sample_rate` PCM-WAV. `fit_to_slot=True`: wie SAPI
  `apad` + `-t`; `fit_to_slot=False`: volle Länge ohne Pad/Trim. HTTP-Fehlerbody (z. B.
  `payment_issue`) landet wörtlich in der RuntimeError-Message. Der API-Key darf in keiner
  Log-/Fehlermeldung auftauchen.
- Registry: `resolve_voiceover_backend` kennt `{"elevenlabs", "el"}`; `"auto"`-Reihenfolge
  bleibt unverändert (SAPI→Stub — Cloud niemals implizit).

### 2. Protokoll-Erweiterung `fit_to_slot`

- `VoiceoverBackend.synthesize(..., fit_to_slot: bool = True)` (Keyword-only, Default
  erhält das heutige Verhalten aller Aufrufer).
- Stub: ignoriert das Flag (erzeugt exakt `duration_frames`; Natural-Mode mit Stub nutzt
  `duration_frames` als gewünschte Länge — deterministisch testbar).
- SAPI: bei `fit_to_slot=False` entfallen `apad`/`-t` (nur Resample auf mono/`sample_rate`).
- Sidecar: Flag wandert als `fit_to_slot` ins JSON-Payload (der Chatterbox-Sidecar
  ignoriert Slot-Fit und liefert immer natürliche Länge; `duration_frames` bleibt als Hint
  im Payload).

### 3. Natural-Fit im Voiceover-Job

- `VoiceoverRequest` (api/models.py): neu `fit: Literal["slot", "natural"] = "slot"` und
  `pad_frames: int = 12` (nur bei natural relevant; 0–120). Bei `fit="natural"` ist
  `seq_out_frame_exclusive` weiterhin Pflicht, dient aber nur als **Obergrenze** (Slot-Ende;
  Clip endet früher, wenn die Sprache kürzer ist) — so bleibt der Undo/Idempotenz-Pfad
  unverändert und nichts kann unbegrenzt wachsen.
- Handler: bei natural → `synthesize(fit_to_slot=False)`, Frames messen
  (`probe_media`/ffprobe, aufrunden), `seq_out_eff = min(seq_in + frames + pad, seq_out)`;
  Overlap-Delete und `add_timeline_audio_clip` nutzen `seq_out_eff`. Ergebnis-Dict enthält
  `measured_frames` und `seq_out_frame_exclusive` (effektiv).
- Job-Ergebnis (`result_json`) ist der Rückkanal für den Collage-Builder.

### 4. Wort-Timing-Sidecar

- Ort: `<voiceover>.wav` → `<voiceover>.wav.words.json`,
  Schema `{"words": [{"text": str, "start_frame": int, "end_frame_exclusive": int}], "source": "whisper" | "even"}`
  — Frames relativ zum WAV-Anfang, Projekt-Framerate.
- Erzeugung in `handle_voiceover` nach erfolgreicher Synthese (beide Fit-Modi):
  - Wenn `faster_whisper` importierbar: Transkription (`word_timestamps=True`,
    Sprache aus Payload), dann Autoren-Mapping: Wortliste aus dem Payload-Text
    (Bindestrich-Einzeltoken entfernen); bei gleicher Zahl 1:1, sonst Wort *j* → Slot
    `min(n_w-1, j*n_w//n_a)`. Der TEXT stammt immer aus dem Payload (ASR verhört Namen).
  - Sonst: gleichverteilte Slots über die gemessene Dauer.
  - Sidecar-Fehler sind nie fatal (Warning; Voiceover bleibt gültig).
- Whisper läuft im Job-Prozess nur, wenn bereits installiert (Analysis-Extra) — kein neuer
  Dependency-Zwang.

### 5. Caption-Quelle Voiceover

- `render/captions_source.py::voiceover_caption_words(db, timeline_id) -> list[Word]`:
  alle Audio-Clips der Timeline, deren Asset ein `.words.json`-Sidecar neben der
  Originaldatei hat; Mapping Wort-Frame → `seq_in_frame + (start_frame - asset_in_frame)`,
  geclippt auf den Clip-Span; Reihenfolge nach `seq_in`.
- Render-Handler: `caption_source`-Option (`"auto"` Default): bei `auto` zuerst
  `voiceover_caption_words`, fallback `timeline_caption_words`. Neues Preset
  `"wide"` existiert bereits; Default-`fontsize`/`margin_v` für wide: 54/48.
- `RenderRequest` (api/models.py): neu `captions: bool = False`,
  `caption_source: Literal["auto","voiceover","transcript"] = "auto"`,
  `caption_preset: Literal["reels","tiktok","shorts","wide"] = "reels"` — sie wandern wie
  `burn_captions` in `options` (burn_captions bleibt unverändert der SRT-Pfad).

### 6. Collage-Builder `ai.narrated_reel`

- `POST /projects/{project_id}/narrated-reel` (202) mit
  ```json
  {
    "name": "Rowboat Produktvideo",
    "beats": [{"text": "…", "asset_id": "…", "src_in_frame": 1320, "pad_frames": 12}],
    "crossfade_frames": 8, "final_fade_frames": 12,
    "backend": "elevenlabs" | "sidecar" | …, "voice_id": null, "language": null,
    "render": true, "caption_preset": "wide"
  }
  ```
  Validierung: 1–64 Beats; jedes Asset gehört zum Projekt, ist Video und online;
  `src_in_frame < duration_frames`. Antwort: `{"timeline_id", "job_id"}`.
- Handler `ai.narrated_reel` (ai/handlers.py, Queue `ai`): pro Beat sequenziell
  1. synthesize natural-length (gleicher Codepfad wie handle_voiceover — Refactor in eine
     gemeinsame Funktion `_synthesize_voiceover_asset(...)`, damit Asset/Provenance/Sidecar
     identisch entstehen),
  2. Beat-Länge = gemessene Frames + `pad_frames`, geclippt auf Asset-Ende,
  3. `append_clip` (repos-Ebene, wie der Operations-Endpoint), Transition Crossfade auf
     allen Clips außer dem letzten (Fade-out `final_fade_frames`),
  4. Voice-Clip `replace_original` über den Beat-Span (fade_in 3 / fade_out 4,
     ducking 100).
  Danach optional Render-Export (`captions=True`, `caption_source="voiceover"`,
  `caption_preset` aus dem Request) — gleicher Codepfad wie der Render-Endpoint
  (create_export + enqueue), Export-Id im Job-Ergebnis. Cancel-Checks zwischen Beats.
- MCP-Tool `build_narrated_reel` (mcp/tools.py + server.py): dünner Spiegel des Endpoints,
  Docstring mit Beat-Beispiel; Ergebnis nennt timeline_id/job_id/export_id.

### 7. Chatterbox-Sidecar (`services/tts-sidecar/`)

- Eigenständiges Skript `chatterbox_sidecar.py` (+ README): stdlib-`http.server` oder
  FastAPI-frei; Endpunkte laut Kontrakt. Lädt `ChatterboxTTS.from_pretrained(cuda)` einmal,
  `POST /voiceover` synthesisiert mit `audio_prompt_path` aus `CHATTERBOX_VOICE_REF`
  (Env; Default `reference.wav` neben dem Skript), resampelt auf `sample_rate` mono WAV.
  Läuft im eigenen venv (E:\chatterbox); KEIN Eintrag in pyproject des Backends.
- README dokumentiert Start (`.venv\Scripts\python.exe chatterbox_sidecar.py --port 8898`)
  und die Env-Kopplung (`LAURA_VOICEOVER_BACKEND=sidecar`, `LAURA_VOICEOVER_URL`).

### 8. Härtung

- `editing/history.py`: der Checkpoint-Schreibpfad (`push_undo_checkpoint` bzw. der
  umgebende `timeline_checkpoint`-Kontext) nutzt eine IMMEDIATE-Transaktion (Muster von
  ae01228), damit parallele append_clip/audio-clip-Aufrufe keine `database is locked`-500er
  mehr werfen.
- `POST /projects`: `sequence_rate_num=30`, `sequence_rate_den=1`, `drop_frame=False` als
  Defaults im Request-Model.

## Fehlerfälle

- Backend nicht verfügbar/Fehler → Job failed mit Klartext (ElevenLabs-Body inklusive,
  Key nie), Timeline bleibt im letzten konsistenten Zustand (Checkpoint vor dem Job).
- Whisper fehlt → `source:"even"`-Sidecar, Captions funktionieren weiter.
- Beat länger als Quell-Asset → Clip endet am Asset-Ende (Warning im Job-Ergebnis).
- Sidecar down → `available()` False → 500 mit klarer Message beim Job, nicht beim Enqueue.

## Tests (Kern)

- Backend-Registry: elevenlabs auflösbar, auto unverändert, Key-Fehlen → available False.
- EL-Synthese gemockt (urllib): mp3→wav-Pfad, fit/natural, Fehlerbody in Message, Key nicht
  in Message.
- Natural-Fit: Stub-Backend, gemessene Länge bestimmt Span; Obergrenze respektiert;
  Overlap-Delete auf effektivem Span.
- Sidecar-Schema: Whisper-Mapping 1:1 und proportional (Whisper gemockt), even-Fallback.
- voiceover_caption_words: Mapping/Clipping/Ordnung; auto-Fallback auf Transkript.
- narrated_reel-Handler: 3-Beat-Collage mit Stub-Backend end-to-end (Clips, Transitions,
  VO-Clips, Job-Ergebnis, optionaler Export enqueued); Validierungsfehler des Endpoints.
- Härtung: parallele Checkpoints ohne BUSY (Regression analog ae01228-Test).

## Live-Verifikation

Abschluss des Arcs: das Laura-Produktvideo (Projekt 82f9595f…) einmal komplett über
`build_narrated_reel` mit Chatterbox-Sidecar neu bauen — gleiche Beats, Ergebnis muss dem
manuell gebauten v1 strukturell entsprechen (7 Clips, Crossfades, Captions eingebrannt).
