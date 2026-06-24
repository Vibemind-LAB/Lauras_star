# Deep Research: Automatische Short-Extraktion aus Roughcut-Material

- **Datum:** 2026-06-24
- **Methode:** Multi-Agent Deep-Research (6 Researcher, web-zitiert, adversarial verifiziert), geerdet in Lauras Stack
- **Ziel:** aus einem AI-Roughcut automatisch hochwertige Social-Shorts (TikTok/Reels/YT-Shorts) extrahieren — saubere Bildschnitte, transkript-sichere Grenzen, natürliche Audio-Übergänge, semantisch sinnvolle Clips.

## Executive Summary & Empfehlung für Laura (§7)

**Befund:** Laura ist bereits **~70 %** einer hochwertigen Auto-Shorts-Pipeline — die schweren Teile existieren und sind korrekt gebaut (von den Researchern im Code verifiziert): frame-genaue SBD (TransNetV2 + PySceneDetect adaptive), Wort-Level-ASR+Align (faster-whisper + WhisperX), **transkript-sichere Cut-Platzierung** (`joint.py`/`semantic.py`/`editorial.py` — Cuts nur auf Satz-/Sprecher-Grenzen, nie mitten im Wort), echter `xfade`/`acrossfade`-Renderer, VLM-Transition-Review, OTIO + Integer-Frames + Audio-in-Samples.

**Der genannte Cloud-Stack (Redis/Supabase/n8n) passt NICHT** zu Laura (local-first FastAPI + SQLite + lokaler Job-Runner, ADR-0003) — gehört nur ins optionale `server`-Profil. Nicht für den Desktop hinzufügen.

**Was wirklich fehlt (klein, lokal, lizenz-sauber):**
1. `analysis/shorts_segments.py` — Kandidaten-Fenster (15–60 s), die **nur** auf `sentence_end`/`speaker_turn`-Frames beginnen/enden → transkript-sicher per Konstruktion.
2. `analysis/shorts_score.py` — deterministischer multimodaler Scorer (Hook, Vollständigkeit, Längen-Fit, Sprechdichte, Schnittsauberkeit).
3. `analysis/shorts_qa.py` — `blackdetect`/`freezedetect` + „kein Wort zerschnitten" (`editorial_metrics.pct_mid_word==0`).
4. `shorts.extract`-Job-Handler nach `ai/auto_pipeline.py` + `shorts_candidates`-Tabelle (spiegelt `scenes`).
5. **Silero VAD** + **`vad_filter=True` in `asr.py`** — größter Qualitätsgewinn für AI-Roughcuts (killt Whisper-Halluzinationen auf Stille/Musik).

**CutScore-Modell (§5):** `CutScore(t)=w1·Visual+w2·AudioSilence+w3·Transcript+w4·Semantic − p1·WordInterruption − p2·AudioJump − p3·FaceMotion`, robust-z-normalisiert (Median/MAD), Gewichte erst manuell (Transkript-Sicherheit am höchsten, Wort-Schnitt = **harte Sperre**) → gelernt (logistische Regression → pairwise Learning-to-Rank → kontrastiv), bootstrappt aus Lauras Ledger akzeptierter Cuts. Segment-Auswahl per **DP/Graph-Search** über Kandidaten-Grenzen.

**Agentic-Prinzip:** *Das Modell schlägt vor, der deterministische Code entscheidet* — Frame-Genauigkeit/Transkript-Sicherheit nie an ein LLM delegieren (genau Lauras `transition_review`-Muster). LLM/VLM bringt echten Wert bei Hook-/Highlight-Ranking + Titel/Caption-Text + Boundary-*Vorschlag* (per Transkript-Referenz, dann frame-genau gesnappt).

