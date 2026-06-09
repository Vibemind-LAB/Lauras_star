# Reel-Produktion — Track (Vertikal · Captions · Scene-Compositing) + Eval/QC

Ziel: Laura soll fertige, **konsentierte + gekennzeichnete** AI-Aufklärungs-/Marketing-**Reels**
produzieren (9:16, Hook-Caption, Person in Szene). Setzt auf Multi-Lane
(`2026-06-09-multilane-both-tabs-design.md`) + AI-Effekte
(`2026-06-09-ai-effects-integration-plan.md`) auf.

## Stand (verifiziert im Repo)
- `render/mp4.py` = `libx264` trim + `concat v=1:a=0` — **kein** `scale`/`crop`/`drawtext`/`overlay`
  → heute **nur 16:9-Concat**, keine Vertikal-/Caption-/Compositing-Fähigkeit.
- Geplant (Design, nicht gebaut): Multi-Lane Phase A–D, AI-Adapter `swap`+`reenact`, Voiceover→A2,
  B-Roll→V2, Lipsync, 2-Ebenen-Kennzeichnung, Consent, Aufnahme, Face-Probe.
- Das Reel-Szenario ist **Reenactment** (meine Performance treibt das konsentierte Zielgesicht),
  nicht Face-Swap.

## Produktionsrezept → Lauras Tabs (was schon geplant ist vs. echte Lücke)
Hook/Skript (extern) · **Caption**=LÜCKE · Driving-Performance aufnehmen=geplant · Zielmaterial+Consent=geplant ·
`reenact`/`swap`=geplant · **Person in Szene**=LÜCKE(Compositing) · Voiceover→A2=geplant · Lipsync=geplant ·
Übergänge=geplant(Phase A/B) · **Captions/Hook**=LÜCKE · **9:16+Reframe**=LÜCKE · Musik→A2=geplant ·
**Kennzeichnung** (Burn-in + Video Seal + C2PA)=geplant · Export 9:16=teils Lücke.
→ Neu sind **drei „Verpackungs"-Lücken + Scene-Compositing**.

## Die drei echten Lücken

### A — Vertikal 9:16 + Auto-Reframe
- **Billige Basis (GPU-frei, Default):** `crop=ih*9/16:ih` → `scale=1080:1920`, optional 1× Gesicht
  detektieren + x-Offset fixieren.
