# Vibemind-KI-Effekte → Laura (Integrationsplan)

Quelle: `Vibemind_V1/.../spaces/video/vibevideo_deepfake` (+ `vibevideo`). Ziel: deren
KI-Fähigkeiten als **optionale Effekte** in Laura nutzen, **andockend an die Overlay-Lanes**
aus dem Multi-Lane-Plan — ohne Lauras frame-genauen Kern zu verwässern.

## Prinzip (nicht verhandelbar)

- **Kein moviepy/Float-Code in Lauras Kern.** Die KI-Fähigkeiten leben als **optionaler
  Sidecar/Service**, den Lauras Job-Queue aufruft (genau wie Whisper/pyannote heute optionale
  Extras sind). Backend startet **ohne** GPU/Modelle; fehlt das Extra → UI sagt „nicht installiert".
- **Frame-genau an der Grenze:** Laura übergibt **Ganzzahl-Frame-Range + Quelle + fps**; der Service
  rechnet intern (falls nötig in Sekunden), liefert eine **Mediendatei** zurück; Laura **re-probt**
  sie (exakte Frames) und registriert sie als **Asset**. Lauras *Zustand* verlässt nie den Frame-Raum
  — der KI-Service ist eine Black-Box wie ffmpeg.

## Architektur

```
UI (Range + Params)
  → Laura Job (kind: ai.voiceover | ai.broll | ai.faceswap | ai.lipsync)
    → AI-Service (reuse vibevideo_deepfake / vibevideo, optionales Extra)
      → Mediendatei (WAV / MP4)
  → Laura: ffprobe → add_asset (synthetic=true, ai_effect=…)
  → Platzierung über die Overlay-/Source-Replace-Mechanik (Multi-Lane-Plan)
```

## Die vier Effekte + Andockpunkte

| Effekt | Vibemind-Quelle | liefert | landet als |
|---|---|---|---|
| **KI-Voiceover** | `voice/clone_and_tts` (ElevenLabs/chatterbox) | WAV | **A2-Overlay** (Audio über Range) |
| **KI-B-Roll** | `sora/` (OpenAI Sora) | MP4-Clip | **V2-Overlay** (Bild über Range) |
| **Faceswap** ⭐ | `faceswap/FaceSwapper.swap(frame)` | bearbeiteter Clip | **Effekt-Clip** (ersetzt Quelle des Range) |
| **KI-Dub (Lipsync)** | `lipsync/` (MuseTalk/Wav2Lip) | bearbeiteter Clip | **Effekt-Clip** (Lippen auf neue Stimme) |

**Faceswap-Fluss (frame-genau):** Range → Quellframes dekodieren → `FaceSwapper.swap` je Frame →
als CFR-Clip re-encoden → Asset → als Effekt-Clip auf V1/V2 für den Range platzieren. Per-Frame =
perfekt für Lauras Modell.

## Wiederverwendung

`vibevideo_deepfake` als **optionale Dependency** einbinden; ein dünner Laura-Adapter **importiert**
`FaceSwapper`, die TTS- und Lipsync-Entrypoints (kein Code-Copy). Schwere Deps (mediapipe, rembg,
insightface, musetalk, chatterbox) hängen **nur** an den jeweiligen Extras.

## Datenmodell (additiv, meins)

- Job-Kinds `ai.voiceover|ai.broll|ai.faceswap|ai.lipsync` (bestehende Queue).
- `assets`: + `synthetic BOOLEAN DEFAULT 0`, + `ai_effect TEXT` (Provenienz).
- Platzierung: nutzt **`role`/Overlay-Clips** aus dem Multi-Lane-Plan (kein neues Platzierungsmodell).

## Responsible-AI (Pflicht für Faceswap & Lipsync)

