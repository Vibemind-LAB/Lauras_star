# Auto-Shorts-Cutter — Implementierungsplan (agent-steuerbar)

> Quelle der Wahrheit für Design: [docs/research/short-extraction-synthesis.md](../../research/short-extraction-synthesis.md)
> (+ short-extraction-deep-research.md). Ausführung via subagent-driven-development. Ledger: `.superpowers/sdd/progress.md`.

**Ziel:** aus dem MVP einen professionellen, **von AI-Agents (AutoGen o.ä.) steuerbaren** Auto-Shorts-Cutter machen — auf Lauras vorhandenem deterministischen Rückgrat, local-first (n8n/Supabase erst später, externe Integration).

## Globale Constraints
- Python 3.11 + uv + ruff + mypy + pytest; TS strict, kein `any`; Conventional Commits; projektlokaler Logger.
- Invarianten: Integer-Frames, end-exclusive, Audio in Samples, OTIO = Wahrheit, **`(input, pipeline_version)`-Idempotenz**.
- Schwere Modelle = optionale Extras; Backend startet ohne GPU. „Modell schlägt vor, Code entscheidet" — Frame-Genauigkeit nie an ein LLM.
- EXPLIZIT `git add <paths>` (nie `-A`); `uv.lock`/`.superpowers` nie committen. AI-Runtime-Subtree (`services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py`) nicht anfassen.

## Task-Reihenfolge (SDD)

- **S0 — Schwarz-Filter flag-and-snap** *(Voraussetzung; Sofort-Fix)*
  `analysis/quality.py`: „black" nur wenn **mean < BLACK_LUMA UND max < BLACK_MAX(~48)** (dunkle Bühne + Spotlight ≠ schwarz). Keep-Logik: **nie 100 % der Shots eines Assets als black verwerfen** (sonst leerer Rough-Cut → die hellsten behalten). `pipeline_version` 2→3 (Idempotenz). Tests: dark-but-lit→keep, true-black→drop, never-drop-all. Danach „watch" re-analysieren → echte Keep-Shots.
