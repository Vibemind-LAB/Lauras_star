# Laura Analysis Runtime (GPU-Worker)

Containerisierter GPU-Worker, der Lauras schwere Analyse-Modelle ausführt, damit das lokale
Backend sie **nicht auf der Host-CPU** rechnen muss (behebt den `mkl_malloc`-OOM und ist auf der
GPU deutlich schneller). Stateless HTTP + JSON, Modelle werden **lazy** geladen.

Eigener Subtree — **getrennt** von `services/ai-runtimes/` (Voice/Reenact/Lipsync).

## Phasen

| Endpoint | Modell | Status |
| --- | --- | --- |
| `POST /transcribe` | faster-whisper (ASR) | ✅ |
| `POST /scenes` | TransNetV2 (Szenen) | ✅ |
| `POST /embed` | CLIP/SigLIP (Embeddings) | ✅ |
| `GET /healthz` | — | ✅ |

> **Image-Größe:** Das Szenen-Modell (TransNetV2) zieht **torch+CUDA** (~2–3 GB) → das Image wird
> groß (~6–8 GB). ASR (CTranslate2) und Embeddings (onnxruntime) brauchen kein torch. Szenen-
> Erkennung ist **opt-in** (nur wenn `detector="transnet"` gewählt wird); der Default `adaptive`
> (PySceneDetect, CPU) läuft ohne Container weiter.

## Contract

```
GET  /healthz
  -> {"status":"ok","device":"cuda|cpu","compute_type":"float16|int8","models_loaded":[...]}

POST /transcribe?model_size=base&language=de
  Body:  WAV-Bytes (Content-Type: audio/wav)
  -> {"segments":[{"text","start_sec","end_sec","confidence",
                   "words":[{"text","start_sec","end_sec","confidence"}]}]}
```

Die JSON-Form ist **formgleich** zu `laura.analysis.types.SegmentResult`/`WordResult`, sodass
In-Process- und Sidecar-Pfad denselben DB-Mapping-Code im Backend nutzen.

## Starten

```bash
docker compose -f deploy/analysis-runtime/compose.yml up -d --build
# Backend dagegen verdrahten:
#   LAURA_ANALYSIS_URL=http://127.0.0.1:8896
```

Das Backend prüft `/healthz` und nutzt den Container nur, wenn er gesund ist — sonst transkribiert
es in-process auf der CPU (kein Container = kein Problem). Schlägt der Container mitten in einer
Anfrage fehl, fällt das Backend ebenfalls auf den lokalen Pfad zurück.

## Env

| Variable | Default | Zweck |
| --- | --- | --- |
| `LAURA_RUNTIME_PORT` | `8896` | HTTP-Port im Container |
| `LAURA_ASR_DEVICE` | (auto) | `cuda` erzwingen oder `cpu`; sonst CUDA-Autodetect |
| `LAURA_ASR_VAD` | `1` | Voice-Activity-Filter |
| `LAURA_MODELS_ROOT` | `./models` | Host-Pfad für persistente Modell-Weights (`/models`) |

## Lokal ohne Docker testen (Smoke)

```bash
cd services/analysis-runtime
pip install -r requirements.txt
LAURA_ASR_DEVICE=cpu uvicorn server:app --port 8896
curl -s localhost:8896/healthz
```
