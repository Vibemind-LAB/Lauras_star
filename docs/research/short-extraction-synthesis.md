# Auto-Shorts-Extraktion — finale Synthese (zwei unabhängige Deep-Researches)

- **Datum:** 2026-06-24
- **Quellen:**
  - [short-extraction-deep-research.md](short-extraction-deep-research.md) — meine Multi-Agent-Research, **geerdet in Lauras echtem Code**.
  - [short-extraction-chatgpt-research.md](short-extraction-chatgpt-research.md) — externe ChatGPT-Deep-Research (web-zitiert, ungeerdet).
- **Status:** Deep Research abgeschlossen. Dies ist das maßgebliche Synthese-Dokument.

## Konfidenz: zwei unabhängige Researches konvergieren

Beide kamen — getrennt — zur gleichen Architektur. Das hebt die folgenden Punkte von „Meinung" auf „belastbar":

- **Hybrider multimodaler Cutter, KEIN monolithisches End-to-End-Modell.**
- **Deterministische Medien-Tools unten + LLM/Agent-Planer oben** — „Modell schlägt vor, Code entscheidet"; Frame-Genauigkeit/Transkript-Sicherheit nie an ein LLM delegieren.
- **Kern-Stack:** TransNetV2 + PySceneDetect + faster-whisper + WhisperX + Silero VAD + FFmpeg.
- **AutoShot** = Research-Comparator, nicht Produktionskern (Packaging + Baidu-Gewichte).
- **CutScore-Funktion** (beide nennen praktisch dieselbe Formel: visual + audio-silence + transcript + semantic − word-interrupt − audio-jump − face/motion) + **DP/Graph-Search** für die Segmentwahl.
- **Robust-Normalisierung** (Median/MAD), Gewichte erst manuell → dann **pairwise gelernt** (Learning-to-Rank), bootstrappt aus akzeptierten Cuts.
- **QA:** `blackdetect`/`freezedetect` + Loudness-Normalisierung; „kein Wort zerschnitten" als harte Regel.

## Was die ChatGPT-Research zusätzlich beiträgt (übernehmenswert)

- **Lighthouse** (QVHighlights/Moment-Retrieval-Toolkit, Apache-2) als konkreter Hook/Highlight-Layer — präziser als mein generisches „Embeddings + VLM-Scoring".
- **pyloudnorm** (ITU-R BS.1770-4 in Python) zusätzlich zu `loudnorm` für Loudness-Messung/QA.
- **Golden-Set-CI-Harness:** 30–50 problematische Creator-Videos + 10–20 AI-Roughcuts + multilingual/Kamera-Bewegung/schlechte-Audio-Fälle als **Medien-Regression** pro Commit (cut-F1, boundary-offset, word-interruption-rate, audio-jump, semantic-relevance). Sehr wertvoll — sollten wir aufbauen.
- **Reframing/Crop-Controller** (face-bbox + mouth-activity + screen-text + semantic-relevance, mit Glättung) für 9:16/1:1/16:9 statt Center-Crop.
- **Voice-Repair/Cloning-Vorsicht:** nur eigene/lizenzierte Stimmen mit Einwilligung — deckt sich mit Lauras Consent-Gate + EU-AI-Act.
- **Confidence-Gate + HITL-Review-Inbox** (≈80 % Vollautomatik / 15 % one-click / 5 % manuell) + **Explainability-MCP-Tools** (`analyze_video`, `generate_cut_candidates`, `score_short_variants`, `render_short`, `run_export_qa`, `request_human_review`, `explain_decision`), jedes mit Explainability-Payload.

## Wo sie sich unterscheiden — aufgelöst, geerdet in Laura

- **Cloud-Stack (Redis / Supabase / Qdrant / n8n):** Die ChatGPT-Research nimmt sie als gegeben (sie kennt Lauras Code nicht). **Geerdete Auflösung: für den Desktop ablehnen** — Laura ist local-first FastAPI + SQLite + lokaler Job-Runner (ADR-0003); diese Komponenten gehören nur ins optionale `server`/on-prem-Profil. (Genau deshalb war die code-geerdete Research hier wertvoller als ein generischer Survey.)
- **Montreal Forced Aligner:** ChatGPT empfiehlt MFA als Final-Pass für High-Value-Exports; meine Research sagt „skip by default" (conda/Kaldi-Footprint kollidiert mit uv/local-first; WhisperX ±50 ms reicht meist). **Auflösung:** WhisperX als Default, **MFA optional als opt-in Final-Precision-Extra** für Hero-Deliverables — nicht im Kern.

## Finale Roadmap

**MVP** (auf Lauras vorhandenem deterministischen Rückgrat):
- 5 Adds: `analysis/shorts_segments.py` (Kandidaten nur auf Satz-/Sprecher-Grenzen), `analysis/shorts_score.py` (multimodaler Scorer), `analysis/shorts_qa.py` (black/freeze + `pct_mid_word==0`), `shorts.extract`-Job + `shorts_candidates`-Tabelle, **Silero VAD + `vad_filter=True` in `asr.py`**.
- **Sofort-Fixes (höchster ROI, niedrigster Aufwand):** Schwarz-Filter auf **flag-and-snap** (statt alle Shots verwerfen) + `vad_filter=True` (killt Whisper-Halluzinationen).

**Stabilisierung:**
- Golden-Set-QA-Harness (Medien-Regression pro Commit), pyloudnorm-Loudness-Gate, Confidence-Gate + HITL-Review-Inbox, Explainability-Payload pro Cut.

**Upgrade:**
- Lighthouse/CLIP-Hook-Ranking; LLM-Boundary-*Vorschlag* (per Transkript-Referenz, frame-genau gesnappt); face-aware Reframing-Controller; Diversity-Penalty für Multi-Varianten.

**Agentic-Schicht:**
- MCP-Tools (Laura hat ein `mcp`-Extra) — der Agent plant/parametrisiert/entscheidet unter Konfidenzregeln, nie frame-genau.

## Bottom Line

Beide Researches sagen dasselbe, und es passt zu Laura: **Laura ist ~70 % einer hochwertigen Auto-Shorts-Pipeline.** Der Weg ist nicht „ein großes Modell", sondern die vorhandenen deterministischen Bausteine um einen **multimodalen CutScore + DP-Segmentwahl + Hook-Ranking** zu ergänzen, mit einem **Agenten als Planer** und **Confidence-Gate/HITL** für die Fälle, die noch Mensch brauchen.
