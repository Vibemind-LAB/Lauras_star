# VibeVideo Feature-Audit fuer Laura

Stand: 2026-06-14. Geklont nach `workspace/external-repos/`:

- `vibevideo` @ `bf0315f` — MIT, mit Zusatzbedingungen fuer AI-generierte Inhalte.
- `vibevideo-deepfake` @ `4bd1604` — proprietaer/vertraulich laut `LICENSE`; kein Code-Copy in
  Lauras Kern ohne explizite Freigabe.

## Kurzfazit

Die Repos sind fuer Laura wertvoll, aber nicht als direkter Import in den Kern. Die sinnvolle
Richtung ist: Laura bleibt frame-/sample-genauer Editor und ruft VibeVideo-Funktionen als optionale
Sidecars oder Adapter-Jobs auf. Ergebnisse kommen als WAV/MP4 zurueck, werden von Laura neu geprobt,
als synthetische Assets markiert und ueber bestehende Timeline-/Overlay-Lanes platziert.

Akzeptiertes Integrationsdesign: [`2026-06-14-vibevideo-laura-integration-design.md`](2026-06-14-vibevideo-laura-integration-design.md).

## Features, die fuer Laura Sinn machen

| Prioritaet | Feature | Quelle | Laura-Andockpunkt | Warum |
|---|---|---|---|---|
| P1 | Voiceover/TTS aus editiertem Transcript | `vibevideo-deepfake/voice/tts_engine.py`, `chatterbox_engine.py`, `fish_engine.py` | A2-Audio-Lane + Transcript-Panel | Schliest die aktuelle Luecke: Textaenderung erzeugt heute nur Captions/Timing, keine neue Stimme. |
| P1 | Frame-count A/V Sync Guard | `vibevideo-deepfake/lipsync/sync_guard.py` | Render-/Export-Preflight | Passt stark zu Lauras Invariante: Video-Frames sind Ground Truth, Audio wird getrimmt/gepadded. |
| P1 | Product-Demo aus Screenrecording | `vibevideo/product/demo_video.py` | eigenes `Demo/Auto-Edit`-Tool oder Export-Preset | Erkennt Szenen, erzeugt JSON-Config, Labels, TTS und kompakte Demo-Videos. Sehr passend fuer Laura-Demos. |
| P2 | TTS-to-original Silence Alignment | `vibevideo-deepfake/lipsync/align_audio.py` | Voiceover-Realign-Job | Stretcht nicht die Sprache, sondern justiert Pausen. Gute Qualitaet fuer neu generierte Stimme. |
| P2 | Lipsync Quality/Eval | `lipsync/quality/*`, `temporal_judge.py`, `adaptive_blend.py` | nach Reenact/Lipsync als Qualitaets-Gate | Liefert messbare Artefakt-/Mundregion-Kriterien statt reinem Bauchgefuehl. |
| P2 | Sora/B-Roll/Vision Clips | `vibevideo/sora/*` | V2-Overlay/B-Roll-Generator | Erzeugt externe Clips, die Laura als synthetische Assets platzieren kann. API-Key-/Kosten-Gate noetig. |
| P3 | Team/Split-Screen Pipeline | `vibevideo/pipeline/*` | spaeteres Template/Storyboard-Preset | Nuetzlich als Inspiration, aber MoviePy-floatlastig und zu projektspezifisch fuer den Kern. |
| P3 | MuseTalk/Wav2Lip Full Lipsync | `vibevideo-deepfake/lipsync/*` | separater Lipsync-Sidecar | Wertvoll, aber schwer, consent-sensibel, GPU-/Modell-lastig. Nach Voiceover und Audio-Lanes. |

## Empfohlene Reihenfolge

1. **Audio-Lane/Voiceover-Basis in Laura bauen.** Ohne A2-Lane, Lautstaerke, Ducking und Export-Mix
   haengt TTS in der Luft.
2. **Voiceover-Sidecar-Adapter.** Input: `segment_id`/Text, Reference-Audio oder Voice-ID,
   Ziel-Frames/Samples. Output: WAV + Provenienz. Backend muss ohne Sidecar weiter starten.
3. **Sync Guard als Render-Preflight.** Erst als reine Pruefung, spaeter optionales Enforce.
4. **Product-Demo-Preset.** Screenrecording analysieren, Vorschlags-Szenen/Labels in Laura-UI
   uebernehmen, nicht MoviePy direkt als Timeline-Zustand verwenden.
5. **Lipsync/Deepfake spaeter.** Nur nach Consent-Gate, synthetischer Kennzeichnung, Lizenzfreigabe
   und Qualitaetscheck.

## Architektur-Regeln fuer die Integration

- Kein MoviePy/Float-Sekunden als Laura-Zustand. Sidecar darf intern Sekunden nutzen; Laura speichert
  weiter Ganzzahl-Frames und Samples.
- Kein schweres Modell als Default-Dependency. Extras/Sidecars muessen fehlen duerfen.
- Kein Code-Copy aus `vibevideo-deepfake` in den Laura-Kern. Wegen proprietaerer Lizenz nur Adapter,
  Prozessaufruf oder private Submodule nach Freigabe.
- Jeder synthetische Output bekommt `synthetic=true`, `ai_effect`, Source-Asset, Range und Consent-ID
  sofern Identitaet/Stimme/Lipsync betroffen ist.
- API-Keys (`OPENAI_API_KEY`, `ELEVENLABS_API_KEY`) bleiben ausserhalb von Laura-Projektdateien.

## Konkrete Laura-Slices

### VV1 — Voiceover-Audio-Lane

- Backend: Audio-Overlay-Clips/A2-Lane, Lautstaerke, Mute/Replace/Mix, Export-Mix.
- UI: Voiceover/Musik-Spur im Assemble, Clip-Lautstaerke, einfache Fade-in/out.
- Tests: Sample-genauer Mix, Export-Dauer, kein Drift.

### VV2 — TTS/Voiceover-Sidecar

- Backend: `ai.voiceover` Job, `VoiceoverBackend` Protocol, `Unavailable/Stub/Sidecar`.
- Input: Text aus Transcript-Segment oder freier Voiceover-Block, Reference-Audio/Voice-ID.
- Output: WAV-Asset auf A2, synthetisch markiert.
- UI: `Stimme neu erzeugen` getrennt von `Speichern + neu ausrichten`.

### VV3 — Sync Guard

- Backend: Prueft Export/Sidecar-Outputs auf Frame-derived Video-Dauer vs. Audio-Dauer.
- UI: Warnung in Job-/Export-Zentrale mit delta-ms; optional `Fix audio duration`.

### VV4 — Product-Demo Assistant

- Backend: Screenrecording analysieren, Frames extrahieren, Szenenvorschlaege als JSON/Timeline-Draft.
- UI: Vorschlaege annehmen/ablehnen, Labels/Voiceovertext editieren, dann in Assemble-Sequenz schreiben.

### VV5 — Lipsync/Deepfake

- Backend: `ai.lipsync` Sidecar mit Consent-Record, Face-/Mouth-Probe, Quality-Gate.
- UI: nur sichtbar, wenn Sidecar installiert und Consent vorhanden ist.
- Export: synthetische Kennzeichnung Pflicht.