**Direkt relevant für unsere offenen Punkte:**
- Der **Schwarz-Filter-Bug** ist im Report explizit als Risiko „black-frame over-drop" gelistet — empfohlener Fix: **flag-and-snap statt aggressivem Verwerfen** (ein Short darf nicht auf Schwarz starten, aber man darf nicht *alle* Shots wegwerfen).
- Das **Auto-Glätten**: nur same-source-Dead-Air-Jumps kurz überblenden (`acrossfade` ~6–12f), harte Schnitte an Satz-/Sprecher-Grenzen lassen — genau was `transition_review` schon vorgibt (kein „alles weichgespült").

---
## 1 · Shot Boundary Detection

I have enough verified evidence. The task list is unrelated legacy work — I'll leave it untouched. I have what I need to write the cited section. One correction to flag: my first search surfaced a confusingly-worded "TransNet 0.931 vs TransNetV2 0.916" claim from a *different* paper (3D depthwise attention variants on BBC); the canonical TransNetV2 paper numbers are BBC 96.2 / RAI 93.9 / ClipShots 77.9, which I verified directly from the arXiv source. I'll use the canonical numbers and note the discrepancy.

Here is the research section.

---

# Shot Boundary Detection for Laura — Methods, Benchmarks, and Recommendation

## 1. What the problem actually is

Shot Boundary Detection (SBD) segments a video into **shots** — uninterrupted runs of frames from one camera take. Boundaries come in two flavors:

- **Hard cuts (CUT):** abrupt frame-to-frame change. Easy, ~90%+ of all boundaries in most footage.
- **Gradual transitions (GT):** dissolves, fades (to/from black), wipes — spread over 2–30+ frames. These are where classical methods fall apart and where almost all the residual error lives.

For Laura's actual goal — cutting Shorts out of an **AI-generated rough-cut** — note a critical context point: the rough-cut is *already assembled from clips Laura controls*, so many "boundaries" are known a priori from the OTIO timeline. SBD here is mostly useful for (a) source footage that arrives un-segmented, and (b) verifying/snapping cut points on imported material. Keep this in mind for the recommendation — the marginal accuracy of a heavier model matters less than it would for blind documentary footage.

## 2. Classical methods (PySceneDetect family)

These compute a per-frame **dissimilarity score** between adjacent (or near-adjacent) frames and threshold it.

| Method | Signal | Strength | Failure mode |
|---|---|---|---|
| **Pixel/frame differencing** | Sum of absolute pixel intensity differences | Trivial, fast, near-zero deps | Extremely sensitive to motion, object movement, noise; flash/illumination triggers false cuts ([survey](https://link.springer.com/article/10.1007/s10462-024-10742-1)) |
| **Color-histogram difference** | χ²/Manhattan/Euclidean distance between frame histograms (HSV common) | More motion-robust than pixel diff; the de-facto classical baseline | Sensitive to global illumination changes; misses cuts between visually similar shots; weak on gradual transitions ([CEUR survey](https://ceur-ws.org/Vol-2589/Paper6.pdf)) |
| **Edge Change Ratio (ECR)** | Fraction of edges entering/leaving between frames | More invariant to illumination; can localize dissolves/wipes | Expensive (edge extraction), fragile under fast motion and registration error ([ScienceDirect overview](https://www.sciencedirect.com/topics/computer-science/boundary-detection)) |

**Thresholding is the real problem.** A single global threshold cannot separate "fast camera pan" (high score, no cut) from "real cut." Two mitigations matter for Laura:

- **Adaptive / rolling-window thresholding.** This is exactly what **PySceneDetect's `AdaptiveDetector`** does: it runs `ContentDetector` to get per-frame content scores, then flags a boundary only where the score exceeds a **rolling average of neighboring frames** by an `adaptive_threshold` ratio, over a `window_width`. This specifically *"helps mitigate false detections in situations such as fast camera motions"* ([PySceneDetect 0.7 detectors docs](https://www.scenedetect.com/docs/latest/api/detectors.html)). This is the single most important classical technique for robustness under motion.
- **Dual-threshold (twin-comparison)** for gradual transitions: a low threshold accumulates a slow build-up (dissolve) and a high threshold catches the cut; the classical Zhang twin-comparison scheme. PySceneDetect's content score plus its fade/threshold detectors partially cover fade-to-black, but **classical methods remain weak on dissolves** in general — this is the documented gap that deep methods close.

Classical range-based F1 sits roughly **0.75–0.82** on hard benchmarks per the recent [OmniShotCut](https://arxiv.org/html/2604.24762v2) comparison, i.e. roughly in TransNetV2 territory on *easy* cut-heavy content but well below it on gradual/ambiguous material.

**Laura already has PySceneDetect adaptive — keep it.** It is the correct CPU/no-GPU fallback and the right default for clean, cut-dominated AI rough-cuts. Do not remove it.

## 3. Deep-learning methods

### TransNetV2 (Souček & Lokoč, 2020)
The current workhorse and what Laura already ships as the `scene-ml` extra.

- **Architecture:** 6 stacked **DDCNN** cells, each 4× `3×3×3` convolutions at dilation rates 1/2/4/8 along time, giving a ~97-frame temporal receptive field; two prediction heads (single-frame and all-transition-frames) ([arXiv 2008.04838](https://ar5iv.labs.arxiv.org/html/2008.04838)).
- **Input:** frames resized to **48×27**, processed in **100-frame** windows — deliberately tiny, which is why it is fast and CPU-viable.
- **Gradual transitions:** explicitly trained on synthesized dissolves (2–30 frames) mixed with real transitions (optimal mix cited as 15% real / 35% cuts / 50% dissolves) — this is *why* it beats classical methods on GTs.
- **F1 (canonical paper):** ClipShots **77.9**, BBC Planet Earth **96.2**, RAI **93.9** ([repo](https://github.com/soCzech/TransNetV2)).
- **License: MIT.** TF + a maintained **PyTorch reimpl** that *"produces identical results"* and is `pip install transnetv2-pytorch` ([PyPI](https://pypi.org/project/transnetv2-pytorch/)); weights also mirrored on Hugging Face. License-clean and local-runnable on CPU or GPU.

### AutoShot (Zhu et al., CVPR-W 2023)
Neural-architecture-search over 3D ConvNets + transformers; ships the new **SHOT** dataset (853 short videos, 11,606 annotations, 200-video / 2,716-annotation test set — *short-form social content*, average 39.5s clips, 2.59s shots) ([arXiv 2304.06116](https://ar5iv.labs.arxiv.org/html/2304.06116)).

- **F1:** SHOT **0.841** vs TransNetV2 **0.799** (**+4.2%**); ClipShots **0.787** (+1.1%), BBC **0.971** (+0.9%), RAI **0.955** (+1.2%).
- **Efficiency:** 37 GMACs (vs TransNetV2's 41) at the F1-optimal point — *cheaper and more accurate*.
- **License: MIT**, PyTorch, pretrained `ckpt_0_200_0.pth` provided ([repo](https://github.com/wentaozhu/AutoShot)).
- **Caveat I verified:** AutoShot's repo does **not** ship a TransNetV2 file or advertise weight-compatibility; its model is `supernet_flattransf_3_8_8_8_13_12_0_16_60.py`. So it is **not a literal drop-in for TransNetV2 weights**. However, AutoShot is architecturally in the same family (DDCNN-derived NAS blocks) and operates on the same low-res short-window frame regime, so the *integration surface* (decode → resize → window → per-frame logits → threshold) is the same as Laura's existing TransNetV2 path. Migration cost is "swap the model module + weights + re-tune threshold," not "rebuild the pipeline." (Evidence on exact input dims for AutoShot is **weak** — the README omits them and I could not fetch the model source; treat 48×27/100-frame as *likely-same* but verify against the checkpoint before relying on it.)

### Transformer / VLM SBD (2024–2026)
Newer than AutoShot, reported to beat both:
- **OmniShotCut** — shot-query transformer, joint range + relational modeling ([arXiv](https://arxiv.org/html/2604.24762v2)).
- **TransVLM** — vision-language framework, reports beating PySceneDetect/TransNetV2/AutoShot and even larger general VLMs ([project page](https://chence17.github.io/TransVLM/)).

Both are research-stage, heavier, and license/weights are not confirmed clean for local shipping. **Evidence is weak** (no independent reproductions fetched; arXiv-only). Not recommended for Laura now.

## 4. Comparison table

| Method | Type | F1 ClipShots | F1 BBC | F1 RAI | F1 SHOT (short-form) | GT/dissolve handling | Runtime | Deps / License | Local-first fit |
|---|---|---|---|---|---|---|---|---|---|
| Pixel/histogram diff | Classical | ~0.75–0.82* | — | — | — | Poor | Real-time, CPU | OpenCV / permissive | Fallback only |
| **PySceneDetect Adaptive** | Classical+rolling thr | ~0.75–0.82* | — | — | — | Fair (fades), weak (dissolves) | Real-time, CPU | OpenCV / **BSD-3** | **Keep — CPU default** |
| **TransNetV2** | DDCNN (3D dilated) | **77.9** | **96.2** | **93.9** | 79.9 | **Good** (trained on synth dissolves) | Fast; 41 GMACs; CPU-viable @48×27 | TF/**PyTorch**, **MIT**, pip + HF weights | **Have it — keep as ML default** |
| **AutoShot** | NAS (3D ConvNet+Transf) | 78.7 | 97.1 | 95.5 | **84.1** | Good (same training regime) | Faster; **37 GMACs** | PyTorch, **MIT**, weights provided | Worth adding *if* short-form gain matters |
| OmniShotCut | Shot-query transformer | SOTA-claimed | — | — | — | Strong (relational) | Heavier | Research; license unconfirmed | No (premature) |
| TransVLM | Vision-language | beats all above (claimed) | — | — | — | Strong | Heaviest (VLM) | Research; license unconfirmed | No (premature) |

\* Classical range-F1 band per [OmniShotCut](https://arxiv.org/html/2604.24762v2); the survey literature does not report per-dataset classical F1 consistently — treat as indicative, **evidence weak**. A second source ([3D-depthwise-attention paper](https://pmc.ncbi.nlm.nih.gov/articles/PMC10457897/)) quotes BBC ~0.916/0.931 for TransNetV2/TransNet — these are *that paper's re-runs of attention variants*, not the canonical numbers; I used the original TransNetV2 paper figures (96.2/93.9/77.9) and flag the discrepancy.

## 5. Recommendation for Laura — keep / add / upgrade

**Keep:**
- **PySceneDetect `AdaptiveDetector`** as the no-GPU, license-clean (BSD-3) default and as the rolling-window robustness layer under fast motion. This is the right classical baseline and you already have it.
- **TransNetV2** (`scene-ml` extra) as the ML default. It is MIT, has an identical-output PyTorch reimpl (`pip install transnetv2-pytorch`), runs at 48×27/100-frame so it is genuinely CPU-viable, and explicitly handles dissolves/fades — which raw PySceneDetect does not. Do not drop it.

**Should you adopt AutoShot over TransNetV2?** **Conditional yes, as an optional `scene-ml-shot` extra — not a replacement.** Specifically:
- **Adopt it when the input is genuinely short-form / social-native footage** (fast montage, frequent quick cuts, lots of gradual transitions). That is exactly the SHOT dataset's domain, and that is exactly Laura's stated goal (TikTok/Reels/Shorts). The **+4.2% F1 on SHOT (0.841 vs 0.799)** is the *only* benchmark that matches Laura's target distribution, and it is the largest gap. The general-domain gaps (BBC +0.9%, RAI +1.2%, ClipShots +1.1%) are too small to justify a swap on their own.
- It is **MIT-licensed, PyTorch, ships weights, and is *cheaper* than TransNetV2** (37 vs 41 GMACs) — so there is no runtime/licensing reason *not* to offer it.
- **Do not make it the default** because: (a) it is not a literal drop-in for TransNetV2 weights (different model module; you must wire its checkpoint and re-tune the boundary threshold), (b) the SHOT test set is small (2,716 annotations) so the +4.2% is real but not enormous, and (c) for Laura's *primary* path the rough-cut boundaries are already known from OTIO, so blind-SBD accuracy is second-order.
- **Practical path:** add AutoShot behind the same detector interface TransNetV2 uses (decode→resize→window→per-frame logit→threshold→`out_frame_exclusive` integer-frame boundaries), select via an env flag (mirroring `LAURA_VLM_MODEL`), default off. Verify AutoShot's exact input dims against its checkpoint before wiring (evidence on 48×27/100-frame for AutoShot is **weak**).

**Do not adopt yet:** OmniShotCut / TransVLM / general transformer-VLM SBD. They claim SOTA but are research-stage, heavier (a VLM violates the no-GPU invariant for a core path), and have unconfirmed weight licenses. Revisit only if a clean, quantized, locally-runnable checkpoint with a permissive license appears.

**The bigger lever for Laura's Shorts goal is not the SBD model at all** — both TransNetV2 and AutoShot already clear ~96–97% F1 on clean cuts. The quality-determining work is **boundary placement**, which Laura already owns: visual boundaries from SBD must be **snapped to ASR word/sentence units** (Laura's `cutplace` + WhisperX word timings) so a Short never splits a word/syllable, and audio must cross-fade at sample granularity (Laura's `acrossfade` renderer). The right architecture is: **SBD proposes candidate visual cuts → cutplace constrains them to transcript-safe, sense-unit boundaries → xfade/acrossfade smooths → VLM transition-reviewer scores.** Upgrading SBD from TransNetV2 to AutoShot moves the *visual-candidate* quality a few F1 points on short-form; it does not change that the transcript-snapping layer is what makes cuts feel "human." Spend effort proportionally.

## Sources (fetched)
- TransNetV2 paper — https://ar5iv.labs.arxiv.org/html/2008.04838
- TransNetV2 repo (MIT, F1 numbers) — https://github.com/soCzech/TransNetV2
- transnetv2-pytorch (identical-output PyTorch reimpl) — https://pypi.org/project/transnetv2-pytorch/
- AutoShot paper (F1, SHOT dataset, GMACs) — https://ar5iv.labs.arxiv.org/html/2304.06116
- AutoShot repo (MIT, weights, file list) — https://github.com/wentaozhu/AutoShot
- PySceneDetect detectors (AdaptiveDetector / ContentDetector) — https://www.scenedetect.com/docs/latest/api/detectors.html
- SBD survey (classical methods, robustness) — https://link.springer.com/article/10.1007/s10462-024-10742-1
- SBD fundamentals & survey (CEUR) — https://ceur-ws.org/Vol-2589/Paper6.pdf
- Boundary detection overview (ECR, pixel diff) — https://www.sciencedirect.com/topics/computer-science/boundary-detection
- OmniShotCut (classical F1 band, transformer SBD) — https://arxiv.org/html/2604.24762v2
- TransVLM (vision-language SBD, weak evidence) — https://chence17.github.io/TransVLM/

**Weak-evidence flags:** per-dataset classical F1 (indicative band only); AutoShot's exact input resolution/window (README silent, model source not fetched); OmniShotCut/TransVLM SOTA claims (arXiv/project-page only, no independent reproduction, licenses unconfirmed); the "TransNet 0.931/0.916 on BBC" figure from the PMC depthwise-attention paper conflicts with canonical TransNetV2 numbers and was not used.

---

## 2 · Audio-basierte Schnittpunkt-Erkennung

I have comprehensive, well-cited evidence across all requested topics. Let me synthesize the research section now.

---

# Audio-Based Cut-Point Detection for Clean Editing

Research report for **Laura** (local-first, frame-accurate, AI-first editor). Goal: auto-extract high-quality social Shorts from a long AI-generated rough-cut with clean visual cuts, transcript-safe boundaries, natural audio transitions, semantically meaningful clips. All recommendations favor local-runnable, license-clean, CPU-capable options.

## 0. What Laura already has (verified against the codebase)

Before recommending, I read the relevant modules so the advice plugs into the real architecture rather than reinventing it:

- **`laura/analysis/silence.py`** — already shells out to FFmpeg `silencedetect=noise=-30dB:d=0.12` and parses stderr into **end-exclusive source-frame** silence ranges, with EOF/degenerate-range guards and graceful `[]` on any failure. This is solid and invariant-compliant (#1, #2).
- **`laura/analysis/joint.py`** — the centerpiece. One blended cut decision: `score(f) = (w_visual·visual + w_editorial·editorial)/(w_v+w_e)` over a `±window` band, with an editorial tier `SPEAKER_TURN 1.0 > SENTENCE_END 0.95 > SILENCE 0.85 > WORD_EDGE 0.70 > MID_WORD 0.0`. Ties resolve to least disruption. Backward-compatible collapse to pure visual when no context. This is exactly the right place to add new audio cues.
- **`laura/analysis/cutplace.py` / `editorial.py` / `semantic.py`** — gathers `words`, `sentence_end_frames`, `speaker_turn_frames`, `silence`; `bias_to_weights` exposes a picture-vs-sound knob.
- **ASR/align** — faster-whisper + WhisperX forced alignment (word-level timestamps, `<100 ms` accuracy per the WhisperX paper).
- **Render** — real FFmpeg `xfade`/`acrossfade` renderer + VLM transition reviewer; `render/sync.py` enforces a ±1-frame A/V drift guard (good — keep using it to catch audio repair that desyncs).

**Key gap relative to the new goal:** the only acoustic signal feeding `joint_place` is FFmpeg `silencedetect` (a single energy-floor threshold). There is **no VAD**, no detection of "this cut clips a word at the *acoustic* level" (only the ASR-word proxy), and no detection of an **audible jump/discontinuity** at a cut. Repair is limited to whole-clip `acrossfade` at the renderer; there is no targeted micro-fade / zero-crossing snap / declick.

---

## 1. Voice Activity Detection (VAD)

VAD answers "is someone speaking *right now*?" at frame granularity — strictly better than a single energy floor because it is robust to music beds, room tone, and breaths, all common in AI-generated rough-cuts.

**Recommendation: add Silero VAD as an optional `vad` extra; keep `silencedetect` as the zero-dependency fallback.**

Why Silero over the alternatives:

- **License & footprint:** MIT, ~2 MB JIT model, **no GPU required**, runs on a modern AVX CPU; a 30 ms chunk processes in `<1 ms` on one CPU thread (RTF ≈ 0.004 → ~15 s for 1 h of audio on CPU). ONNX runtime path can be 4–5× faster again. This fits Laura's "must run without GPU, heavy models are optional extras" rule perfectly. ([Silero VAD GitHub](https://github.com/snakers4/silero-vad), [PyTorch Hub](https://pytorch.org/hub/snakers4_silero-vad_vad/))
- **Accuracy, especially on noisy / music-bed audio:** on UrbanSound8K-mixed benchmarks Silero hit **87%** vs WebRTC **58%** and pyannote **62%**; at a 5% false-positive rate Silero made **4× fewer errors than WebRTC**. On clean speech it is competitive (~92%). ([Picovoice VAD comparison](https://picovoice.ai/blog/best-voice-activity-detection-vad/), [pyannote issue #604 benchmark](https://github.com/pyannote/pyannote-audio/issues/604))
- **API maps cleanly to Laura's model:** `get_speech_timestamps(wav, model, return_seconds=False)` returns sample-accurate `{start, end}` speech regions; with `return_seconds=False` you get **samples directly** (honors invariant #3 — store alignment in samples, project to frames for the UI). Tunable `threshold`, `min_silence_duration_ms`, `min_speech_duration_ms`, `speech_pad_ms`. 8 kHz / 16 kHz input. ([Silero wiki / API](https://github.com/snakers4/silero-vad/wiki/Examples-and-Dependencies))

Alternatives and when to reach for them:

- **webrtcvad** — tiny, pure-C, GMM-based, runs anywhere with no model, but markedly weaker on noise/music and only 10/20/30 ms framing. Fine as a *second* ultra-light fallback, not the primary. ([Picovoice guide](https://picovoice.ai/blog/complete-guide-voice-activity-detection-vad/))
- **pyannote VAD** — strong for *diarization* (which Laura already uses for speaker turns) but slower (97 s vs Silero's 15 s for 1 h) and a heavier dependency; not worth it purely for VAD. ([pyannote benchmark](https://github.com/pyannote/pyannote-audio/issues/604))
- **auditok** — pure energy tokenizer, numpy-only, no model. Good as the *no-extras-installed* default to replace/augment `silencedetect` without shelling out, but no smarter than the current threshold. ([auditok GitHub](https://github.com/amsehili/auditok))

**How it plugs into Laura:** VAD speech regions are the *complement* of "safe to cut" zones. Convert speech regions → source-frame ranges (samples → frames), invert to get non-speech gaps, and feed those alongside the existing `silence` list into `joint_place`. A new tier between SILENCE and WORD_EDGE — `VAD_GAP` — lets a real acoustic pause that ASR missed (un-transcribed breath, filler) outrank a bare ASR word-edge. This is purely additive and keeps the backward-compat collapse intact.

---

## 2. Silence detection, energy envelope, spectral/MFCC distance

- **Silence (energy floor):** Laura's FFmpeg `silencedetect` is the right baseline. `noise` defaults to −60 dB, `d` to 2 s; Laura's `-30 dB / 0.12 s` is well-tuned for spoken-word pauses. Per the filter docs, `mono=1` would let you detect per-channel silence (useful for stereo interview beds). ([FFmpeg silencedetect docs](https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Audio/silencedetect.html), [FFmpeg filters manual](https://ffmpeg.org/ffmpeg-filters.html)) — *evidence solid.*
- **Energy/RMS envelope:** `librosa.feature.rms` gives a continuous loudness contour; the **local RMS minimum** inside a candidate window is the lowest-energy frame to cut on, a finer target than a binary silence flag. Cheap, numpy-only. ([librosa tutorial](https://librosa.org/doc/latest/tutorial.html))
- **MFCC / spectrogram distance (audio "scene" boundaries):** Build a **self-similarity matrix (SSM)** from per-frame MFCC (+ optionally spectral-contrast / chroma) using L2 distance, then correlate a **Gaussian-weighted checkerboard kernel** down the diagonal (Foote novelty). Peaks in the resulting **novelty curve** are timbral change-points — the *audio analogue of a shot cut* (music change, room change, speaker timbre shift). librosa exposes `librosa.segment.recurrence_matrix`, MFCC, and onset/novelty utilities directly. ([librosa: Audio and Music Signal Analysis paper](https://www.researchgate.net/publication/328777063_librosa_Audio_and_Music_Signal_Analysis_in_Python), [SSM/novelty for structure analysis](https://arxiv.org/pdf/2309.02243)) — *evidence solid for the technique; weak that it's needed for speech-only Shorts (most value is on music-bed content).*

For Shorts specifically: energy-minimum and VAD-gap matter most for clean *speech* cuts; SSM-novelty matters when the rough-cut has music/ambience and you want a clip to *start on a musical phrase*. Treat SSM-novelty as an optional enrichment, not core.

---

## 3. Prosody / breath-pauses / intonation as cut cues

This is where you upgrade from "safe cut" to "natural cut." Evidence here is **academic and indirect** (no turnkey local library), so flag it as medium-confidence:

- **Prosodic boundaries** are reliably marked by (a) **pre-boundary lengthening / phrase-initial speech-rate change** and (b) silent pauses — i.e., the *combination* of a duration cue and a pause, not pause alone. ([Automatic detection of prosodic boundaries, PLOS One](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0250969), [NCBI mirror](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8092678/))
- **Breath/inhalation pauses** can be classified apart from plain silence (silent / inhalation-breath / noisy / no-pause) — a breath is the *ideal* cut point because cutting on the inhale is inaudible and feels intentional. ([Inhalation breath-pause detection for TTS, ResearchGate](https://www.researchgate.net/publication/256503016_Automatic_detection_of_inhalation_breath_pauses_for_improved_pause_modelling_in_HMM-TTS), [Breath-sound demarcation algorithm](https://www.researchgate.net/publication/3457766_An_Effective_Algorithm_for_Automatic_Detection_and_Exact_Demarcation_of_Breath_Sounds_in_Speech_and_Song_Signals))

**Practical local approximation (no new ML model):** Laura already has WhisperX word timings *and* `silencedetect`. A pause that is (i) a real silence, (ii) sits at a **sentence end** (already computed in `semantic.py`), and (iii) is preceded by a lengthened final word (compare last-word duration to its speaker's median word duration) is, with high probability, a prosodic/breath boundary. Score these higher than a mid-sentence pause. This reuses existing signals and needs no new dependency — recommended as the first prosody step. A dedicated breath-detector (band-limited energy 1–4 kHz + low ZCR + sub-speech RMS) is a possible later extra; **evidence that it's worth the complexity for Shorts is weak.**

---

## 4. Detecting hard audio jumps / discontinuities at a cut

Two distinct failure modes, each needs its own detector:

**(a) "This cut clips a word/syllable" (semantic/acoustic).** Today Laura only knows this via the ASR-word proxy (`MID_WORD` tier). WhisperX word edges are accurate to `<100 ms` but evaluated with a **200 ms collar** — i.e., a cut placed exactly on a word boundary can still shave the onset/coda of a phoneme. ([WhisperX paper](https://www.robots.ox.ac.uk/~vgg/publications/2023/Bain23/bain23.pdf), [PyPI whisperx](https://pypi.org/project/whisperx/)). The robust check: **a cut is word-safe only if both the ASR word-gap *and* VAD agree it is non-speech at that frame.** When they disagree (ASR says gap, VAD says speech), the boundary is suspect → either nudge to the nearest VAD-confirmed non-speech frame or apply a micro-fade (see §5).

**(b) "This cut creates an audible jump/click" (waveform discontinuity).** A click is literally half an impulse: when the samples on either side of a join differ in amplitude (or slope), you get a pop. Two cheap, deterministic detectors:
- **Amplitude-step:** |last sample of clip A − first sample of clip B|. A large step (relative to local RMS) predicts a click. ([Why discontinuous audio pops, KVR](https://www.kvraudio.com/forum/viewtopic.php?t=182555), [Zero Crossings And You, Wooji-Juice](https://www.wooji-juice.com/blog/zero-crossings-and-you))
- **Slope-mismatch:** even at equal amplitude, a steep-then-shallow slope across the join clicks. Compare the local derivative on both sides. ([Sound on Sound, using fades & crossfades](https://www.soundonsound.com/techniques/using-fades-crossfades))

Both are a few lines of numpy on the boundary samples. Surface the predicted-click magnitude as a new signal so placement can *avoid* it and the renderer can *repair* it. This is the concrete "audible jump" detector the prompt asks for — *evidence solid.*

---

## 5. Repair: nudge vs zero-crossing vs micro-fade vs crossfade

A decision ladder, cheapest/least-destructive first. This is the core "crossfade vs nudge" recommendation:

1. **Nudge to a clean frame (preferred, non-destructive).** If a VAD-confirmed non-speech frame or an energy-minimum exists within the `±window`, move the cut there. This is exactly what `joint_place` does; the new VAD/energy signals make the nudge target better. No audio is altered → no fade artifacts. Use when a safe spot exists nearby.
2. **Snap to the nearest zero-crossing (sub-frame).** When the cut frame is fixed (visual peak must be kept), snap the *audio* edit to the nearest zero-crossing within a few ms so the join starts/ends at amplitude 0 — eliminates most clicks at zero cost in audible content. Standard editing practice; zero-crossings occur hundreds–thousands of times/second so the snap is tiny. ([Zero Crossing and Crossfades, Springer](https://link.springer.com/chapter/10.1007/978-3-031-40067-4_20), [Wooji-Juice](https://www.wooji-juice.com/blog/zero-crossings-and-you)). Keep this **audio-only** so it never violates the integer-frame *video* invariant (#1) — it lives in the sample domain (#3).
3. **Micro-fade (1–10 ms) to avoid clipped words/clicks.** When neither nudge nor zero-crossing fully cleans it (e.g., the cut must land mid-room-tone), apply a very short fade-out/in. Forcing the edge through zero removes the impulse and softens a half-clipped word onset without audibly shortening speech. ([Wooji-Juice micro-fades](https://www.wooji-juice.com/blog/zero-crossings-and-you), [Premierely fade guide](https://premierely.io/blog/audio-fade-guide)). Implement with FFmpeg `afade` (Laura already uses the same curve family) — durations in the 5–10 ms range.
4. **Crossfade (10–80 ms) when the two sides are dissimilar.** For a real audible jump between clips (different room tone / music), overlap with `acrossfade`. **Curve choice matters:**
   - **Equal-power (constant-power)** for *incoherent* material (different sources) — only −3 dB dip at center, loudness stays steady. FFmpeg `acrossfade` curves `tri` is linear; use a sine-based power curve such as `qsin`/`hsin` for constant-power behavior. This is the right default for stitching two different clips. ([Sound on Sound: linear vs constant-power](https://www.soundonsound.com/sound-advice/q-should-use-linear-or-constant-power-crossfades), [Obsidian: gain vs equal-power](https://publish.obsidian.md/arendleejessurun/Atlas/Music+production/When+to+use+gain+versus+equal+power+crossfades))
   - **Linear / equal-gain** only when both sides are the *same continuous take* (e.g., trimming within one clip) — the razor-blade-on-tape case; equal-power would *bump* loudness there. ([Sound on Sound](https://www.soundonsound.com/techniques/using-fades-crossfades))
   - FFmpeg `acrossfade` exposes independent `curve1`/`curve2` and `duration`/`overlap`, drawing from the full `afade` curve set (`tri, qsin, hsin, esin, log, par, exp, losi, …`). ([FFmpeg filters manual](https://ffmpeg.org/ffmpeg-filters.html))

**Rule of thumb to encode:** *nudge if a clean frame exists → else zero-crossing snap → else micro-fade (clicks / half-clipped word) → else equal-power crossfade (dissimilar sides).* Reserve crossfades for genuine dissimilarity; over-crossfading speech smears consonants. Always re-run `render/sync.py`'s ±1-frame drift guard after any sample-domain repair so audio edits never desync from the integer-frame video.

**Loudness consistency for Shorts:** normalize stitched clips with **pyloudnorm** (ITU-R BS.1770-4 LUFS, BSD-licensed, pure-Python) so a multi-clip Short doesn't jump in level between cuts. ([pyloudnorm GitHub](https://github.com/csteinmetz1/pyloudnorm), [PyPI](https://pypi.org/project/pyloudnorm/)). For noisy AI-generated source, **noisereduce** (spectral gating, stationary + non-stationary, MIT) is the license-clean denoiser; **Spotify pedalboard** (GPL-3.0 — check license fit) wraps studio effects/VST if you need more. ([noisereduce GitHub](https://github.com/timsainb/noisereduce), [pedalboard GitHub](https://github.com/spotify/pedalboard)). Note: open-source *declick/declip* is thin — the commercial tools (Acon DeClick/DeClip) have no MIT equivalent; for Laura, the zero-crossing+micro-fade path covers click prevention without a declicker. *Evidence on declick OSS gap: solid (absence of options).*

---

## Feature table: signal → what it detects → library

| Signal | What it detects | Recommended library / method (local, license) | Relation to Laura |
|---|---|---|---|
| **VAD (neural)** | Speech vs non-speech, robust to music/noise | **Silero VAD** (MIT, ~2 MB, CPU, ONNX) — `get_speech_timestamps` | **ADD** as `vad` extra; new `VAD_GAP` tier in `joint.py`; samples→frames (#3) |
| VAD (signal) | Speech vs non-speech, ultra-light | webrtcvad (BSD) / auditok (MIT, numpy-only) | Optional no-model fallback for `silence.py` |
| VAD (diarization) | Speaker turns + speech | pyannote (MIT, slower, heavier) | **Keep** — already used for `speaker_turn_frames` |
| **Silence (energy floor)** | Pauses below a dB floor | **FFmpeg `silencedetect`** (LGPL) | **Keep** — already in `silence.py`; consider `mono=1` for stereo |
| Energy envelope (RMS) | Lowest-energy cut frame in a window | `librosa.feature.rms` (ISC) | **ADD** — finer target inside `joint_place` window |
| **MFCC + SSM novelty** | Timbral/audio-scene change-points | `librosa` recurrence_matrix + Foote checkerboard kernel (ISC) | **ADD (optional)** — audio analogue of shot cut; mainly for music beds |
| Word timings | Word/syllable spans (clip-a-word risk) | **WhisperX** forced align (<100 ms, BSD) | **Keep** — `MID_WORD` tier; cross-check with VAD |
| Sentence boundaries | Natural narrative breaks | Laura `semantic.sentence_end_frames` (punctuation) | **Keep** — `SENTENCE_END 0.95` tier |
| Prosody / breath / pre-boundary lengthening | Most-natural cut (inhale, phrase end) | Heuristic: silence ∧ sentence-end ∧ lengthened final word (reuse existing signals); dedicated breath-detector = later optional | **ADD (heuristic)** — new top tier; medium confidence |
| Amplitude-step / slope-mismatch | Audible **click/jump** at a join | numpy on boundary samples (no dep) | **ADD** — the "audible jump" detector |
| Zero-crossing | Sub-frame clean audio edit point | numpy sign-change search (no dep) | **ADD** — audio-only snap, preserves frame invariant |
| Micro-fade (1–10 ms) | Click / half-clipped word removal | **FFmpeg `afade`** (LGPL) | **ADD** — extends existing fade renderer |
| Crossfade (equal-power vs linear) | Smooth dissimilar-side transition | **FFmpeg `acrossfade`** `curve1/2` (`qsin`/`hsin` = power; `tri` = linear) | **Have it** — add equal-power-vs-linear *choice* logic |
| Loudness | Inter-clip level jumps | **pyloudnorm** (BSD, ITU BS.1770-4) | **ADD** — normalize stitched Shorts |
| Denoise | Hiss/hum on AI source | **noisereduce** (MIT) / pedalboard (GPL-3, check license) | **ADD (optional)** — pre-clean |

---

## Concrete recommendations for Laura (priority order)

1. **Adopt Silero VAD as the primary VAD** behind an optional `vad` extra (MIT, CPU, ~2 MB, ONNX). Produce non-speech gaps in samples, project to source frames, and feed `joint_place` a new `VAD_GAP` tier between `SILENCE` and `WORD_EDGE`. Keep `silencedetect`/auditok as the no-extras fallback. This is the single highest-leverage add for transcript-safe Shorts boundaries.
2. **Add the dual word-safety check:** a cut is clip-a-word-safe only if ASR word-gap *and* VAD agree it's non-speech (WhisperX is only ~100 ms / 200 ms-collar accurate). On disagreement, nudge or micro-fade.
3. **Add the click/jump detector** (amplitude-step + slope-mismatch on boundary samples) as a new placement penalty and a renderer trigger. This is the requested "this cut would create an audible jump" detector.
4. **Add the repair ladder:** nudge → zero-crossing snap (audio-only) → micro-fade (FFmpeg `afade`, 5–10 ms) → equal-power `acrossfade` (`qsin`/`hsin`) only for dissimilar sides; linear (`tri`) only within one take. Re-assert `render/sync.py` drift guard after any sample-domain repair.
5. **Add pyloudnorm** loudness normalization across stitched clips (Shorts feel professional only if levels match).
6. **Optional/later:** librosa RMS-minimum targeting inside the window; SSM-novelty for music-bed content; a dedicated breath detector. Flag prosody/breath as medium-confidence (academic evidence, no turnkey OSS lib).

**Evidence-quality flags:** VAD comparison, FFmpeg filter behavior, crossfade theory, zero-crossing/click physics, WhisperX accuracy, and library licenses are **well-sourced**. **Prosody/breath as a *cut cue*** is supported by speech-science papers but has **no off-the-shelf local library** — recommended as a heuristic over existing signals, not a new model. Open-source **declick/declip** is genuinely thin (commercial-only); the zero-crossing + micro-fade path is the license-clean substitute.

## Relevant files (absolute paths)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\silence.py` — FFmpeg `silencedetect` → source-frame ranges (the existing acoustic signal)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\joint.py` — blended cut scorer + editorial tier (where VAD_GAP / breath / click penalty should be added)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\cutplace.py` — gathers words/sentence/speaker/silence signals (entry point for new VAD signal)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\semantic.py` / `editorial.py` — sentence/speaker frames, word model
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\render\sync.py` — ±1-frame A/V drift guard to re-run after sample-domain audio repair

## Sources
- Silero VAD: https://github.com/snakers4/silero-vad · https://pytorch.org/hub/snakers4_silero-vad_vad/ · https://pypi.org/project/silero-vad/
- VAD comparison/benchmarks: https://picovoice.ai/blog/best-voice-activity-detection-vad/ · https://picovoice.ai/blog/complete-guide-voice-activity-detection-vad/ · https://github.com/pyannote/pyannote-audio/issues/604
- webrtcvad / auditok: https://github.com/amsehili/auditok
- FFmpeg filters (silencedetect, afade, acrossfade): https://ffmpeg.org/ffmpeg-filters.html · https://ayosec.github.io/ffmpeg-filters-docs/8.0/Filters/Audio/silencedetect.html
- Crossfade theory (equal-power vs linear): https://www.soundonsound.com/sound-advice/q-should-use-linear-or-constant-power-crossfades · https://www.soundonsound.com/techniques/using-fades-crossfades · https://publish.obsidian.md/arendleejessurun/Atlas/Music+production/When+to+use+gain+versus+equal+power+crossfades · https://premierely.io/blog/audio-fade-guide
- Zero-crossing / clicks: https://www.wooji-juice.com/blog/zero-crossings-and-you · https://link.springer.com/chapter/10.1007/978-3-031-40067-4_20 · https://www.kvraudio.com/forum/viewtopic.php?t=182555
- MFCC / SSM novelty: https://www.researchgate.net/publication/328777063_librosa_Audio_and_Music_Signal_Analysis_in_Python · https://arxiv.org/pdf/2309.02243 · https://librosa.org/doc/latest/tutorial.html
- Prosody / breath: https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0250969 · https://www.ncbi.nlm.nih.gov/pmc/articles/PMC8092678/ · https://www.researchgate.net/publication/256503016_Automatic_detection_of_inhalation_breath_pauses_for_improved_pause_modelling_in_HMM-TTS · https://www.researchgate.net/publication/3457766_An_Effective_Algorithm_for_Automatic_Detection_and_Exact_Demarcation_of_Breath_Sounds_in_Speech_and_Song_Signals
- WhisperX accuracy: https://www.robots.ox.ac.uk/~vgg/publications/2023/Bain23/bain23.pdf · https://pypi.org/project/whisperx/
- Repair libs: https://github.com/csteinmetz1/pyloudnorm · https://pypi.org/project/pyloudnorm/ · https://github.com/timsainb/noisereduce · https://github.com/spotify/pedalboard

---

## 3 · Transcript Alignment

I have everything needed. Here is the cited research section.

---

# Transcript Alignment for Cut Safety

*Research feeding Laura — relating each finding to what Laura already has (`asr.py`, `align.py`, `editorial.py`, `semantic.py`, `cutplace.py`, `silence.py`) and to the Shorts-extraction goal. Local-runnable, license-clean options preferred.*

## 1. The model landscape and where Laura sits

| Tool | Role | Timestamp granularity | Local? | License |
|---|---|---|---|---|
| **Whisper** (OpenAI) | ASR + decoder-attention timestamps | word (~±500 ms) | yes | MIT |
| **faster-whisper** (SYSTRAN, CTranslate2) | 4× faster Whisper reimpl., int8/fp16 | word (DTW from attention) | yes, CPU-capable | MIT |
| **WhisperX** (m-bain) | faster-whisper + VAD cut/merge + wav2vec2 CTC forced alignment | word (~<100 ms) | yes, CPU-capable | **BSD-2-Clause** |
| **wav2vec2 / MMS CTC** | the forced aligner *inside* WhisperX | phoneme→word | yes | MIT / CC-BY (model-dependent) |
| **Montreal Forced Aligner (MFA)** | Kaldi HMM-GMM forced aligner | phoneme + word, 10 ms frames | yes (conda/Kaldi) | MIT |
| **CrisperWhisper** (nyrahealth) | retokenized Whisper-large-v3 for verbatim, disfluency-accurate timestamps | word | yes (HF transformers) | **CC-BY-NC-4.0 (non-commercial)** |

Laura already pins the right two for a local-first commercial product: `faster-whisper>=1.0` (`asr` extra, MIT) for transcription and `whisperx>=3.1` (`align` extra, BSD-2) for the forced-alignment refinement pass. Both run on CPU; the heavy parts are optional extras (correct per the GPU-optional invariant). The recommendation below is to **keep this stack** and tighten its configuration, not replace it.

Sources: [WhisperX GitHub](https://github.com/m-bain/whisperx), [WhisperX paper (Interspeech 2023)](https://www.isca-archive.org/interspeech_2023/bain23_interspeech.pdf), [faster-whisper transcribe.py](https://github.com/SYSTRAN/faster-whisper), [CrisperWhisper model card](https://huggingface.co/nyrahealth/CrisperWhisper).

## 2. Word-level vs phoneme-level timestamps and their accuracy

**Two fundamentally different timestamp mechanisms:**

- **Whisper / faster-whisper word timestamps** are derived from cross-attention weights (dynamic time warping over the decoder attention matrix). They are *cheap* (no extra model) but *coarse and drift-prone* — commonly cited around **±0.5 s** error, and they degrade across long segments because the attention is conditioned on previously decoded text.
- **WhisperX word timestamps** come from a separate **wav2vec2 phoneme CTC forced-alignment** pass: the recognized text is force-aligned to the audio at phoneme resolution, then collapsed to word start/end. WhisperX claims word timestamps accurate to **<100 ms** (vs ~±500 ms for vanilla Whisper). This is the relevant upgrade for cut safety, and Laura already runs it in `align.py` (`whisperx.load_align_model` → `whisperx.align`).

**Phoneme/word accuracy — the hard numbers (Rousso et al., Interspeech 2024).** A careful comparison of MFA vs WhisperX vs MMS found **MFA wins at all time resolutions** for boundary accuracy. On TIMIT, MFA placed **41.6 % of boundaries within 10 ms, 72.8 % within 25 ms, 89.4 % within 50 ms**, with **median deviation 12.5 ms / mean 21.9 ms** (Buckeye mean 27.8 ms). The reason is structural: MFA's HMM-GMM operates at **10 ms frame resolution**, while WhisperX/MMS wav2vec2 stride is coarser (~20 ms) and operates over longer audio stretches. WhisperX recognized more words correctly than MMS on Buckeye (278,480 vs 259,189 of 285,347) but its *boundary precision* was still below MFA.

Sources: [Rousso et al. 2024 (Interspeech / ISCA archive)](https://www.isca-archive.org/interspeech_2024/rousso24_interspeech.pdf), [arXiv 2406.19363](https://arxiv.org/abs/2406.19363), [MFA 2026 review](https://arxiv.org/html/2606.18466), [MFA benchmarks](https://mfa-models.readthedocs.io/en/latest/benchmarks/english_alignments.html).

**Practical takeaway for Laura:** WhisperX (~tens-of-ms class) is already good enough for frame-safe cutting at 24–30 fps, where one frame is **33–42 ms**. MFA would buy ~10 ms of extra boundary precision but adds a Kaldi/conda dependency that conflicts with Laura's "starts without heavy models" invariant — **not worth it as a default**. Reserve MFA as an *optional* "high-precision align" extra only if boundary-error telemetry shows WhisperX is the bottleneck. *Evidence note:* the 10 ms-class MFA advantage is sub-frame at editorial frame rates, so for cut placement it is largely academic.

## 3. Critical WhisperX limitation that directly threatens cut safety

WhisperX's wav2vec2 aligners only model **`[a-z]`-class characters**. Words containing **digits or pronounced symbols (`%`, `&`, `@`, `§`, numerals)** cannot be aligned — wav2vec2 has no token for them. WhisperX marks those words' timestamps `NaN`/`-1` and **`interpolate_nans` fills them by linear/nearest interpolation from neighbours**. Reported real-world error for number-containing words has reached **up to 3 seconds**. For Laura this is a *latent cut-safety bug*: an interpolated word boundary is not a measured boundary, so snapping a cut to it can land mid-syllable while *looking* clean. **Mitigation (recommended): every word boundary should carry a `source: "aligned" | "interpolated"` flag, and the cut-safety rule (§6) must treat interpolated boundaries as unsafe / widen the guard band around them.** Newer Whisper alignment also drops un-dictionary words onto a wildcard column ("max non-blank score per frame"), which is better than nothing but still not a true boundary.

Sources: [whisperX #1298 (numbers)](https://github.com/m-bain/whisperX/issues/1298), [whisperX #869](https://github.com/m-bain/whisperX/issues/869), [whisperX #1247 (MFA vs WhisperX accuracy)](https://github.com/m-bain/whisperX/issues/1247), [alignment.py](https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py).

## 4. Drift, hallucination, and repetition — causes and defenses

**Causes.** Whisper hallucinates (loops a phrase, invents text, drifts timestamps) primarily over **silence / non-speech / low-confidence audio**: the encoder embedding is near-zero and the autoregressive decoder "fills in" plausible text. `condition_on_previous_text=True` (the default) feeds the previous segment's text as a prompt, which *propagates* a hallucination forward and is a major source of repetition loops on long-form audio. A mechanistic study (Calm-Whisper) localized non-speech hallucination to **3 of 20 decoder attention heads accounting for >75 % of hallucinations** (heads #1/#6/#11), and fine-tuning them cut the non-speech hallucination rate from **99.97 % → 15.51 %** with WER essentially unchanged (2.19 % vs 2.12 % on LibriSpeech test-clean).

**Defenses, in order of impact:**

1. **VAD gating (highest leverage, already partly in Laura).** WhisperX's VAD "cut & merge" pre-segments audio so the ASR never decodes over long silences; the WhisperX paper shows this **reduces hallucination and repetition with no WER degradation**, and it is *on by default*. faster-whisper's own `vad_filter` (Silero) is `False` in the plain `transcribe()` but `True` in `BatchedInferencePipeline`. *Caveat:* VAD is not a silver bullet — Silero v5 was only ~61 % utterance-accurate on ESC-50 noise (up to 40 % of pure noise misread as speech), so VAD-gating + decoding thresholds must be combined, not relied on alone.
2. **`condition_on_previous_text=False`** — stops hallucination/repetition propagation across segments; widely recommended for batch/long-form. WhisperX already sets `condition_on_prev_text=False` by default.
3. **`compression_ratio_threshold=2.4`** — flags a segment whose decoded text is suspiciously repetitive (high gzip compression ratio) and triggers temperature fallback / drop.
4. **`log_prob_threshold=-1.0`** + **`no_speech_threshold=0.6`** — drop/treat-as-silence low-confidence and no-speech segments.
5. **`hallucination_silence_threshold`** (faster-whisper, default `None`) — when set with word timestamps on, skips silent periods longer than the threshold to avoid hallucinating during gaps.
6. **Per-word `probability`** — faster-whisper already returns `w.probability` (Laura stores it as `WordResult.confidence`); use it to flag low-confidence words near a planned cut.

Sources: [openai/whisper #679 (hallucination fixes)](https://github.com/openai/whisper/discussions/679), [Whisper silence-loop writeup](https://dev.to/nareshipme/whisper-hallucination-on-silence-why-your-transcript-loops-the-same-phrase-2pg4), [Calm-Whisper, arXiv 2505.12969](https://arxiv.org/html/2505.12969v1), [WhisperX paper](https://www.isca-archive.org/interspeech_2023/bain23_interspeech.pdf), [faster-whisper transcribe options](https://whisper-api.com/docs/transcription-options/), [faster-whisper source](https://github.com/SYSTRAN/faster-whisper).

## 5. Concrete faster-whisper + WhisperX config for Laura (minimize drift/hallucination)

Defaults below are verified against faster-whisper `transcribe.py` / `vad.py` source. Laura's `asr.py:_run` currently passes only `word_timestamps=True, language=...` — i.e. it inherits all the risky defaults (`condition_on_previous_text=True`, `vad_filter=False`). Change to:

```python
segments, info = model.transcribe(
    str(audio_path),
    language=language,
    word_timestamps=True,
    condition_on_previous_text=False,      # default True → stops repetition/drift propagation
    vad_filter=True,                       # default False → gate out silence before decode
    vad_parameters=dict(
        min_silence_duration_ms=500,       # default 2000 is too coarse for Shorts pacing
        speech_pad_ms=200,                 # default 400; pad so word onsets aren't clipped
        threshold=0.5,                     # Silero default
    ),
    no_speech_threshold=0.6,               # default; treat ≥0.6 no-speech prob as silence
    log_prob_threshold=-1.0,               # default; drop low-confidence segments
    compression_ratio_threshold=2.4,       # default; catch repetition loops
    hallucination_silence_threshold=2.0,   # default None → set: skip >2s silent gaps
    temperature=[0.0, 0.2, 0.4, 0.6, 0.8, 1.0],  # default fallback ladder; keep
    beam_size=5,                           # default
)
```

Then keep the **WhisperX forced-alignment pass** (`align.py`) as the authoritative source of word boundaries — Whisper's attention timestamps are only the fallback when the `align` extra is absent. WhisperX VAD is already on by default in its pipeline; if Laura calls WhisperX's own `transcribe` path rather than feeding faster-whisper segments in, the VAD cut/merge happens there too.

Verified defaults: `condition_on_previous_text=True`, `no_speech_threshold=0.6`, `log_prob_threshold=-1.0`, `compression_ratio_threshold=2.4`, `vad_filter=False` (plain) / `True` (batched), `hallucination_silence_threshold=None`, `word_timestamps=False`, `beam_size=5`; `VadOptions(threshold=0.5, min_speech_duration_ms=0, max_speech_duration_s=inf, min_silence_duration_ms=2000, speech_pad_ms=400)`.
Sources: [faster-whisper transcribe.py / vad.py](https://github.com/SYSTRAN/faster-whisper), [transcription-options docs](https://whisper-api.com/docs/transcription-options/).

## 6. Is cutting at frame *f* transcript-safe? — a concrete rule with a guard band

Laura already has the right primitives (`editorial.Word` half-open `[start_frame, end_frame)`, `_covering_word`, `_gap_frames`, plus `silence.detect_silence`, `semantic.sentence_end_frames`/`speaker_turn_frames`, and `joint.joint_place` scoring). What's missing is an explicit **guard band** and a **boundary-quality gate** so a cut never lands a hair inside a word/syllable due to alignment error. Concrete rule:

**Definitions.** Let `frame_ms = 1000 · rate_den / rate_num`. Choose a guard band `G_ms` from the *measured* alignment precision class:
- WhisperX-aligned boundary: `G_ms ≈ 50–60 ms` (covers WhisperX's ~<100 ms / 20 ms-stride error with margin).
- Whisper-attention-only boundary (no `align` extra): `G_ms ≈ 150–200 ms` (covers ~±0.5 s tail-risk loosely; flag as low-trust).
- **Interpolated / wildcard boundary** (§3 — number/symbol word, `source != "aligned"`): treat the *whole word* as unsafe; `G_ms` = full word span.

Convert to frames: `G = ceil(G_ms · rate_num / (1000 · rate_den))` (a few frames at 30 fps with `G_ms=50` ⇒ `G≈2`).

**Safety classification of a candidate cut at frame `f`:**

1. **In real audio silence** → `safe` (best). `f` lies inside any `detect_silence` interval `[s,e)` with `f-s ≥ G` and `e-f ≥ G`. This is the editor's ideal cut and beats everything.
2. **On a sentence end / speaker turn** → `safe` (preferred for Shorts: cut on a *sense unit*, not just any gap). `f ∈ sentence_end_frames ∪ speaker_turn_frames` **and** the surrounding inter-word gap is ≥ `2G`.
3. **In a word gap with margin** → `safe`. There exist consecutive words with `prev.end_frame + G ≤ f ≤ next.start_frame - G` (i.e. `f` is at least `G` away from both word boundaries — *not* merely between them).
4. **Within `G` of a word boundary but inside a word** → `unsafe (would clip onset/coda)`. Snap to the boundary only if doing so opens a ≥`G` gap; otherwise reject.
5. **Strictly inside a word, >`G` from both edges** → `unsafe (splits a word/syllable)`. Never cut here. (`_covering_word(f) is not None` and `min(f-start, end-f) ≥ G`.)
6. **Inside an interpolated/low-confidence word** → `unsafe` regardless of position.

**Decision procedure** (drop-in extension of `align_cut` / `joint_place`): given the desired visual cut `f₀`, search `[f₀-window, f₀+window]` (Laura's `AUTO_EDITORIAL_WINDOW=12`) for the highest-ranked candidate by the order silence > sentence/speaker seam > word-gap-with-margin > word-edge-with-G-clearance; reject any frame failing the `G` margin or sitting in an interpolated word. If nothing in the window is `safe`, leave the cut at `f₀` and **mark the clip boundary `transcript_unsafe=true`** for UI review rather than silently splitting a word. This preserves Laura's invariants: integer frames (#1), end-exclusive ranges (#2), DF/NDF as display-only (#4), and pure-refinement degradation when no transcript exists.

**Why the margin matters:** without `G`, the existing `_gap_frames` logic treats *any* frame in `[prev.end, next.start]` as clean — but if the true word end is `G_err` frames later than the aligned `end_frame`, a cut on `prev.end_frame` clips the word's coda. The `G` band is exactly the alignment-error budget, sized from the model's measured precision (§2).

## 7. Summary — keep / upgrade / add for Laura

**Keep:** faster-whisper (MIT, CPU-capable) for ASR; WhisperX (BSD-2) for forced alignment as the authoritative word boundaries; `silence.detect_silence`, `semantic` seams, `joint.joint_place` scoring, integer-frame/end-exclusive invariants. The architecture is already correct.

**Upgrade (config only, no new deps):** in `asr.py:_run` set `condition_on_previous_text=False`, `vad_filter=True` with tuned `vad_parameters`, and `hallucination_silence_threshold` (§5). This is the single highest-value, lowest-risk change and directly attacks drift/hallucination/repetition.

**Add (small code, no heavy deps):**
1. A **guard band `G`** and the §6 safety classifier wrapped around `align_cut`/`joint_place`; expose `transcript_unsafe` on clip boundaries for Shorts review.
2. A per-word **`boundary_source` flag** (`aligned`/`interpolated`/`attention`) so interpolated number/symbol words (§3) are treated as unsafe — closes a real latent cut-safety bug.
3. Use the already-stored per-word **`confidence`** (faster-whisper `probability`) to widen `G` around low-confidence words near a cut.

**Optional / defer (license- or dependency-cost):**
- **MFA** as a high-precision `align` extra (MIT) — only ~10 ms sub-frame gain, heavy Kaldi/conda dep; gate behind telemetry.
- **CrisperWhisper** — best verbatim/disfluency-accurate timestamps (handles fillers/pauses Shorts editors care about) but the published weights are **CC-BY-NC-4.0 (non-commercial)** — *not license-clean for a commercial product*; usable only for research/eval, not a shippable default. Evidence on its exact F-score is from secondary summaries (50 ms collar, beats Whisper-large-v3 on AMI/TED-LIUM); I could not extract the primary table (PDF returned binary), so treat the magnitude as directional.

**Weak-evidence flags:** (a) the WhisperX "<100 ms" and vanilla-Whisper "±500 ms" figures come from the README/secondary summaries, not a re-measured benchmark; the *independent* Rousso et al. boundary numbers (MFA 72.8 % within 25 ms) are the more trustworthy precision anchor. (b) CrisperWhisper accuracy figures are secondary-source only. (c) The exact `G_ms` values in §6 are engineering recommendations derived from the cited error classes, not a benchmarked Laura measurement — validate against Laura's own `cut_bench`/`eval_cut` once instrumented.

---

**Files reviewed (absolute paths):**
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\asr.py` — faster-whisper call; currently passes no anti-hallucination params (the §5 fix lands here).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\align.py` — WhisperX forced-alignment pass (keep; add `boundary_source` flag).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\editorial.py` — `Word`, `_covering_word`, `_gap_frames`, `align_cut` (extend with guard band `G`, §6).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\semantic.py` — `sentence_end_frames`, `speaker_turn_frames` (sense-unit seams for §6 rule 2).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\silence.py` — `detect_silence` (real-silence intervals, §6 rule 1).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\cutplace.py` — `joint_place` orchestration (wrap the safety gate here).
- `C:\Users\User\Desktop\Laura\services\local-api\pyproject.toml` — extras: `asr=faster-whisper>=1.0`, `align=whisperx>=3.1`.

**Sources:** [WhisperX GitHub](https://github.com/m-bain/whisperx) · [WhisperX paper (Interspeech 2023)](https://www.isca-archive.org/interspeech_2023/bain23_interspeech.pdf) · [WhisperX arXiv 2303.00747](https://arxiv.org/abs/2303.00747) · [Rousso et al. 2024 (ISCA)](https://www.isca-archive.org/interspeech_2024/rousso24_interspeech.pdf) · [arXiv 2406.19363](https://arxiv.org/abs/2406.19363) · [MFA review 2026](https://arxiv.org/html/2606.18466) · [MFA benchmarks](https://mfa-models.readthedocs.io/en/latest/benchmarks/english_alignments.html) · [faster-whisper (SYSTRAN)](https://github.com/SYSTRAN/faster-whisper) · [faster-whisper options](https://whisper-api.com/docs/transcription-options/) · [Calm-Whisper arXiv 2505.12969](https://arxiv.org/html/2505.12969v1) · [Whisper hallucination #679](https://github.com/openai/whisper/discussions/679) · [Whisper silence-loop](https://dev.to/nareshipme/whisper-hallucination-on-silence-why-your-transcript-loops-the-same-phrase-2pg4) · [whisperX #1298](https://github.com/m-bain/whisperX/issues/1298) · [whisperX #869](https://github.com/m-bain/whisperX/issues/869) · [whisperX #1247](https://github.com/m-bain/whisperX/issues/1247) · [whisperX alignment.py](https://github.com/m-bain/whisperX/blob/main/whisperx/alignment.py) · [CrisperWhisper arXiv 2408.16589](https://arxiv.org/abs/2408.16589) · [CrisperWhisper model card (CC-BY-NC-4.0)](https://huggingface.co/nyrahealth/CrisperWhisper)

---

## 4 · Semantische Segmentierung & Highlight/Hook

I have comprehensive coverage across all six sub-topics with primary sources fetched. I'll synthesize now. The audio-visual ICCV PDF 403'd but the search summary already gave me the key claim (audio-visual joint > visual-only), and I have the Mr.HiSum/QVHighlights/Lighthouse ecosystem covered for the highlight angle.

Here is the cited research section.

---

# Semantic Segmentation + Highlight/Hook Detection for Shorts — Research & Laura Integration

## 0. TL;DR recommendation

Build the Shorts extractor as a **transcript-first cascade** that reuses Laura's existing word-level ASR, `semantic.py` seams, and `cutplace` boundary engine, and adds three new local stages:

1. **Sentence/sense-unit boundaries** — keep Whisper/WhisperX punctuation as the cheap default; add **`wtpsplit` SaT** (MIT, CPU-runnable) as an optional `[segment]` extra for robust, punctuation-agnostic sense-unit splitting when ASR punctuation is unreliable.
2. **Topic-shift segmentation into candidate clips** — **embedding-based TextTiling** over sentence embeddings (cosine valley / depth-score detection). Use **`bge-m3`** or **`paraphrase-multilingual-MiniLM-L12-v2`** via `sentence-transformers`, CPU-OK, as an optional `[embed]` extra. This is the new "scenes-for-meaning" layer; it pairs with TransNetV2's "scenes-for-picture."
3. **Candidate scoring** — a **multimodal score** = `LLM standalone+hook rubric (text)` + `audio-energy/prosody peaks (signal)` + optional `VLM visual-hook check`, reusing the exact `transition_review` backend pattern (Protocol + Ollama + Stub + cache-by-signature + idempotency).

Everything degrades to a heuristic when models are absent — matching Laura's "must run without GPU/models" invariant.

---

## 1. Sentence boundary / sense-unit detection (never split a word or thought)

**What Laura already has.** `analysis/semantic.py::sentence_end_frames` derives sentence ends from word text ending in `.?!…`, with a long-pause fallback (`DEFAULT_CLAUSE_GAP_FRAMES = 15`). `cutplace`/`editorial.py` already snap cuts to word gaps > word edges and prefer sentence ends/speaker turns. This is solid and frame-exact — keep it.

**The gap.** ASR punctuation is the weak link: faster-whisper/WhisperX punctuation is inconsistent, and Laura's fallback is a fixed pause threshold. For Shorts, mis-detecting a sense-unit boundary means a clip that starts mid-thought (kills the hook) or ends on a dangling clause.

**Upgrade — `wtpsplit` / SaT ("Segment any Text").** State-of-the-art, **punctuation-agnostic**, multilingual (85+ languages), and explicitly designed for *poorly formatted* text (i.e., raw ASR) — it beats strong LLM baselines across 8 corpora ([EMNLP 2024 paper](https://aclanthology.org/2024.emnlp-main.665/); [arXiv 2406.16678](https://arxiv.org/html/2406.16678v2)). MIT-licensed, `pip install wtpsplit`; ships **3-layer (speed)** and **12-layer (quality)** SaT models. A trimmed CPU-only inference variant exists ([`wtpsplit-lite`](https://github.com/superlinear-ai/wtpsplit-lite)). The repo: [github.com/segment-any-text/wtpsplit](https://github.com/segment-any-text/wtpsplit).
*Integration:* run SaT on the joined word text, then map each sentence span back to word indices → source frames (Laura already stores word `start_frame`/`end_frame`). Replace/augment the punctuation+pause heuristic in `sentence_end_frames`. German support matters here (Laura is bilingual) and SaT handles it natively.

**Acoustic SBD as a complement.** Classic work shows sentence-boundary detection is strongest with **parallel lexical + acoustic (prosodic) models** ([Sentence Boundary Detection Based on Parallel Lexical and Acoustic Models](https://www.researchgate.net/publication/307889206_Sentence_Boundary_Detection_Based_on_Parallel_Lexical_and_Acoustic_Models); [Streaming Punctuation, arXiv 2301.03819](https://arxiv.org/pdf/2301.03819)). Laura already has the prosodic half — `analysis/silence.py` (`silencedetect`). So the locally-optimal recipe is: **SaT lexical boundary ∧ a nearby real silence** → highest-confidence sense-unit cut. This is a small extension to `cutplace`'s existing "prefer silence > sentence end > word edge" ranking, not a new subsystem.

---

## 2. Topic-shift detection (segment the rough-cut into candidate clips)

This is the genuinely new layer: TransNetV2 finds *shot* boundaries (picture changes); for Shorts you need *topic/idea* boundaries (a self-contained point starts/ends), which often span many shots or none.

**TextTiling (the classic baseline).** Sliding window over the transcript, cosine "lexical cohesion" between adjacent blocks, place boundaries at **valleys (local minima) of the similarity curve** via a **depth score** ([AssemblyAI: Text Segmentation](https://www.assemblyai.com/blog/text-segmentation-approaches-datasets-and-evaluation-metrics); [ML Journey](https://mljourney.com/text-segmentation-in-machine-learning/)). Evaluated with **Pk** and **WindowDiff**.

**Embedding-based TextTiling (the recommended approach).** Replace bag-of-words cohesion with **sentence-embedding cosine similarity**; BERT/SBERT embeddings substantially outperform lexical TextTiling, word2vec, and bag-of-words ([Solbiati et al., *Unsupervised Topic Segmentation of Meetings with BERT Embeddings*, arXiv 2106.12978](https://arxiv.org/pdf/2106.12978)). Concrete, copyable parameters from that paper: **window = 6 sentences**, cosine similarity between the block before and after each candidate boundary, **depth-score valley detection** with a threshold, then **split long / merge short** segments. Meeting transcripts (conversational, unstructured) are the closest analog to an AI rough-cut's narration. A ready local implementation with tunable `window k / pooling (mean|max|min) / threshold`, supporting `all-MiniLM-L6-v2`, `all-mpnet-base-v2` (and OpenAI, which we'd skip): [`saeedabc/llm-text-tiling`](https://github.com/saeedabc/llm-text-tiling) (MIT). Newer SBERT-based segmenters confirm the recipe — sequence of similarities = weighted average of cosine sims with the previous N sentences, boundaries at troughs ([A More Effective Sentence-Wise Text Segmentation Using BERT](https://link.springer.com/chapter/10.1007/978-3-030-86337-1_16)).

**LLM-based segmentation** is also viable and improving ([Topic Segmentation Using Generative Language Models, arXiv 2601.03276](https://arxiv.org/html/2601.03276v1)), but for a local-first tool the embedding+valley method is cheaper, deterministic, and GPU-optional. Reserve the LLM for *scoring* (§5), not bulk segmentation.

**Recommendation for Laura.** Add `analysis/topics.py`: embed each sentence (from §1) once, compute the windowed cosine-similarity curve, detect depth-score valleys → topic boundaries in source frames. Snap each boundary to the nearest §1 sense-unit boundary (so a topic cut never lands mid-word). Output: candidate **segments** = maximal runs between topic valleys, optionally sub-windowed to the 15–60 s Shorts target. This is the semantic dual of `scenes/grouping.py`.

---

## 3. Embedding model choice (local, license-clean, CPU-OK)

| Model | Why | Notes |
|---|---|---|
| **`BAAI/bge-m3`** | Best self-hosted quality/cost balance for multilingual, up to 8192 tokens, dense+sparse+multi-vector ([BentoML guide](https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models); [MS Tech Community](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/what%E2%80%99s-trending-on-hugging-face-pubmedbert-base-embeddings-paraphrase-multilingu/4496185)) | MIT; ~560M params, runs on CPU but slower; best default when a GPU is present |
| **`paraphrase-multilingual-MiniLM-L12-v2`** | Community-proven for clustering/cross-lingual; fast on CPU ([Nixiesearch SBERT](https://www.nixiesearch.ai/features/inference/embeddings/sbert/)) | Apache-2.0; the right "no-GPU default" — small enough for CPU-only Laura installs |
| `gte-multilingual-base` | Highest scores on small datasets among <1B models ([MultiClaimNet, arXiv 2503.22280](https://arxiv.org/pdf/2503.22280)) | Strong alternative; Apache-2.0 |

**Pick:** `paraphrase-multilingual-MiniLM-L12-v2` as the CPU default (matches the bilingual EN/DE requirement and the "must run without GPU" invariant), `bge-m3` as the opt-in `[embed-hq]` upgrade. ONNX export runs multi-core on CPU ([ailog RAG guide](https://app.ailog.fr/en/blog/guides/choosing-embedding-models)). One embedding pass serves *both* §2 segmentation and §5 semantic-coherence scoring — compute once, cache by `(asset, pipeline_version)` per Laura's idempotency invariant #7.

---

## 4. Hook detection (first 1–3 s) and highlight detection

### 4a. Hook — what the evidence says
The 3-second window is the dominant retention lever: ~**65% who watch 3 s watch ≥10 s; ~45% watch ≥30 s** ([Animoto](https://animoto.com/blog/video-marketing/why-first-3-seconds-matter); [OpusClip hook formulas](https://www.opus.pro/blog/youtube-shorts-hook-formulas)). The psychological levers are well-characterized and **operationalizable from the transcript + audio**: **curiosity gap** (open question / violated expectation), **pattern interrupt**, **unresolved tension**, **social proof**, **promise of transformation** ([Brandefy](https://brandefy.com/psychology-of-viral-video-openers/); [virvid.ai](https://virvid.ai/blog/first-3-seconds-hook-faceless-shorts-2026)). Educational content → direct-promise/question hooks; entertainment → pattern interrupts.

This evidence is **marketing-blog grade, not peer-reviewed** — directionally reliable (consistent across many independent sources) but treat exact percentages as soft. Use it to design the *rubric*, not as ground truth.

*Laura implication:* the hook is judged primarily on the **first sentence(s)** of a candidate clip (text) plus the **opening audio energy** — both already available. No new capture is needed.

### 4b. Highlight detection — datasets & models
- **Datasets:** **Mr.HiSum** (large-scale, YouTube-8M-derived, NeurIPS 2023 [PDF](https://proceedings.neurips.cc/paper_files/paper/2023/file/7f880e3a325b06e3601af1384a653038-Paper-Datasets_and_Benchmarks.pdf)); **QVHighlights** (10,148 segments, ground-truth for both moment-retrieval and highlight detection, [Moment-DETR/QVHighlights, NeurIPS 2021](https://github.com/jayleicn/moment_detr)).
- **Models / library:** **Lighthouse** ([github.com/line/lighthouse](https://github.com/line/lighthouse), **Apache-2.0, CPU inference supported**) wraps Moment-DETR, QD-DETR, CG-DETR, TR-DETR, UVCOM, R2-Tuning, with CLIP / CLIP+Slowfast / **CLIP+Slowfast+PANNs (audio)** feature extractors, and supports **audio moment retrieval**. This is the one license-clean, `pip`-installable, locally-runnable highlight stack — but it's heavyweight (PyTorch + pretrained checkpoints, slow on CPU).
- **Audio + sentiment peaks (lightweight, recommended for Laura):** Audio-visual joint models beat visual-only highlight detection ([Badamdorj et al., *Joint Visual and Audio Learning for Video Highlight Detection*, ICCV 2021](https://openaccess.thecvf.com/content/ICCV2021/papers/Badamdorj_Joint_Visual_and_Audio_Learning_for_Video_Highlight_Detection_ICCV_2021_paper.pdf) — note: the PDF blocked direct fetch; claim is from the search abstract, treat as solid-but-secondhand). The data-driven VLM virality study quantifies which signals matter: **audio-energy dynamics ≈12.4% importance, frame variance/motion ≈10.7%, text-from-speech contrast ≈9.1%, scene-change frequency ≈7.6%** — and reaches **Spearman ρ=0.71 with engagement** ([arXiv 2512.21402](https://arxiv.org/html/2512.21402)). Practical signal extraction: run **`scipy.signal.find_peaks`** on an intensity/energy score sequence to nominate highlight moments — explicitly the technique used by recent audio-driven highlight work ([Automated Detection of Sport Highlights from Audio and Video, arXiv 2501.16100](https://arxiv.org/pdf/2501.16100)).

*Laura implication:* you do **not** need to ship Lighthouse to get most of the value. Audio-energy/RMS peaks (from the proxy audio Laura already extracts) + sentiment/emphasis peaks (from the transcript via a small classifier or the LLM scorer) reproduce ~the top-weighted signals cheaply and on CPU. Offer Lighthouse only as a heavy optional `[highlight-ml]` extra for users with a GPU.

---

## 5. LLM clip scoring — "does this 15–60 s segment stand alone as a Short?"

### 5a. Prior art (what to copy)
- **OpusClip's LLM-as-judge** ([engineering writeup](https://medium.com/opus-engineering/a-scalable-llm-as-a-judge-framework-for-video-quality-evaluation-74612034bd1e)): four dimensions — **Hook** ("Does the clip immediately make sense? Engaging enough to stay?"), **Content** ("clear, complete, logically delivered?"), **Visual**, **Audio** — each on a **0-1-2 scale** with explicit per-level definitions and +/- examples. Key finding: **simpler rubrics generalize better; too many score levels destabilize both human and LLM judgments.** **75.2% agreement** with humans on 250 clips; export rate rose 13%→35% from low to high scores (real predictive value).
- **SamurAI AI-YouTube-Shorts-Generator** ([GitHub, MIT](https://github.com/SamurAIGPT/AI-Youtube-Shorts-Generator)): LLM scans the transcript against a **virality framework** — *hooks, emotional peaks, opinion bombs, revelation moments, conflict, quotable lines, story peaks, practical value* — ranks candidates **0–100**, uses **20-min overlapping chunks** (1200 s / 60 s overlap) so clips spanning chunk edges aren't lost. Concrete, license-clean blueprint.
- **VLM-as-rubric-evaluator** ([arXiv 2512.21402](https://arxiv.org/html/2512.21402)): a VLM with a custom virality/persona rubric aligns with human-centered judgments — validates using Laura's **own Ollama VLM** for the *visual* hook check.

### 5b. Recommended design for Laura — reuse `transition_review` wholesale
Clone the proven pattern in `analysis/transition_review.py` + `vlm_ollama.py`:
- **`ClipScorerBackend` Protocol** (mirrors `VlmBackend`): `available()/model_id()/model_digest()/score(segment_features) -> ClipVerdict`.
- **`StubClipScorer`** — deterministic heuristic (audio-peak presence + sentence-completeness + length-in-band), so the pipeline runs with **no model** and tests stay model-free (Laura's invariant).
- **`OllamaClipScorer`** — text LLM via the same local Ollama (`temperature=0, top_k=1, seed=0` for determinism, JSON-in-prompt + defensive parse → safe default verdict, exactly as `vlm_ollama.py` does). Use a small local text model (e.g. `qwen3:8b` / `llama3.1:8b`) for the transcript rubric; reuse the existing `qwen3-vl:8b` only for the optional *visual* hook check.
- **Cache by semantic signature + idempotency:** key the verdict on `(segment source-frame span, transcript hash, model_digest)` — same `boundary_signature` discipline — so re-scoring after an unrelated edit is a cache hit.

### 5c. The rubric (text LLM, 0-1-2 per dimension — deliberately coarse per OpusClip's finding)
Score each candidate segment on:
1. **`standalone_coherence`** — Can a cold viewer follow this with zero prior context? (0 = needs prior setup / starts mid-thought; 1 = mostly self-contained; 2 = complete idea with its own setup + payoff.)
2. **`hook_strength`** — Does the **first sentence** create a curiosity gap / pose a question / pattern-interrupt / make a bold claim? (0 = bland/throat-clearing; 1 = mild interest; 2 = strong open loop.) *Judged on the opening 1–2 sentences only.*
3. **`payoff`** — Does it deliver a revelation / quotable line / emotional or story peak / practical value before it ends? (0/1/2)
4. **`completeness`** — Does it start at a sense-unit boundary and end on a resolved thought (no dangling clause)? (0/1/2) — cross-checked against §1/§2 boundaries.

Plus a non-LLM **`audio_emphasis`** term from §4b (RMS-energy peak / prosodic emphasis via `find_peaks`), and an **optional VLM `visual_hook`** (0–1, "is the opening frame visually arresting / does it show a face/motion?") via the existing Ollama VLM.

**Final score** = weighted sum; clips outside the 15–60 s band are penalized, clips whose boundaries don't align to §1 sense-units are penalized hard (transcript-safety is non-negotiable for Laura). Surface top-N as candidate Shorts; the user picks, then Laura's existing `cutplace` + xfade/acrossfade renderer + OTIO export produce the frame-accurate, transcript-safe, audio-smooth output.

### 5d. Prompt skeleton (drop-in for `OllamaClipScorer`)
```
You are a short-form video editor selecting clips for TikTok/Reels/YouTube Shorts.
Below is the transcript of ONE candidate clip (with its first sentence marked [HOOK]).
A great Short: stands alone with zero prior context, opens with a curiosity gap or
bold claim in the first 1-2 sentences, delivers a payoff (revelation, quotable line,
emotional/story peak, or practical value), and ends on a resolved thought.
Reply with ONE JSON object and nothing else:
{"standalone_coherence":0|1|2, "hook_strength":0|1|2, "payoff":0|1|2,
 "completeness":0|1|2, "best_title":"<=8 words", "reason":"<=15 words"}
Use 2 only when clearly excellent; default to 1 when unsure; 0 for clearly poor.
```
(Coarse 0-1-2, JSON-only, deterministic decoding, defensive coercion to a safe `1`-default verdict — directly mirroring `vlm_ollama._parse_verdict`.)

---

## 6. What to keep / add / upgrade in Laura

| Capability | Status | Action |
|---|---|---|
| Word-level ASR (faster-whisper/WhisperX) | Have | Keep — feeds everything |
| Shot detection (TransNetV2 + PySceneDetect) | Have | Keep — "scenes for picture" |
| Sentence ends / speaker turns (`semantic.py`) | Have (punctuation+pause heuristic) | **Upgrade** with `wtpsplit` SaT (`[segment]` extra, MIT, CPU) |
| Cut placement (`cutplace`/`editorial.py`/`joint.py`) | Have (silence>sentence>word) | Keep — extend ranking with SaT∧silence agreement |
| Silence detection (`silence.py`) | Have | Reuse for acoustic SBD + as a highlight/emphasis signal |
| **Topic-shift segmentation** | **Missing** | **Add** `analysis/topics.py` — embedding TextTiling, valley detection (window=6, depth-score) |
| **Sentence embeddings** | **Missing** | **Add** `[embed]` extra: `paraphrase-multilingual-MiniLM-L12-v2` (CPU default), `bge-m3` (HQ opt-in) |
| **Audio-energy / emphasis peaks** | **Partial** (have audio + silence) | **Add** `scipy.signal.find_peaks` over RMS — lightweight highlight signal |
| **Clip scoring (hook + standalone)** | **Missing** | **Add** `ClipScorerBackend` (Protocol+Stub+Ollama), clone `transition_review` pattern |
| ML highlight models (Lighthouse/Moment-DETR) | n/a | **Optional heavy extra** `[highlight-ml]` only (Apache-2.0, GPU-preferred) |
| VLM reviewer (Ollama qwen3-vl) | Have | Reuse for optional **visual-hook** sub-score |
| xfade/acrossfade renderer + OTIO export | Have | Keep — final transcript-safe, audio-smooth Short |

**Architecture fit:** the Shorts extractor is a new job type (`shorts.extract`) producing candidate segments scored as above, then materialized via the existing rough-cut → scene → Feinschnitt → export path. No new invariants are violated: boundaries stay integer source frames (#1), end-exclusive (#2), OTIO source-of-truth (#6), idempotent by `(input, pipeline_version)` (#7).

---

## Evidence-strength flags
- **Strong / peer-reviewed:** embedding TextTiling (Solbiati arXiv 2106.12978), SaT segmentation (EMNLP 2024), highlight datasets/models (Mr.HiSum NeurIPS 2023, QVHighlights NeurIPS 2021), embedding-model rankings (multiple 2025–26 evaluations).
- **Solid but secondhand:** ICCV 2021 audio-visual highlight claim (PDF 403'd; relied on search abstract). The VLM virality feature-importance numbers (arXiv 2512.21402) correlate to likes/views, **not** controlled human judgment — the authors say so.
- **Directional only (marketing blogs):** the 3-second retention percentages and hook taxonomies (OpusClip, Animoto, Brandefy, virvid). Consistent across sources, but use to shape the rubric, not as ground truth.
- **License-verified local options:** `wtpsplit` (MIT), `llm-text-tiling` (MIT), SamurAI generator (MIT), Lighthouse (Apache-2.0), MiniLM (Apache-2.0), bge-m3 (MIT). All `pip`-installable and CPU-runnable (Lighthouse feature extraction is GPU-preferred).

### Key sources
- Solbiati et al., Unsupervised Topic Segmentation with BERT — https://arxiv.org/pdf/2106.12978
- Segment Any Text (SaT) — https://aclanthology.org/2024.emnlp-main.665/ · https://github.com/segment-any-text/wtpsplit
- llm-text-tiling — https://github.com/saeedabc/llm-text-tiling
- OpusClip LLM-as-judge — https://medium.com/opus-engineering/a-scalable-llm-as-a-judge-framework-for-video-quality-evaluation-74612034bd1e
- SamurAI AI-YouTube-Shorts-Generator — https://github.com/SamurAIGPT/AI-Youtube-Shorts-Generator
- VLM virality framework — https://arxiv.org/html/2512.21402
- Lighthouse (MR/HD library) — https://github.com/line/lighthouse · Moment-DETR/QVHighlights — https://github.com/jayleicn/moment_detr
- Mr.HiSum — https://proceedings.neurips.cc/paper_files/paper/2023/file/7f880e3a325b06e3601af1384a653038-Paper-Datasets_and_Benchmarks.pdf
- Joint Visual+Audio Highlight Detection (ICCV 2021) — https://openaccess.thecvf.com/content/ICCV2021/papers/Badamdorj_Joint_Visual_and_Audio_Learning_for_Video_Highlight_Detection_ICCV_2021_paper.pdf
- Audio/video sport highlights (find_peaks) — https://arxiv.org/pdf/2501.16100
- Embedding model guides — https://www.bentoml.com/blog/a-guide-to-open-source-embedding-models · https://www.nixiesearch.ai/features/inference/embeddings/sbert/
- Hook/retention (directional) — https://animoto.com/blog/video-marketing/why-first-3-seconds-matter · https://www.opus.pro/blog/youtube-shorts-hook-formulas · https://brandefy.com/psychology-of-viral-video-openers/

**Relevant Laura files grounding the recommendations (absolute paths):** `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\semantic.py` (sentence/speaker seams to upgrade with SaT), `...\analysis\editorial.py` + `...\analysis\cutplace.py` + `...\analysis\joint.py` (cut-placement ranking to extend), `...\analysis\silence.py` (reuse for acoustic SBD + audio-energy peaks), `...\analysis\transition_review.py` + `...\analysis\vlm_ollama.py` (the exact Protocol+Ollama+Stub+cache-by-signature pattern to clone for the new `ClipScorerBackend`), `...\scenes\grouping.py` (the structural template for the new `analysis\topics.py` segmenter).

---

## 5 · Multimodales CutScore-Modell

I now have enough grounded prior art and a precise picture of Laura's signals. Let me compose the design report. This is a synthesis task; my final response is the deliverable.

# Multimodal Cut-Scoring & Segment-Selection Model for Auto-Shorts (for Laura)

Two-level model. **Level 1 (CutScore):** scores every candidate *boundary frame* — exactly the user's template, and a near-verbatim generalization of what Laura's `joint.py:joint_place` already does per-cut. **Level 2 (SegmentSelect):** treats Shorts extraction as *constrained subset/interval selection over candidate boundaries*, solved by DP/shortest-path with duration + hook constraints. Boundary quality (Level 1) defines edge cost; clip content desirability defines node value.

Laura is unusually well-positioned: `joint_place` is already `score(f) = (w_visual·visual + w_editorial·editorial)/Σw` over a candidate window. The Shorts model is the same idea promoted from "place one cut" to "select a set of clips."

---

## 0. What Laura already has → keep / upgrade

| Template term | Laura signal that already exists | Verdict |
|---|---|---|
| VisualBoundaryScore | `eval_cut._diff_signal` (luma `d(f)`), `refine.snap_boundaries`, TransNetV2 `confidence` + PySceneDetect adaptive (`shots.py`, `fuse.py`) | **Keep.** Add: use TransNet soft logits as the score, not just the snapped boundary. |
| AudioSilenceScore | `silence.detect_silence` → end-exclusive `[start,end)` frame intervals (ffmpeg `silencedetect`) | **Keep.** Upgrade: keep dB depth + duration, not just the binary interval. |
| TranscriptBoundaryScore | `semantic.sentence_end_frames`, `editorial._gap_frames`, WhisperX word `[start_frame,end_frame)` | **Keep.** This is Laura's crown jewel — the word-safe boundary guarantee. |
| SemanticBoundaryScore | `semantic.speaker_turn_frames` (diarization); embeddings not yet wired into cut logic | **Upgrade.** Add sentence-embedding topic-shift (local model). |
| WordInterruptionPenalty | `editorial._covering_word` (mid-word detection) | **Keep — promote to a hard constraint** (never split a word). |
| AudioJumpPenalty | not computed yet | **Add** (RMS/loudness discontinuity + VLM `acrossfade` need). |
| FaceMotionDiscontinuityPenalty | partial: VLM `transition_review` returns `smoothness ∈ [0,1]` + `motion_break` label | **Upgrade.** Cheap optical-flow proxy by default; VLM verdict as the expensive confirmer. |

The existing tier constants in `joint.py` (`_SCORE_SPEAKER_TURN=1.0 > SENTENCE_END=0.95 > SILENCE=0.85 > WORD_EDGE=0.70 > MID_WORD=0.0`) are already a hand-tuned ranking of boundary desirability. The design below makes those tiers a *learned* continuous score and adds the segment layer on top.

---

## 1. Per-term feature definitions + computation

All times are **integer source frames**, ranges **end-exclusive** (Laura invariants #1/#2). Audio features are computed in **samples** then projected to frames for scoring (invariant #3). Let a candidate boundary be frame `t`.

### w1 · VisualBoundaryScore(t)
*"How real is the picture-cut here?"* Combine three sources, take the max (a boundary is strong if any detector fires):
- **Luma peak**: `vis_luma(t) = d(t) / max_{w}d` over a ±W window — Laura's existing normalized `_diff_signal`. Cheap, always available, no GPU.
- **TransNet logit**: `vis_transnet(t) = σ⁻¹` prediction at `t` from the `scene-ml` extra. Use the *soft per-frame probability*, not just the post-threshold boundary. Best signal; optional.
- **Detector agreement**: `vis_fuse(t)` = the `ShotResult.confidence` from `fuse.fuse_shots` (adaptive ∧ TransNet agreement).
`VisualBoundaryScore(t) = max(vis_luma, vis_transnet, vis_fuse)`.

### w2 · AudioSilenceScore(t)
*"Is there a real pause to cut on?"* From `silence.detect_silence`, for the silence interval containing/nearest `t`:
- `dur = (end−start)` frames; `depth = max(0, noise_floor_dB − measured_dB)`.
- `AudioSilenceScore(t) = sat(dur/dur_ref) · sat(depth/depth_ref)`, with `sat(x)=min(1,x)`, `dur_ref≈0.4s`, `depth_ref≈25 dB`. Zero outside any silence. This upgrades the current binary `_in_silence` to a graded score (a long deep breath > a 1-frame dropout).

### w3 · TranscriptBoundaryScore(t)
*"Is this a clean speech edge / sentence end?"* From WhisperX words:
- `0` if `t` is mid-word (`_covering_word(t)` not None) — this also drives the interruption penalty.
- Else graded: `1.0` if `t ∈ sentence_end_frames`; `0.8` if in a real word-gap (`_gap_frames`, gap ≥ clause threshold); `0.5` on a bare word edge. Optionally weight by gap length: longer inter-word pause → higher.

### w4 · SemanticBoundaryScore(t)
*"Is this a meaning seam (topic / speaker change)?"*
- **Speaker turn**: `1.0` if `t ∈ speaker_turn_frames` (diarization).
- **Topic shift**: embed each sentence with a *local* model (e.g. `bge-small`/`all-MiniLM` via sentence-transformers, or reuse the Ollama embedding endpoint Laura already talks to). For adjacent sentences `s_i, s_{i+1}` around `t`, `topic(t) = 1 − cos(emb_i, emb_{i+1})`. High cosine distance = topic boundary (the classic *TextTiling* lexical-cohesion dip, now neural).
- `SemanticBoundaryScore(t) = max(speaker_turn, topic_shift)`.

### p1 · WordInterruptionPenalty(t)
`1.0` if `_covering_word(t)` is not None, else `0`. **Recommendation: make this a hard constraint** (forbid the boundary), not a soft penalty — it's the user's non-negotiable "never split a word/syllable." Keep the soft form only for tolerance fallback when no clean frame exists in window (mirrors `align_cut`'s `unchanged_out_of_window`).

### p2 · AudioJumpPenalty(t)
Discontinuity in the audio envelope across the cut: `p2(t) = |RMS_dB(left 100ms) − RMS_dB(right 100ms)| / ref`. Large jump (loud→loud splice, music phrase cut mid-beat) = jarring. This is what `acrossfade` in Laura's renderer fixes; the penalty tells you *when* a crossfade is needed and flags cuts that can't be saved.

### p3 · FaceMotionDiscontinuityPenalty(t)
*"Does motion/identity jump across the cut?"* Two tiers:
- **Cheap default (no GPU):** mean optical-flow magnitude (Farnebäck, OpenCV) just before vs after `t`; large flow *into* the cut frame = mid-gesture cut. Also a histogram/SSIM delta of the framing. `p3 = sat(flow_discontinuity/ref)`.
- **Expensive confirmer (optional):** Laura's VLM `transition_review` → `p3 = 1 − smoothness`, with `label ∈ {motion_break, hard_jolt}` forcing high penalty. Run the VLM only on the *top-K selected* boundaries (it's slow), not on every candidate.

---

## 2. Normalization to a common scale

The terms have wildly different native units (luma diff, dB, cosine distance, flow px). Normalize **per long-input, per term** so a knob like `w1` means the same thing across videos.

**Recommended: robust z-score → squash.** For each term `x` over all candidate frames in *this* video:
```
x_robust = (x − median(x)) / (1.4826 · MAD(x) + ε)     # MAD = median abs deviation
x_norm   = σ(x_robust)                                  # logistic squash → (0,1)
```
Robust (median/MAD) beats plain z-score because boundary signals are heavy-tailed (a few huge cuts, mostly flat) — a single hard cut would blow up a std-based z-score and crush everything else. Squashing to (0,1) keeps all terms on a comparable scale so weights are interpretable and the DP costs stay bounded.

- For **bounded, semantically-anchored** terms (TranscriptBoundaryScore tiers, TransNet probability) skip the robust step — they're already calibrated in [0,1]; just clamp.
- For **min-max** (simplest, used by current `joint.py` window-peak normalization): fine *within a small window* (that's what `_visual_scores` does), **bad globally** — one outlier sets the max. Keep min-max only for the local snap window; use robust-z for the global candidate field.
- Fit normalization stats on the **whole long input** once, cache them with `(input, pipeline_version)` per Laura's idempotency invariant #7, so re-runs are deterministic.

Final per-frame cut score:
```
CutScore(t) = w1·V(t) + w2·A(t) + w3·T(t) + w4·S(t)
              − p1·Iword(t) − p2·Ajump(t) − p3·Fmotion(t)
```
with all terms in [0,1] post-normalization and weights/penalties as in §3.

---

## 3. Weights: manual vs learned

### 3a. Manual (ship this first — zero training data)
Start from Laura's already-tuned tier ordering. Sensible defaults on the (0,1) scale:
`w1=1.0, w2=0.8, w3=1.2, w4=0.9` (transcript safety weighted highest — it's the product promise), `p1=∞ (hard constraint), p2=0.6, p3=0.7`.
Expose one editor knob `cut_bias ∈ [0,1]` (picture↔sound) exactly like the existing `joint.bias_to_weights`, scaling `(w1) vs (w2,w3,w4)`. Keep a config block so power users override per-term, defaults in a `cutscore.toml`, hot-reloaded — matches Laura's env-flag convention (`LAURA_*`).

### 3b. Learned — three escalating options

**(i) Pointwise logistic regression (boundary classifier).** Label each candidate frame `good cut / not` from where humans actually cut (Laura already records edits in OTIO + the ledger — `ledger/store.py`). Features = the seven terms. Fit `P(good|t)=σ(wᵀx)`; the learned `w` *are* your weights, and the intercept calibrates. Tiny, CPU-only (scikit-learn), interpretable, license-clean. **Best first learned model.**

**(ii) Pairwise learning-to-rank (RankSVM / RankNet).** The dominant paradigm in highlight detection: from edited videos, *kept* segments rank above *dropped* ones. Construct pairs `(t⁺ a human cut on, t⁻ a nearby frame they didn't)` and minimize pairwise hinge/logistic loss `Σ max(0, margin − wᵀ(x⁺−x⁻))`. This learns *relative* boundary quality, which is exactly what the DP edge costs need, and is robust to the fact that "absolute cut goodness" has no ground truth. This is the method of Sun et al. (RankSVM on edited videos) and Yao et al. (pairwise *deep* ranking, +10.5% over RankSVM).

**(iii) Contrastive cut model ("Learning to Cut by Watching Movies").** Train a small audiovisual net to discriminate *real* editorial cuts from *random* cuts via contrastive loss — no manual labels, just professionally edited video. Yang et al. extracted 255K cuts from 10K videos this way. For Laura this is the long-game upgrade to `VisualBoundaryScore`/`FaceMotionDiscontinuity`: a learned "is this a plausible cut frame" head, runnable locally as a distilled small model. Flag: **strongest evidence but heaviest**; gate behind an optional extra, never a default dependency (Laura invariant: heavy models optional).

**Data bootstrapping for Laura specifically:** every time a user accepts/nudges an auto-cut in Feinschnitt, log `(features, kept?)` to the ledger. That's a free, growing, in-domain pairwise dataset — close the loop (Xu & Wang's "closing-the-loop" framing). Start manual → collect → fit logistic → graduate to pairwise.

---

## 4. Candidate cut-window generation

Don't score every frame. Generate a sparse **candidate boundary set** `C` = union of seams within a tolerance τ (e.g. ±0.25s), then snap & dedupe:

```
candidates(asset):
    shot_bounds   = {s.src_in_frame for s in hybrid_shots}           # shots.py / fuse.py
    silence_mids  = {midpoint(iv) for iv in detect_silence(asset)}   # silence.py
    sent_ends     = sentence_end_frames(words)                       # semantic.py
    spk_turns     = speaker_turn_frames(words)                       # semantic.py
    raw = shot_bounds ∪ silence_mids ∪ sent_ends ∪ spk_turns
    C = []
    for t in sorted(raw):
        t = joint_place(t, words, window=τ, silence=..., sentence_frames=..., speaker_frames=...)[0]  # reuse Laura's snap!
        if _covering_word(t, words): continue        # hard word-safety constraint
        if C and t - C[-1] < min_separation: continue # dedupe near-duplicates
        C.append(t)
    return [(t, CutScore(t)) for t in C]
```

Key point: **`joint_place` is reused verbatim** to snap each candidate to the locally optimal frame — the new model only *chooses which boundaries to use and which clips to keep*, it doesn't reinvent frame-exact placement. This respects the existing word-safety + visual-peak machinery.

---

## 5. Segment selection: DP / graph search

A Short = an ordered set of clips between candidate boundaries, satisfying duration + hook constraints, maximizing total content value minus cut costs. Two formulations; (B) is the recommended one.

**Node value** `ContentScore(clip)` (separate from boundary score — *what's inside* the clip): from the transcript LLM/heuristic — hook strength, emotional peak, quotability, info density, on-screen face/energy. (This is the OpusClip / AI-Youtube-Shorts-Generator axis: viral score 0–100 over hooks, emotional peaks, opinion, revelation, conflict, quotable lines, story peaks, practical value.) Local-first: a small instruct LLM via Ollama scores transcript windows; degrade to TF-IDF/keyword salience + audio energy if no LLM.

### (A) Single contiguous best clip — max-sum interval (simplest)
Find the `[a,b)` with both endpoints in `C`, `D_min ≤ b−a ≤ D_max`, maximizing
`Score(a,b) = Σ ContentScore over [a,b) + α·CutScore(a) + α·CutScore(b)`.
This is a **prefix-sum + two-pointer** scan, O(|C|²) worst, O(|C|) with the duration window — trivial. Use for "give me the one best 30s clip."

### (B) Multiple clips / optimal trim within a clip — shortest-path DP over boundaries (recommended)
Build a DAG: nodes = candidate boundaries `C` (sorted) + source `START`, sink `END`. A directed edge `(i→j)` means "keep the segment `[Cᵢ,Cⱼ)` as one piece of the Short." Edge weight = **reward** for that segment minus the **cut cost** of landing on `Cⱼ`:

```
reward(i→j) = Σ_{t∈[Ci,Cj)} ContentScore(t)            # content kept
              + λ_cut · CutScore(Cj)                    # quality of the OUT boundary
              − λ_jump · AudioJumpPenalty(Cj)           # (already inside CutScore, keep explicit if desired)
edge exists only if  D_seg_min ≤ Cj−Ci ≤ D_seg_max
```
We then want the **max-reward path** `START→…→END` whose **total kept duration ∈ [D_min,D_max]** (the Short length) and that **starts on a hook**. Because duration is a *budget* constraint, this is a **resource-constrained shortest path / weighted-interval-knapsack**, solved by DP over `(boundary index, accumulated_duration_bucket)`:

```
# DP: dp[j][d] = max reward of a valid clip-set ending at boundary Cj using ~d frames of Short budget
# Quantize duration to buckets of B frames to bound the table (pseudo-poly, like 0/1 knapsack DP).
def select_short(C, content, cutscore, D_min, D_max, B):
    n = len(C); buckets = D_max // B + 1
    dp   = [[-INF]*(buckets+1) for _ in range(n)]
    back = [[None]*(buckets+1) for _ in range(n)]
    # init: a clip may START at any boundary that satisfies the hook constraint
    for i in range(n):
        if not is_hook_start(C[i]): continue          # HOOK CONSTRAINT (see §5c)
        dp[i][0] = HOOK_BONUS * hook_strength(C[i])
    for j in range(n):
        for d in range(buckets+1):
            if dp[j][d] == -INF: continue
            for k in range(j+1, n):                    # extend with segment [Cj, Ck)
                seg = C[k] - C[j]
                if seg < SEG_MIN or seg > SEG_MAX: continue
                nd = d + round(seg / B)
                if nd > buckets: break                 # over Short budget (C sorted ⇒ prune)
                r = dp[j][d] + prefix_content(C[j], C[k]) + LAM_CUT*cutscore[C[k]]
                if r > dp[k][nd]:
                    dp[k][nd] = r; back[k][nd] = (j, d)
    # accept any end-state whose total duration is within [D_min, D_max]
    best = argmax over (k,d) with D_min <= d*B <= D_max of dp[k][d]
    return reconstruct(back, best)                      # the kept segments = the Short
```
This is the classic **importance-score → DP subset selection** used across video summarization (predict per-segment importance, then DP/knapsack to pick the budget-constrained subset), and the **shortest-path-over-candidate-boundaries** segmentation pattern. Complexity `O(n²·buckets)`, n=|C| (sparse, hundreds), trivially fast on CPU.

To extract **several Shorts** from one long input: run the DP, remove the chosen frames' candidates, repeat (greedy peel) — or wrap in **submodular maximization** to enforce diversity (next).

### (C) Diversity across multiple Shorts — submodular / max-marginal-relevance
If you want N non-redundant Shorts, the value of a set isn't additive (two clips about the same moment are redundant). Use Gygli et al.'s framing: maximize a **weighted sum of submodular objectives** under a budget —
`f(S) = β1·interestingness(S) + β2·representativeness(S) + β3·diversity(S)`, where the `β` are *learned from human summaries* and each objective is submodular. Greedy maximization gives a `(1−1/e)` guarantee. For Laura: `interestingness`=Σ ContentScore, `diversity`=coverage of distinct transcript embeddings/speakers, budget=total Shorts minutes. This is the principled way to pick *which* moments become Shorts; the DP in (B) then trims *each* one.

### (C) — Hook constraint, concretely
A Short must open strong (first ~1.5s decides retention). Encode as:
- **Hard:** `is_hook_start(C[i])` = the clip's first sentence is a question / strong claim / named entity / high-energy (LLM-tagged `hook_sentence`, like AI-Youtube-Shorts-Generator), AND `C[i]` is a sentence *start* (not mid-thought). Forbid starts that don't qualify.
- **Soft:** `HOOK_BONUS·hook_strength` in the DP init so the optimizer prefers stronger openings when several qualify.
- **Smart-end already exists**: Laura's `reel_smart_end` snaps the *duration cap* back to a complete word boundary — reuse it as the DP's `D_max` realization so the Short never ends mid-word. The DP picks the clip set; `reel_smart_end` guarantees the tail.

---

## 6. End-to-end pipeline for Laura (keep / add)

```
long rough-cut (OTIO, source of truth)
  → [KEEP] hybrid shots (TransNet+adaptive, fuse, snap)         shots.py / fuse.py / refine.py
  → [KEEP] WhisperX words + sentences + diarization             semantic.py / editorial.py
  → [KEEP] silence intervals                                    silence.py
  → [ADD]  per-term feature extraction + robust-z normalization (new: cutscore.py)
  → [REUSE] candidate boundaries via joint_place snap           joint.py  (no rewrite)
  → [ADD]  CutScore(t) over candidates (manual w → learned)     (new)
  → [ADD]  ContentScore(clip) from local LLM/heuristic          (new: reuse Ollama client)
  → [ADD]  DP/shortest-path segment selection (+submodular div) (new: shortselect.py)
  → [UPGRADE] VLM transition_review on TOP-K boundaries only     transition_review.py
  → [KEEP] xfade/acrossfade renderer + reel_smart_end + 9:16     render/reel.py, handlers.py
```
Everything new is pure + CPU-runnable; every heavy piece (TransNet, VLM, LLM ContentScore, contrastive cut model) stays an **optional extra** with graceful degradation, per Laura's invariants. Persist all scores keyed by `(input, pipeline_version)` (idempotency #7); store edits in OTIO frames; the learned-weights loop feeds off the ledger.

**Minimum viable next step:** add `cutscore.py` (the 7 terms, robust-z, manual weights reusing `bias_to_weights`) + `shortselect.py` (formulation B DP), wire `joint_place` for candidate snapping and `reel_smart_end` for the tail. No new heavy dependency. Learned weights and the contrastive model are phase 2/3.

---

## Evidence quality flags

- **Strong / directly applicable:** Gygli 2015 (learned weighted-submodular summarization), Yang et al. "Learning to Cut by Watching Movies" (contrastive cut scoring — verified via fetched PDF), Yao/Sun pairwise + RankSVM highlight ranking, knapsack/DP subset-selection for summaries. These directly justify the weighted-sum scorer, the learning-to-rank path, and the DP selection.
- **Medium:** the OpusClip-style ContentScore axes (hooks/emotional peaks/quotability) come from an open-source reimplementation's docs (AI-Youtube-Shorts-Generator), not a peer-reviewed source — treat the *specific* viral-criteria list as engineering heuristic, not validated science.
- **Weak / inferred:** the exact shortest-path-DP-over-boundaries phrasing is synthesized from patent abstracts + summarization-DP papers (search snippets), not one canonical paper — the algorithm is standard (resource-constrained shortest path / weighted-interval knapsack) but I did not fetch a single source that states it in this exact Shorts framing. The robust-z normalization choice is a standard-practice recommendation, not cited from a Shorts-specific paper.
- I could not fetch the Gygli PDF directly (HTTP 403); its method summary above is from the search abstract + well-known prior knowledge of that paper — verify the exact β-learning (structured SVM) against the source before implementing.

## Sources (fetched or returned by search)
- Yang et al., *Learning to Cut by Watching Movies* — https://arxiv.org/pdf/2108.04294 (fetched)
- Gygli et al., *Video Summarization by Learning Submodular Mixtures of Objectives*, CVPR 2015 — https://openaccess.thecvf.com/content_cvpr_2015/papers/Gygli_Video_Summarization_by_2015_CVPR_paper.pdf (403 on fetch; via search)
- Yao et al., *Highlight Detection with Pairwise Deep Ranking* (MSR) — https://www.microsoft.com/en-us/research/wp-content/uploads/2016/06/2219-1.pdf
- Sun et al., *Ranking Domain-Specific Highlights by Analyzing Edited Videos*, ECCV 2014 — https://link.springer.com/chapter/10.1007/978-3-319-10590-1_51
- Xu & Wang, *Closing the Loop: A Data-Driven Framework for Effective Video Summarization* — https://www.semanticscholar.org/paper/cf47d0d8fde9fbd619188b0a3b259db86556f2db
- Xiong et al., *Less is More: Learning Highlight Detection from Video Duration* — https://arxiv.org/pdf/1903.00859
- *Mr. HiSum* highlight dataset, NeurIPS 2023 — https://proceedings.neurips.cc/paper_files/paper/2023/file/7f880e3a325b06e3601af1384a653038-Paper-Datasets_and_Benchmarks.pdf
- *ElasticPlay: Interactive Video Summarization with Dynamic Time Budgets* — https://arxiv.org/pdf/1708.06858
- Awesome Video Summarization (paper/code index) — https://github.com/fjchange/Awesome_Video_Summarization
- AI-Youtube-Shorts-Generator (open-source OpusClip alternative; ContentScore criteria + dedup) — https://github.com/samuraigpt/ai-youtube-shorts-generator
- Chapter-Llama, CVPR 2025 (LLM transcript chaptering, relevant to topic-shift SemanticBoundaryScore) — https://openaccess.thecvf.com/content/CVPR2025/papers/Ventura_Chapter-Llama_Efficient_Chaptering_in_Hour-Long_Videos_with_LLMs_CVPR_2025_paper.pdf

## Relevant Laura files (absolute paths)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\joint.py` — the existing per-cut blend to generalize; reuse `joint_place` + `bias_to_weights`.
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\semantic.py` — `sentence_end_frames`, `speaker_turn_frames` (w3/w4).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\editorial.py` — `_covering_word` (p1 hard constraint), `_gap_frames`, `editorial_metrics`.
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\silence.py` — `detect_silence` (w2).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\refine.py` / `eval_cut.py` — `_diff_signal`, `snap_boundaries` (w1 luma).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\shots.py` / `fuse.py` — hybrid TransNet+adaptive + `confidence` (w1).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\transition_review.py` — VLM `smoothness`/`motion_break` (p3 confirmer).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\render\reel.py` + `tests\test_reel_smart_end.py` — 9:16 chain, hook drawtext, word-safe `reel_smart_end` (Short tail constraint).
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\api\shorts.py` — current Shorts next-action state machine to extend with `select_short`.
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\scenes\build.py` — `speech_keep_ranges`/`tighten_rough_cut` (ContentScore dead-air model).

Suggested new modules: `services/local-api/src/laura/analysis/cutscore.py` (7-term features + robust-z + weights) and `services/local-api/src/laura/analysis/shortselect.py` (candidate set + DP/shortest-path selection).

---

## 6 · Produktions-Architektur, Library-Bewertung, Risiken

I now have everything verified. Both shot detectors are MIT; AutoShot ships weights via Baidu (a practical friction point) and is unpackaged research code; TransNetV2 has a clean PyTorch inference path and is already a Laura extra. Let me write the final report.

---

# Auto-Shorts Extraction Pipeline for Laura — Production Design & OSS Evaluation

## 0. Bottom line up front

Laura is already ~70% of the way to a high-quality auto-Shorts pipeline. The hard parts the user listed as "to evaluate" — frame-accurate SBD (TransNetV2 + PySceneDetect adaptive), word-level ASR+align (faster-whisper + WhisperX), transcript-safe cut placement (`cutplace`/`editorial`/`joint`/`semantic`), real ffmpeg `xfade`/`acrossfade`, a VLM transition reviewer, OTIO source-of-truth with integer-frame edits — **exist and are correctly architected** (verified by reading `services/local-api/src/laura/analysis/{editorial,joint,semantic,silence,asr,transition_review}.py`, `scenes/grouping.py`, `render/reel.py`, `pyproject.toml`).

The **named stack maps poorly onto Laura and should mostly be rejected**: Laura is FastAPI + SQLite + a local DB-backed job runner (ADR-0003, `services/local-api/src/laura/jobs/`), explicitly *not* Redis/Celery/Supabase (those exist only behind the `server` extra for on-prem). Redis/Supabase/n8n are anti-patterns for a local-first desktop app. What genuinely needs *adding* for Shorts is small: a **highlight/segment-scoring module**, a **candidate-segment ranker**, a **per-Short render preset path with burned-in captions**, and a **QA gate** — all as pure modules + one or two new job handlers.

Below, every external claim is cited to a source I actually fetched. Where evidence is thin I flag it.

---

## (a) MVP architecture — minimum to ship

Goal: from one long AI rough-cut, produce N vertical 9:16 Shorts with clean cuts, transcript-safe boundaries, captions, no black-frame/mushy-cut artifacts. **Reuse everything Laura has; add 3 pure modules + 1 job handler. CPU-only must work.**

```
Long rough-cut (OTIO, integer frames, audio in samples)  ── source of truth
        │
        ▼
[1] Shots         PySceneDetect adaptive (scene extra)         ← HAVE (analysis/shots.py, transnet.py)
[2] ASR+words     faster-whisper base/int8 (asr extra)         ← HAVE (analysis/asr.py)
[2b] VAD/silence  ffmpeg silencedetect → source-frame ranges   ← HAVE (analysis/silence.py)
[3] Semantic seams sentence_end_frames / speaker_turn_frames   ← HAVE (analysis/semantic.py)
        │
        ▼
[4] SEGMENT BUILDER  group words→sentences→"sense units";       ← ADD (analysis/shorts_segments.py, pure)
     emit candidate [seq_in, seq_out) windows 15–60s that
     start/end ONLY on a sentence_end or speaker_turn frame
        │
        ▼
[5] SCORER (deterministic)  per-candidate features →            ← ADD (analysis/shorts_score.py, pure)
     score: hook strength, completeness, length fit,
     speech density, shot count, silence-edge cleanliness
        │
        ▼
[6] CUT PLACEMENT  joint_place() refines each segment edge      ← HAVE (analysis/joint.py)
     (visual peak × silence × sentence-end × speaker-turn)
        │
        ▼
[7] RENDER  ffmpeg: 9:16 crop+scale, drawtext captions,         ← HAVE/EXTEND (render/reel.py, captions.py)
     -af for clean audio; acrossfade only at internal joins
        │
        ▼
[8] QA gate  black-frame/freeze check + boundary frame-exactness ← ADD (analysis/shorts_qa.py)
     + (optional) word never severed assertion
        │
        ▼
N Shorts as OTIO sub-timelines (kind="scene"/"short") + exports
```

**Orchestration (MVP):** one new job handler `shorts.extract` in the existing local job runner (`jobs/runner.py`, `enqueue`) chained after the existing auto-analyze pipeline (`ai/auto_pipeline.py`). No Redis, no n8n. SQLite stores candidate segments + scores (a `shorts_candidates` table, mirroring `scenes`). This is the smallest thing that ships and runs without a GPU (faster-whisper `int8`, PySceneDetect on CPU).

**What MVP deliberately skips:** embeddings/Qdrant, VLM scoring, MFA, AutoShot, diarization-as-default.

---

## (b) Advanced architecture

Adds quality and "agentic" judgment on top of the deterministic spine — all optional extras, all local:

```
                 ┌─────────────────────── deterministic spine (MVP) ───────────────────────┐
ASR ── WhisperX align (align extra, wav2vec2) ──► ±50ms word timestamps  ← UPGRADE accuracy
SBD ── TransNetV2 (scene-ml extra) as primary, PySceneDetect adaptive fallback
VAD ── Silero VAD (ADD) replacing/augmenting silencedetect for speech-grade gaps
Diarize ── pyannote (diarize extra) → real speaker_turn seams (HF-token gated)
                 └──────────────────────────────────────────────────────────────────────────┘
                                          │
            ┌─────────────────────────────┼─────────────────────────────┐
            ▼                             ▼                             ▼
[EMBED] fastembed + Qdrant         [VLM HOOK/HIGHLIGHT]          [VLM TRANSITION REVIEW]
(semantic extra, ALREADY present)  Ollama qwen-vl: rank          (HAVE: transition_review.py)
topic clustering, dedupe           candidates by "would this     judges each internal cut;
near-duplicate Shorts,             stop a scroll?"; pick title/   proposes resnap or crossfade;
semantic search over candidates    hook text; pick caption style  applies via apply_fix()
            │                             │                             │
            └─────────────────────────────┴─────────────────────────────┘
                                          ▼
                       [AGENTIC PLANNER] LLM (local, Ollama) reads the
                       deterministic feature table + transcript and
                       *proposes* segment boundaries & ranking as a TOOL CALL,
                       which the deterministic layer VALIDATES and snaps to
                       frame-exact, transcript-safe edits (LLM never emits frames)
                                          ▼
                       Per-platform render presets (TikTok/Reels/Shorts:
                       safe-area, caption position, loudness target),
                       multi-variant A/B hook generation
```

**Advanced orchestration:** still the local job runner. If the user ever runs the **server/on-prem** profile, the *same* handlers run under Celery+Redis+Postgres+Qdrant via the existing `server` extra — that is the only place Redis/Supabase-equivalents belong. n8n is unnecessary; if external orchestration is ever wanted, expose the pipeline through Laura's existing **MCP server** (`mcp` extra, `laura.mcp.server`) so any agent runner can drive it — cleaner than n8n for a code-first team.

---

## (c) Data-flow (ingest → SBD → ASR/align → VAD → semantic/highlight → multimodal scoring → candidate segments → render → QA)

End-to-end, with the **invariants** enforced at each step (frames are integers, ranges end-exclusive, audio in samples, OTIO is truth — per project `CLAUDE.md`):

| Stage | Input | Process | Output (Laura representation) | Status |
|---|---|---|---|---|
| **Ingest** | rough-cut file/URL | probe → CFR editorial proxy; VFR→CFR proxy (invariant #5) | asset + proxy, `rate_num/den`, `duration_frames` | HAVE (`ingest/`) |
| **SBD** | proxy | TransNetV2 (primary) or PySceneDetect adaptive; diff-peak snap | `shots[]` as `[src_in, src_out)` source frames | HAVE (`shots.py`,`transnet.py`,`refine.py`) |
| **ASR** | audio (16k mono) | faster-whisper word_timestamps; **VAD filter on** to kill silence hallucination | `WordResult` seconds → `mapping.py` → source frames | HAVE (`asr.py`,`mapping.py`) |
| **Align** | words+audio | WhisperX wav2vec2 forced align → ±50ms | tightened `start_frame/end_frame` | HAVE (`align.py`, opt) |
| **VAD/silence** | audio | `silencedetect` (HAVE) → upgrade to Silero VAD for speech-grade pauses | `[start,end)` silence frame ranges | HAVE + **ADD Silero** |
| **Semantic seams** | words(+speaker) | `sentence_end_frames`, `speaker_turn_frames` | source-frame sets | HAVE (`semantic.py`) |
| **Highlight/segment build** | words+seams+shots | group into sense-units; enumerate 15–60s windows that *begin and end only on a seam frame* | candidate `[seq_in, seq_out)` list | **ADD** |
| **Multimodal scoring** | candidates + features | deterministic feature score; (adv) VLM/LLM re-rank; (adv) Qdrant dedupe | ranked candidates + score JSON | **ADD** (det.) / HAVE-adjacent (VLM, embed) |
| **Cut placement** | each candidate edge | `joint_place()` blends visual peak × silence × sentence-end × speaker-turn within ±window | frame-exact, word-safe edges | HAVE (`joint.py`) |
| **Render** | chosen segment | ffmpeg 9:16 crop+scale, `drawtext` captions, loudnorm; `acrossfade` only at internal joins | MP4 per Short + OTIO sub-timeline | HAVE/EXTEND (`reel.py`,`captions.py`,`mp4.py`) |
| **QA** | rendered Short | black/freeze detect (`blackdetect`/`freezedetect`), boundary-exactness (`eval_cut`/`eval_quality`), assert no word severed (`editorial_metrics.pct_mid_word==0`) | pass/fail + report | **ADD** (wraps HAVE) |

The crucial design choice — and Laura already embodies it — is that **segment boundaries are chosen in transcript/seam space, then `joint_place` reconciles them with the visual peak**, rather than cutting visually and hoping speech lines up. `joint.py`'s tier (`speaker_turn 1.0 > sentence_end 0.95 > silence 0.85 > word_edge 0.70 > mid_word 0.0`) is exactly the right scoring for "never split a word/sense-unit." The Shorts segment builder should reuse `sentence_end_frames`/`speaker_turn_frames` as the *only legal* start/end candidates, guaranteeing transcript-safe boundaries by construction.

---

## (d) Open-source library evaluation

License/accuracy/runnability verified against fetched sources (URLs in Sources). "Laura status" maps each to the codebase.

| Library | Role | Maturity | License | GPU need | Local-runnable | Accuracy (cited) | Laura status / recommendation |
|---|---|---|---|---|---|---|---|
| **FFmpeg** | decode, proxy, silencedetect, xfade/acrossfade, blackdetect, render | Very high | LGPL/GPL | No | Yes | n/a | **KEEP** — core. xfade needs CFR + identical fmt/rate/timebase on both inputs (Laura proxies are CFR, so OK). |
| **PySceneDetect** | adaptive shot detection (CPU fallback) | High, active | **BSD-3-Clause** | No | Yes | rolling-avg adaptive threshold reduces false cuts on camera motion | **KEEP** as default/CPU engine (`scene` extra). |
| **TransNetV2** | learned SBD (primary) | Mature research, PyTorch inference released | **MIT** | Optional (GPU faster) | Yes | **ClipShots F1 77.9, RAI F1 93.9** | **KEEP** as `scene-ml` primary; clean license, weights bundled, PyTorch path. |
| **AutoShot** | learned SBD (alt) | Research code only, **no PyPI**, weights via **Baidu** link | **MIT** | Yes (PyTorch) | Awkward (vendoring + Baidu download) | **+4.2% F1 over TransNetV2 on SHOT** (short-video dataset); ClipShots/BBC/RAI +1.1/0.9/1.2% | **DON'T add now.** Marginal gain, packaging/weights friction. Revisit only if short-form SBD becomes a measured bottleneck. |
| **faster-whisper** | ASR + word timestamps | High, active | MIT | Optional (int8 CPU OK) | Yes | Whisper-class; **enable `vad_filter=True`** to cut silence hallucination | **KEEP** (`asr` extra). **Action: turn on VAD filter** in `asr.py` (see risks). |
| **WhisperX** | word align (wav2vec2) + batch | High | **BSD-2-Clause** (current main; older refs say BSD-4 — stale) | Recommended | Yes (CPU possible, slow) | **±50ms** word timestamps; up to ~70× RT batched | **KEEP** (`align` extra). License is clean (BSD-2), not BSD-4 — earlier concern resolved. |
| **Silero VAD** | speech/silence segmentation | High, active | **MIT** | No (tiny) | Yes | strong cross-domain; but **~61% utterance acc on ESC-50 noise** → not perfect on pure noise | **ADD** (small dep). Upgrades `silence.py`'s acoustic-floor `silencedetect` to speech-aware gaps; also the VAD that faster-whisper uses internally. |
| **Montreal Forced Aligner** | phoneme-level forced align | Mature (academic) | code permissive; **models CC-BY-4.0**, per-model varies | No (Kaldi) | Yes but **heavy conda/Kaldi install**, per-session model downloads | sub-phoneme alignment | **SKIP.** WhisperX wav2vec2 already gives ±50ms word align with far lighter deps; MFA's conda/Kaldi footprint conflicts with local-first/uv. Keep as "optional research" only. |
| **pyannote.audio** | diarization → speaker turns | High | code MIT; **models gated on HF (token + accept conditions)** | Recommended | Yes, but **needs HF token + user-agreement** | SOTA diarization | **KEEP optional** (`diarize` extra). Flag the **HF-token gating** as a local-first wrinkle — document it; never default-on. |
| **FastAPI** | API/worker | Very high | MIT | No | Yes | n/a | **KEEP** — Laura is built on it. |
| **Redis** | queue broker | Very high | RSALv2/SSPL (not OSI) | No | Yes | n/a | **DON'T add to desktop.** Laura uses a SQLite-backed local job runner (ADR-0003). Redis only in `server` extra. |
| **Supabase** | metadata/auth | High | Apache-2 (self-host) | No | Yes (heavy) | n/a | **REJECT for local-first.** SQLite already is Laura's metadata store; Supabase = cloud-shaped Postgres+stack, wrong fit. Postgres only via `server` extra. |
| **Qdrant** | vector DB | High | Apache-2 | No | Yes | n/a | **OPTIONAL** — already wired via `semantic`/`server` extras (`fastembed`+`qdrant-client`). Use only for dedupe/semantic-search in Advanced. |
| **n8n** | workflow orchestration | High | Sustainable-Use (fair-code, not OSI) | No | Yes (Node service) | n/a | **REJECT.** Extra always-on service for a desktop app; the local job runner + MCP server cover orchestration with no new daemon and a non-OSI license avoided. |

Evidence flags: AutoShot's exact F1 deltas come from its own paper (self-reported, single short-video dataset) — treat the "+4.2%" as author-favorable, not independent. Silero's ESC-50 61% figure is from a secondary blog summary of VAD limits — directionally right (VAD is imperfect on pure noise) but not a primary benchmark.

---

## (e) Risks + typical failure modes

| Failure mode | Cause | Mitigation in Laura terms |
|---|---|---|
| **ASR hallucination on silence/music** | Whisper invents "ghost" repeated text on non-speech; AI rough-cuts often have music beds/silence. Documented: Whisper "ghost transcripts" + repeats on non-speech. | **Enable `vad_filter=True` in `asr.py`** (faster-whisper runs Silero VAD, skips non-speech). Add a compression-ratio / no-speech-prob drop. This is the single highest-value MVP fix. |
| **Drift (timestamp ↔ frame)** | seconds→frame rounding accumulates; VFR sources; proxy vs source rate mismatch | Laura already mandates integer frames + audio-in-samples + CFR proxy + separate source-mapping (invariants #1,#3,#5). Keep ASR seconds confined to `mapping.py`; never store float seconds as state. WhisperX align tightens drift to ±50ms. |
| **Off-by-one frames** | end-inclusive vs end-exclusive confusion at segment edges | Invariant #2 (`out_frame_exclusive`) is enforced throughout (`editorial.py` docstrings, `joint._candidate_range` clamps to `total_frames-1`, frame 1 floor). New Shorts modules **must** reuse `[start,end)` and the `Word` half-open convention — do not reinvent. |
| **Black-frame / blank over-drop** | AI rough-cuts insert black/fade frames; segment starts on black → dead opener; or QA over-trims | Add `blackdetect`+`freezedetect` QA pass; **forbid a Short from starting/ending inside a black/freeze run**; snap to first non-black seam. Do *not* aggressively trim — flag-and-snap. |
| **Mushy crossfades** | applying `xfade`/`acrossfade` to *every* cut turns sharp edits into soft dissolves; xfade also needs identical CFR/fmt/rate/timebase or it errors/garbles | Laura's `transition_review.py` already gets this right: crossfade is proposed **only** for contiguous same-source dead-air jumps, never distinct material. For Shorts: hard cuts by default; `acrossfade` (~6–12 frames) only at internal same-source joins; never a video xfade at a speaker/sentence boundary. |
| **Word/syllable severed** | visual-only cut lands mid-word | Already solved: build segments only on `sentence_end`/`speaker_turn` frames, then `joint_place`; assert `editorial_metrics.pct_mid_word == 0` in QA. |
| **Near-duplicate Shorts** | scorer picks 5 overlapping windows of the same moment | Advanced: fastembed+Qdrant cosine dedupe; MVP: forbid candidate overlap > X% in the ranker. |
| **VAD misses on noisy/music beds** | Silero ~61% on pure-noise (ESC-50) | Treat VAD as additive (Laura's silence is already "purely additive, never breaks the cut"); combine VAD ∪ silencedetect, don't rely on either alone. |
| **pyannote gating breaks "local-first"** | diarization model needs HF token + accepted user agreement | Keep diarization strictly opt-in; degrade gracefully to no speaker seams (Laura's `speaker_turn_frames` already returns ∅ when unlabeled). Document the token step. |

---

## (f) Where an agentic (LLM/VLM-in-the-loop) system adds value vs deterministic code

**Principle (matches Laura's existing design): the model proposes; deterministic code disposes — frame-exactness and transcript-safety are NEVER delegated to an LLM.** Laura's `transition_review.py` is the template: the VLM returns a *structured verdict* (`smoothness`, `label`, `SuggestedFix`), and deterministic `apply_fix()` clamps the resnap to the editorial window and validates the roll against the pure op. Replicate this boundary everywhere.

**Deterministic code owns (do NOT use an LLM):**
- All frame arithmetic, range math, sample↔frame mapping (invariants).
- SBD, ASR, alignment, VAD, silence detection.
- Snapping a cut to a seam (`joint_place`) — it is cheap, exact, explainable, testable.
- QA assertions (no word severed, no black opener, boundary exactness).

**Agentic LLM/VLM adds genuine value (judgment, not arithmetic):**
1. **Highlight/hook ranking** — "which 30s of this 10-min rough-cut would stop a scroll?" is taste, not a formula. A local LLM reading the transcript + the deterministic feature table ranks candidates and writes the **hook/title/caption text** (fed to `reel.py`'s `drawtext textfile=`). High value.
2. **Segment-boundary *proposal*** — LLM suggests "start the clip at this sentence, end at the punchline," emitting *which seam* (by transcript reference, not by frame). Deterministic layer maps the referenced word→`sentence_end_frame` and snaps. Value: better narrative arcs than a length/density heuristic.
3. **VLM transition review** — already shipped; keep. Judges visual fluidity of a cut where a luma-diff number can't. Opt-in (`LAURA_VLM_MODEL`+Ollama, off by default — correct).
4. **VLM thumbnail/safe-area check** — does the 9:16 crop cut off a face/caption? A VLM glance is cheaper to build than face-tracking. Medium value.
5. **Dedup/topic labeling** — embeddings (fastembed/Qdrant) for near-duplicate suppression and per-Short topic tags. Deterministic vectors, LLM only for human-readable labels.

**Anti-patterns (don't):** LLM emitting frame numbers or timecodes; LLM doing the actual cut; LLM "deciding" crossfade durations as free text. All of these reintroduce drift/off-by-one and are untestable. Keep them in code.

---

## Concrete to-do for Laura (keep / add / upgrade)

**Keep as-is (the spine is right):** OTIO truth + integer frames + samples; `joint.py` blended placement; `semantic.py` seams; `silence.py`; `transition_review.py` (model-proposes/code-disposes); `reel.py` 9:16 + drawtext; local job runner (no Redis); SQLite metadata (no Supabase).

**Add (small, local, license-clean):**
1. `analysis/shorts_segments.py` (pure) — enumerate 15–60s candidates starting/ending only on `sentence_end_frames`/`speaker_turn_frames`; reuse `Word`/half-open ranges.
2. `analysis/shorts_score.py` (pure) — deterministic feature scorer (hook position, completeness, length fit, speech density, shot count, silence-edge cleanliness, overlap penalty).
3. `analysis/shorts_qa.py` — `blackdetect`/`freezedetect` + `editorial_metrics.pct_mid_word==0` + `eval_cut` exactness gate.
4. `jobs` handler `shorts.extract` chained after `ai/auto_pipeline.py`; new `shorts_candidates` SQLite table mirroring `scenes`.
5. **Silero VAD** as an `asr`-adjacent extra (MIT, tiny) and **flip `vad_filter=True`** in `asr.py` (`_run`) — biggest quality win for AI rough-cuts.

**Upgrade (already optional extras — just wire defaults):** prefer TransNetV2 when `scene-ml` present; enable WhisperX align when `align` present; surface a local LLM (Ollama) "hook/rank" tool behind a flag, validated by the deterministic layer.

**Reject for the desktop build:** Redis, Supabase, n8n, Montreal Forced Aligner, AutoShot. (Redis/Postgres/Qdrant remain valid *only* in the existing `server`/on-prem extra; Qdrant optional for dedupe.)

---

## Relevant Laura files (absolute paths)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\editorial.py` — word-safe alignment + `editorial_metrics`
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\joint.py` — blended visual+editorial cut placement (the core to reuse for Short edges)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\semantic.py` — `sentence_end_frames`/`speaker_turn_frames` (legal Short boundaries)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\silence.py` — silencedetect VAD-equivalent (upgrade target for Silero)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\asr.py` — faster-whisper (turn on `vad_filter`)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\analysis\transition_review.py` — VLM model-proposes/code-disposes template + `apply_fix`/`acrossfade`
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\scenes\grouping.py` — seam-based grouping to extend for Shorts
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\render\reel.py` — 9:16 crop+scale + drawtext caption renderer
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\jobs\__init__.py` + `runner.py` — local DB job queue (ADR-0003; the orchestrator, not Redis)
- `C:\Users\User\Desktop\Laura\services\local-api\pyproject.toml` — extras map (`scene`, `scene-ml`, `asr`, `align`, `diarize`, `semantic`, `server`, `mcp`)

## Sources
- PySceneDetect AdaptiveDetector + BSD-3: https://www.scenedetect.com/docs/0.6.4/api/detectors.html , https://github.com/breakthrough/pyscenedetect
- TransNetV2 (MIT, PyTorch, ClipShots F1 77.9 / RAI 93.9): https://github.com/soCzech/TransNetV2
- AutoShot (MIT, research code, Baidu weights, +4.2% on SHOT): https://github.com/wentaozhu/AutoShot , https://arxiv.org/abs/2304.06116
- WhisperX (BSD-2-Clause, ±50ms align, faster-whisper backend, pyannote diarization): https://github.com/m-bain/whisperX
- Silero VAD (MIT): https://github.com/snakers4/silero-vad
- Whisper hallucination on non-speech + VAD mitigation: https://arxiv.org/html/2501.11378v1 , https://arxiv.org/html/2505.12969v1
- Montreal Forced Aligner (install/models CC-BY-4.0): https://montreal-forced-aligner.readthedocs.io/en/latest/installation.html
- pyannote 3.1 (MIT but HF-gated, token + accept conditions): https://huggingface.co/pyannote/speaker-diarization-3.1
- FFmpeg xfade (CFR + identical fmt/rate/timebase required; duration/offset): https://ffmpeg.org/ffmpeg-filters.html , https://ottverse.com/crossfade-between-videos-ffmpeg-xfade-filter/

---