- **Smart-Reframe (optionales Extra):** Tracking-Pass (Vorbild [Google AutoFlip](https://research.google/blog/autoflip-an-open-source-framework-for-intelligent-video-reframing/),
  [auto-vertical-reframe](https://github.com/KazKozDev/auto-vertical-reframe): PySceneDetect → YOLO/MediaPipe+ByteTrack →
  geglätteter Crop-Pfad) → zeitvariabler `crop=w:h:x(t):y(t)` aus vorgebackenen Keyframes.
- **Safe-Zones** ~900×1400 in 1080×1920. **Crop-Keyframes in Ganzzahl-Frames.**

### B — Burned-in animierte Captions + Titel/Hook-Overlays
- Whisper-Extra + **WhisperX-Forced-Alignment** (optionales Extra) → Wort-Timing <100 ms →
  **ASS-Karaoke** (`\kf`) → `ffmpeg -vf ass=` im bestehenden Filtergraph. Fallback ohne Extra: plain
  `word_timestamps` (GPU-frei, driftet). Hook/Titel via `drawtext`/PNG-`overlay`.
- **Caption-Timing als Ganzzahl-Frames im Zustand; ASS ist Export-Artefakt, nie Projektzustand**
  (analog OTIO-Regel). „Emotion-Caption" = Marketing, nicht versprechen.

### C — Scene-Compositing (Person in die Fake-Szene) — die harte
Kein Single-Best; SOTA = **Hybrid** (Szene erzeugen/drehen + Identität als separate, frame-ausgerichtete Ebene):
- (a) Swap/Reenact auf **reales Stand-in-/Stock-Footage** — höchster Realismus für „physisch da".
- (b) **Green-Screen/Matting + Composite** — kontrollierbarster + rechtlich risikoärmster Fallback (Person echt).
- (c) **Voll-KI-Gen** (Veo/Kling/Runway/Sora) — beste Szene, aber Likeness nicht garantiert → Swap/Reenact obendrauf.
- (d) Image-to-Video aus Foto — niedrigste Schwelle, „puppet"-Gefühl.
- **Empfehlung Solo:** generierte/lizenzierte Szene + identitäts-lockendes Reenact, **(b) Green-Screen** als
  risikoärmster Einstieg. **Identitäts-Ebene getrennt von Szenen-Ebene**, jeder Generator hinter dem
  **pluggable Adapter** → Laura bleibt generator-agnostisch.

## Phasen-Track (auf Multi-Lane + AI-Effekte aufgesetzt)
- **R0 — Reel-Render-Skelett (SIMPELSTER Weg zum ersten fertigen Reel):** `mp4.py` + `scale` + statischer
  Center-Crop 1080×1920 + ein `drawtext`-Hook + Disclosure-Burn-in + 9:16-Export-Preset. **Null KI, null neue
  schwere Deps.** Hängt nur an Export.
- **R1 — Captions:** WhisperX → ASS-Karaoke → `ass=`.
- **R2 — Smart-Reframe:** Tracking-Pass (optionales Extra); statischer Crop = GPU-freier Fallback.
- **R3 — Identitäts-Ebene:** = AI-Plan AI-3 (`reenact`/`swap`) + Consent-Gate + Face-Probe + Restoration-Stufe (braucht Multi-Lane C/D-Platzierung).
- **R4 — Scene-Compositing:** erst (b) Green-Screen als nativer Effekt-Layer, dann (c) generative i2v über den Adapter (B-Roll-Lane = Andockpunkt).
- **R5 — Stimme+Lipsync+Musik:** = AI-Plan AI-1/AI-2/AI-4 auf A2/V2.

**Reihenfolge:** R0 zuerst (sofortiger sichtbarer Erfolg, null Risiko) → R1/R2 (höchster Leverage, reuse Whisper-Infra) → dann die schwere KI/Compositing-Schicht.

## Eval / QC-Schicht (übernommen aus dem externen Deep-Research-Report)
Objektive Metriken **+** perzeptuell **+** Ablation — als QC-Gate für jeden AI-Effekt:
- **Lipsync:** LSE-C / LSE-D, AV-Offset (SyncNet-Klasse).
- **Identitäts-Drift:** ArcFace-Cosine auf Held-out-Clips.
- **Perzeptuell:** LPIPS + temporale Konsistenz — **immer mit Human-MOS/A-B gepaart** (FVD **nicht allein**).
- **Ablationen:** je 1 Komponente weglassen (Restoration, Audio-Prior, temporales Filter, per-Identitäts-Map).
- **Pflicht-Human-QC-Schritt** im Editor vor Freigabe.

## Invarianten & Responsible-AI (intakt)
- Ganzzahl-Frames (Crop-Keyframes + Caption-Timing); Sekunden/Centisekunden nur beim Formatieren.
- OTIO = Wahrheit; ASS/EDL/Crop-Pfade = Exporte, nie Zustand.
- Schwere Modelle (WhisperX, Reframe-Tracking, LivePortrait/inswapper, TTS/Lipsync) = je eigenes optionales Extra; Backend startet GPU-frei + kann R0.
- Idempotenz: Reframe-/Caption-Pass auf `(input, pipeline_version)`.
- **Nicht-umgehbar:** Consent-Record vor jedem reenact/swap/voice-clone; Export **gated** auf Consent + 2 Ebenen (sichtbares Burn-in **+** unsichtbares Wasserzeichen **Meta Video Seal** **+** C2PA-Manifest).
- **Recht (verifiziert):** C2PA aktuell 2.x; **ISO/DIS 22144 noch Draft** (nicht als ratifizierte ISO bewerben). **EU-AI-Act Art. 50 + CA SB 942 (via AB 853): beide ab 2. Aug 2026.** Wasserzeichen-Detektion downstream unzuverlässig → Legitimität ruht auf Consent + signiertem C2PA + sichtbarem Burn-in.

## Quellen
[AutoFlip](https://research.google/blog/autoflip-an-open-source-framework-for-intelligent-video-reframing/) ·
[auto-vertical-reframe](https://github.com/KazKozDev/auto-vertical-reframe) ·
[WhisperX](https://github.com/m-bain/whisperx) · [ASS-Tags](https://aegisub.org/docs/latest/ass_tags/) ·
[LivePortrait (MIT)](https://github.com/KwaiVGI/LivePortrait) ·
[AI-Video-Vergleich](https://lushbinary.com/blog/ai-video-generation-sora-veo-kling-seedance-comparison/) ·
[EU AI Act Art. 50](https://artificialintelligenceact.eu/article/50) ·
[C2PA vs SynthID vs Video Seal](https://www.simalabs.ai/resources/c2pa-vs-synthid-vs-meta-video-seal-2025-enterprise-ai-video-authenticity).