- **Consent-Record** vor jedem Swap (Person/Quelle bestätigen) — gespeichert. (Quell-Repo verlangt
  „explicit consent".)
- **Provenienz:** Ergebnis-Asset als `synthetic=true` markiert; sichtbares „KI/synthetisch"-Label in
  der UI + Metadaten (C2PA-style) im Export.
- **Opt-in & off by default:** Extra nicht vorinstalliert; `RESPONSIBLE_AI.md`-Policy aus dem
  Quell-Repo nach Laura übernehmen.
- Kein Veröffentlichungs-/Identitätsmissbrauch — nur auf eigenem/konsentiertem Material.

## Optionalität

Extras: `[ai-voice]` (ElevenLabs/chatterbox), `[ai-broll]` (OpenAI), `[ai-face]` (insightface/
mediapipe), `[ai-lipsync]` (musetalk/wav2lip). Keys via `.env` (`ELEVENLABS_API_KEY`,
`OPENAI_API_KEY`). Backend + alle Nicht-KI-Pfade laufen ohne sie.

## Phasen (NACH bzw. mit den Overlay-Lanes)

> Abhängigkeit: die **Platzierung** (Overlay-/Source-Replace) kommt aus Multi-Lane **Phase C/D**.
> Die KI-**Services** kann ich davor/parallel bauen (sie erzeugen nur Assets).

- **AI-1 — KI-Voiceover** (A2): am leichtesten, kein Faceswap-GPU; reift mit Audio-Overlay (Phase C).
- **AI-2 — KI-B-Roll** (Sora, V2): API-basiert; mit Video-Overlay (Phase D).
- **AI-3 — Faceswap** ⭐ (Effekt-Clip): deine Priorität. Per-Frame-Service + Consent/Provenienz +
  Platzierung über V-Overlay. GPU (insightface/mediapipe).
- **AI-4 — KI-Dub/Lipsync**: am komplexesten (GPU, Qualitäts-Judges, DTW-Align).

## Besitz

Laura-Seite **meins** (Job-Kinds, API, Asset-Registrierung, UI, Migration `synthetic`/`ai_effect`).
Die KI-Logik = **die Vibemind-Module** als optionale Dep (separat, GPU-isoliert). **`timelines.py`
(deins) unberührt.**

## Offene Punkte / Voraussetzungen

GPU-Verfügbarkeit unter Windows (insightface/musetalk); Modell-Downloads; ElevenLabs/OpenAI-Keys +
Kosten; Lizenzen der Modelle; Ethik/Recht (Consent-Pflicht). Vor AI-3/AI-4 klären.

---

## Update — Modell-Adapter, Kennzeichnung (2 Ebenen), Aufnahme, Probe

### Modell-Adapter (pluggable) — lizenz-sauber

Laura ruft nur `face_swap(clip|frame, target)` — das Backend ist Config:

| Backend | Lizenz | für |
|---|---|---|
| inswapper_128 (lokal, insightface) | **non-commercial** | Dev/Demo/intern, **nicht** veröffentlicht |
| **Picsi.ai** (InsightFace-eigene API, Modelle „Dax"/„Evi", closed) | **kommerziell** | veröffentlichte Education+Marketing-Inhalte |
| DeepSwap-API · Magic Hour-API · Replicate `easel/advanced-face-swap` | kommerziell (paid) | Alternativen |

→ Kommerziell = praktisch **API-Backend** (Cloud, Kosten); gute *lokale* kommerziell-lizenzierte
Swap-Gewichte sind rar. Picsi.ai ist die Hausmarke der inswapper-Macher → naheliegendste Lizenz.
Open-Source-Code bleibt davon unberührt; nur das gewählte Backend entscheidet die Nutzungsrechte.

### Deepfake-Kennzeichnung — **2 Ebenen** (Pflicht, sobald Faceswap an)

- **Ebene 1 — sichtbar:** Burn-in-Label im Render (ffmpeg `drawtext`/`overlay`), z.B. „KI · Deepfake".
  Erfüllt die EU-AI-Act-Kennzeichnungspflicht (2026 zunehmend Mandat).
- **Ebene 2 — unsichtbar („versteckter Pixel-Code" = Verifikation „mit Laura erstellt"):**
  robustes **Pixel-Wasserzeichen** (übersteht Re-Encode/Screenshot) → **Meta Video Seal**
  (Open-Source, Frequenzdomäne, für Video) **+ C2PA Content Credentials** (signiertes Provenienz-
  Manifest). Pixel-WZ trägt das Signal *im Bild*, C2PA das signierte „nutrition label" *in den
  Metadaten* — ergänzen sich (Metadaten lassen sich strippen, Pixel-WZ nicht).
- **Verify-Tool:** liest Video Seal + C2PA → bestätigt Herkunft. (Vgl. SynthID/AudioSeal als Alternativen.)

### Aufnahme (neue Ingest-Quelle)

Kamera/Screen-Capture als Quelle: Electron `getUserMedia`/`getDisplayMedia` → `MediaRecorder` →
Datei → **normaler Laura-Ingest** (probe/proxy/audio-extract). So nimmt man Quell- *oder* Ziel-
Material direkt auf, ohne Import.

### Face-Probe / Vorschau (vor dem langsamen Voll-Render)

Schnelltest „passt das Gesicht auf diese Person?": ein paar **Sample-Frames** swappen (oder
Live-Vorschau über das vorhandene `faceswap/live_server.py`) → Vorher/Nachher zeigen, **bevor** der
~3,7-fps-Voll-Render läuft. Spart Zeit, zeigt Qualität früh.

### Quellen (Recherche)

Faceswap-Lizenz/Optionen: [InsightFace commercial licensing](https://www.insightface.ai/services/models-commercial-licensing) ·
[Replicate advanced-face-swap (commercial)](https://replicate.com/easel/advanced-face-swap) ·
[DeepSwap](https://www.nemovideo.com/alternative/deepswap). Kennzeichnung/Provenienz:
[C2PA vs SynthID vs Meta Video Seal](https://www.simalabs.ai/resources/c2pa-vs-synthid-vs-meta-video-seal-2025-enterprise-ai-video-authenticity) ·
[C2PA & Watermarking-Mandate 2026](https://magiclight.ai/news/c2pa-and-global-watermarking-mandates-for-ai-video-in-2026/).