- **S1 — `vad_filter=True`** in `analysis/asr.py` *(Sofort-Fix)* — killt Whisper-Halluzinationen auf Stille/Musik. Config-Flag, Test.
- **S2 — `analysis/shorts_segments.py`** (pure): Kandidaten-Fenster 15–60 s, die **nur** auf `sentence_end_frames`/`speaker_turn_frames` beginnen/enden (transkript-sicher per Konstruktion). Reuse `Word`/half-open.
- **S3 — `analysis/shorts_score.py`** (pure): multimodaler CutScore (visual/audio-silence/transcript/semantic − word-interrupt(hard)/audio-jump/face-motion), robust-z (Median/MAD), Gewichte manuell (Defaults aus `joint.py`-Tiers). Tests.
- **S4 — `analysis/shorts_qa.py`**: `blackdetect`/`freezedetect` + `editorial_metrics.pct_mid_word==0` + boundary-exactness. Kein Short startet/endet auf Schwarz/Freeze.
- **S5 — `shorts.extract` Job + `shorts_candidates`-Tabelle** (Migration, spiegelt `scenes`) + API (`GET /…/shorts`, `POST …:extract`). Kette nach `ai/auto_pipeline.py`.
- **S6 — Auto-Glätten beim Build** (VLM-bewusst): `review_transitions` + `apply_transition_fix` über **alle** Boundaries beim Rough-Cut-Build; `LAURA_VLM_MODEL=qwen3-vl:8b` (Ollama läuft). Crossfade nur same-source, hart an Sprechgrenzen.
- **S7 — Agentic MCP-Tools** *(das „für AI agents in AutoGen"-Ziel)*: `analyze_video`, `generate_cut_candidates`, `score_short_variants`, `render_short`, `run_export_qa`, `request_human_review`, `explain_decision` im `mcp`-Extra. Jedes liefert Explainability-Payload. Agent plant/parametrisiert/entscheidet — nie frame-genau.
- **S8 — Frontend Shorts-View**: Kandidaten-Liste + Score + Preview + „rendern".
- **S9 — Golden-Set-QA-Harness** (Stabilisierung): kleine Fixture-Sammlung + Regression-Metriken (cut-F1, word-interruption-rate, audio-jump). pyloudnorm-Loudness-Gate.
## Visual-Embedding-Layer (VE) — vom User priorisierter Upgrade-Layer

> Storage (Qdrant-Auflösung): User nennt Qdrant; geerdet in Lauras local-first-Realität speichern wir Embeddings **lokal** (SQLite-BLOB float32 + dims + model; optional sqlite-vec; brute-force-Cosine reicht für Single-Video-Scale) hinter einem `VectorStore`-Protocol. Qdrant wird ein **opt-in `server`-Backend** für die spätere externe-App-Integration (wie n8n/Supabase) — kein Desktop-Zwang. Konsistent mit short-extraction-synthesis.md. CLIP/SigLIP/MediaPipe = optionale Extras (`visual`), Backend startet & läuft ohne → CutScore-Visualterme degradieren neutral.

Reihenfolge: **S1→S5 (Backbone) → VE1→VE4 (visuelle Semantik in den Score) → S6/S7+VE5 (Glätten + Agent-Tools) → VE6/S8/S9.** „Modell schlägt vor, Code entscheidet" gilt auch hier: Embeddings *bewerten/ranken*, schneiden nie frame-genau.

- **VE1 — Frame-Sampling + Embedding-Worker** (`visual`-Extra): FFmpeg sampelt Frames (1 fps + alle Shot-Boundary-Frames + Cut-Kandidaten ±1 s); CLIP/SigLIP (open_clip o. transformers, lazy-load, CPU-fähig) → Frame-Embeddings. Idempotenz `(asset, pipeline_version, model)`. Ohne Extra: Worker skippt sauber.
- **VE2 — Embedding-Store (local-first)**: Tabellen `frame_embeddings`/`segment_embeddings` (Vektor=BLOB) + `VectorStore`-Protocol mit Local-Backend (numpy brute-force cosine). Qdrant-Backend später, nicht jetzt.
- **VE3 — Segment-Embeddings + Dedup**: gewichtetes mean-pool (visual + transcript-emb + audio/prosody + hook-frame) über jedes Kandidaten-Segment; Cosine-Dedup (gleiche Szene nicht mehrfach als Short).
- **VE4 — Visual-Features in CutScore (erweitert S3)**: `visual_shift=1−cos(before,after)`, `visual_continuity`, `hook_visual_strength` (cos zu Hook-Text via CLIP-Text-Encoder), `duplicate_penalty`. Graceful: ohne Embeddings neutral.
- **VE5 — Visuelle MCP-Tools** (Teil S7): `search_visual_moments(video,query)`, `get_similar_segments`, `score_visual_hook`, `validate_crop(format)`, `deduplicate_shorts` — auf VectorStore + CLIP-Text-Query. „Queryable Visual Timeline" für den Agent.
- **VE6 — Face-aware 9:16-Crop-Validation** (`visual`-Extra: MediaPipe/YOLO): Crop-Controller hält Gesicht/Objekt im 9:16-Frame mit Glättung; QA „bbox in Safe-Area".

- **VERIFY** je Task: ruff+mypy+pytest (Backend), tsc+vitest (Frontend). Am Ende: Whole-Branch-Review + an „watch" verifizieren.

## Definition of Done (Loop-Ende)
Ein AutoGen-Agent kann über MCP-Tools: ein Video analysieren → Short-Kandidaten generieren + scoren → einen Short rendern (9:16, Captions, Loudness) → QA prüfen → mit Explainability begründen — alles transkript-sicher, frame-genau, local-first. Dann ist der MVP ein professioneller agent-steuerbarer Cutter.
