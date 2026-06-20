# Laura AI Runtime Sidecars

Diese Images kapseln die schweren Persona-Modelle ausserhalb des Laura-Kerns. Der
FastAPI-Prozess importiert keine Torch/CUDA/LivePortrait/VibeVideo/Voice-Libs,
sondern spricht nur den HTTP-Vertrag dieser Sidecars.

## Images

- `laura-runtime-liveportrait:local` auf Port `8899`, Effekt `reenact`
- `laura-runtime-vibevideo:local` auf Port `8901`, Effekt `lipsync`
- `laura-runtime-voice:local` auf Port `8898`, Effekt `voice`

Alle drei Images nutzen denselben stdlib-Server in `runtime_server.py`.

## Smoke Mode

Compose startet standardmaessig mit `LAURA_RUNTIME_MODE=smoke`. Das ist fuer
Build-, Start- und Vertragspruefungen gedacht:

- `/healthz` meldet `ready=true` und `mode=smoke`.
- Voice erzeugt eine deterministische WAV-Tonspur.
- LivePortrait gibt im Smoke-Modus den Driving-Clip zurueck.
- VibeVideo gibt im Smoke-Modus das Input-Video plus Quality-Header zurueck.

Smoke-Outputs sind keine Modelloutputs. Fuer echte Modelle `LAURA_RUNTIME_MODE=model`
setzen und den passenden Command konfigurieren.

## Model Mode

In `model` mode ist eine Runtime nur ready, wenn ein Modell-Command gesetzt ist
und `LAURA_MODEL_ROOT` existiert. Die Commands werden als lokale Container-
Konfiguration gesetzt, nicht in Git gespeichert.

### LivePortrait

Endpoint: `POST /reenact`

Multipart-Files:

- `portrait`
- `driving`

Command-Env:

```text
LAURA_LIVEPORTRAIT_COMMAND="python /models/LivePortrait/inference.py -s {portrait} -d {driving} -o {output}"
```

Verfuegbare Platzhalter: `{portrait}`, `{driving}`, `{output}`, `{model_root}`,
`{fps_num}`, `{fps_den}`.

### VibeVideo / Lipsync

Endpoints: `POST /probe`, `POST /lipsync`

Multipart-Files:

- `video`
- `audio`

Command-Env:

```text
LAURA_VIBEVIDEO_COMMAND="python /models/run_lipsync.py --video {video} --audio {audio} --output {output}"
```

Alternativ wird `LAURA_LIPSYNC_COMMAND` gelesen.

Verfuegbare Platzhalter: `{video}`, `{audio}`, `{output}`, `{model_root}`,
`{fps_num}`, `{fps_den}`.

### Voice

Endpoint: `POST /voiceover`

JSON-Body: `text`, `duration_frames`, `fps_num`, `fps_den`, `sample_rate`,
optional `language`.

Command-Env:

```text
LAURA_VOICE_COMMAND="python /models/run_voice.py --request {request_json} --output {output}"
```

Alternativ wird `LAURA_VOICEOVER_COMMAND` gelesen.

Verfuegbare Platzhalter: `{request_json}`, `{output}`, `{model_root}`,
`{duration_frames}`, `{fps_num}`, `{fps_den}`, `{sample_rate}`.

## Build und Start

Vom Repo-Root:

```powershell
docker compose -f deploy/ai-runtimes/docker-compose.yml up -d --build
```

Mit GPU-Override:

```powershell
docker compose -f deploy/ai-runtimes/docker-compose.yml -f deploy/ai-runtimes/docker-compose.gpu.yml up -d --build
```

Health:

```powershell
Invoke-RestMethod http://127.0.0.1:8898/healthz
Invoke-RestMethod http://127.0.0.1:8899/healthz
Invoke-RestMethod http://127.0.0.1:8901/healthz
```

## Registrierung in Laura

Wenn die lokale API auf `http://127.0.0.1:8765` laeuft:

```powershell
.\scripts\register-ai-sidecars.ps1 -ApiUrl http://127.0.0.1:8765 -Mode smoke
```

Das legt drei `container`-Runtimes an. Wenn die Compose-Container schon laufen,
nutzt Laura dieselben Container-Namen. Wenn sie noch nicht laufen, kann Laura sie
ueber Runtime Start starten.

## Gewichte und Lizenzen

Keine Modellgewichte, gated Repositories oder Lizenzartefakte gehoeren in Git.
Lege Modellcode und Gewichte lokal unter `workspace/models/<runtime>` oder einem
externen Modellpfad ab und mounte diesen Pfad als `/models`.
