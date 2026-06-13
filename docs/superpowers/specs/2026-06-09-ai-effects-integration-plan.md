# Vibemind-KI-Effekte → Laura (Integrationsplan)

Quelle: die externen VibeVideo-Repos:

- [`Flissel/vibevideo`](https://github.com/Flissel/vibevideo) — MIT; Pipeline für Team-Videos,
  Sora/Vision-Clips, Product-Demos, TTS/STT.
- [`Flissel/vibevideo-deepfake`](https://github.com/Flissel/vibevideo-deepfake) — proprietär laut
  Repo; Voice cloning + Lipsync/Deepfake-Tools, nur mit Consent-/Lizenz-Gate.

Ziel: deren KI-Fähigkeiten als **optionale Effekte** in Laura nutzen, **andockend an die
Overlay-Lanes** aus dem Multi-Lane-Plan — ohne Lauras frame-genauen Kern zu verwässern.

Feature-Audit nach lokalem Clone: [`2026-06-14-vibevideo-feature-audit.md`](2026-06-14-vibevideo-feature-audit.md).

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

`vibevideo` und `vibevideo-deepfake` als **externe Sidecar-/Adapter-Quellen** behandeln; ein dünner
Laura-Adapter ruft deren CLI/API-Entrypoints oder einen lokalen Sidecar auf (**kein Code-Copy in den
Laura-Kern**). Schwere Deps (MoviePy, Sora/OpenAI, ElevenLabs, MuseTalk/Wav2Lip, insightface usw.)
hängen **nur** an den jeweiligen Extras.

Wichtig: `vibevideo-deepfake` wird wegen proprietärer Lizenz/Identitätsrisiko nicht blind vendort.
Vor produktiver Nutzung braucht es:

- explizite Lizenz-/Nutzungsfreigabe,
- Consent-Record pro Person/Job,
- synthetische Asset-Provenienz,
- UI-Hinweis, wenn der Deepfake-Sidecar fehlt oder nicht freigegeben ist.

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

---

## Verifizierte Recherche-Updates (Workflow, 15 Agenten, web + adversarial verifiziert)

**Wichtigste Korrektur — Swap ≠ Reenactment.** Das Reel-Szenario („ich spiele, das Zielgesicht
kopiert meine Mimik, ohne Helm") ist **Reenactment / Portrait-Animation**, **nicht** Face-Swap.
Lauras geplanter inswapper-Faceswap deckt nur **Swap** ab (fremde Identität auf vorhandene
Clip-Performance). → **Ein zweiter Effekt-Typ ist nötig**, um das Reel nachzubauen.

**Der „Helm" ist Software.** Die „Tracking-Punkte/Sensoren" sind markerlose Landmarken, aus
RGB-Video regressiert (mediapipe FaceMesh 468/478 · insightface 2d106 · FAN 68) — reine
Visualisierung. Beim Swap ist der eigentliche „Treiber" ohnehin das **ArcFace-512-Embedding**
(via AdaIN in einen GAN), nicht die Landmarken; audio-getrieben (SadTalker) braucht gar kein
Treiber-Gesicht.

**Ehrliche Qualität.** inswapper_128 + GFPGAN = gut für Social/Phone, **nicht Broadcast/„exakt"**
(128px-Decke; ~70–85% brauchbar auf unkontrolliertem Material; bricht bei Close-up, Kopfdrehung
>30°, Bewegungsunschärfe, Verdeckung, Drift/Flackern über lange Clips). „Exakt" braucht **alle vier
Hebel**: ≥256/512-Generierung + Restoration+Grain-Matching + (Per-Identitäts-Training DeepFaceLab
512 / 500k–1M Iter / Tage GPU / 24 GB+ **oder** Top-Kommerziell Evi/Dax/DeepSwap 4K) + zeitliche
Konsistenz.

**Konkrete Plan-Ergänzungen:**
1. **Adapter in zwei Typen:** `swap` (Identität → Clip) **+** `reenact` (Ziel-Portrait → meine
   Performance). Reel = `reenact`.
2. **Reenactment-Backend:** **LivePortrait** (KwaiVGI, **MIT** → kommerziell nutzbar, webcam-fähig,
   ~12,8 ms/Frame @256 auf RTX 4090) als Default; DeepFaceLive „Face Animator" als Live-Pfad.
3. **Restoration-Stufe** (wählbar GPEN/CodeFormer/GFPGAN) + Grain-Re-Matching + optional
   ESRGAN-Upscale + Color-Transfer + gefederter Paste-back. Pipeline: detect/align → swap@128 →
   restore → upscale → color-match → seamless paste.
4. **Trained-Model-Pfad** (DeepFaceLab `.DFM`) als optionaler High-End-Adapter — Nische (Tage
   Training, 24 GB+); Default bleibt Zero-Shot+Restoration **oder** kommerzielle API.
5. **Temporale Konsistenz first-class** (optical-flow-Smoothing / video-native Chunks; Edits in
   Ganzzahl-Frames; **Pflicht-Human-QC-Schritt** im Editor).
6. **Lizenz-Guardrail:** NC-inswapper-Gewichte dürfen **nicht** in veröffentlichten Output → für
   Published-Inhalte den kommerziellen API-Adapter erzwingen.

**Faktenkorrekturen:** buffalo_l = **ResNet-50** (w600k_r50), nicht R100 (R100 = antelopev2,
Embedding bleibt 512-dim). C2PA aktuell 2.x; **ISO/IEC DIS 22144 ist noch Draft**, nicht ratifiziert.
**EU-AI-Act Art. 50 + California SB 942 (verschoben via AB 853): beide ab 2. August 2026.**

Quellen: [LivePortrait (MIT)](https://github.com/KwaiVGI/LivePortrait) ·
[Faceswap-Modellvergleich](https://1337sheets.com/comparing-face-swap-models-blendswap-ghost-inswapper-simswap-uniface/) ·
[DeepFaceLab-Guide](https://www.deepfakevfx.com/guides/deepfacelab-2-0-guide/) ·
[EU AI Act Art. 50](https://artificialintelligenceact.eu/article/50) ·
[C2PA vs SynthID vs Video Seal](https://www.simalabs.ai/resources/c2pa-vs-synthid-vs-meta-video-seal-2025-enterprise-ai-video-authenticity).
