# Laura AI Runtime Sidecars

Diese Images kapseln die schweren Persona-Modelle ausserhalb des Laura-Kerns. Der
FastAPI-Prozess importiert keine Torch/CUDA/LivePortrait/VibeVideo/Voice-Libs,
sondern spricht nur den HTTP-Vertrag dieser Sidecars.

## Images

- `laura-runtime-liveportrait:local` auf Port `8899`, Effekt `reenact`
- `laura-runtime-vibevideo:local` auf Port `8901`, Effekt `lipsync`
- `laura-runtime-voice:local` auf Port `8898`, Effekt `voice`

Alle drei Images nutzen denselben stdlib-Server in `runtime_server.py`. Die
eigentlichen Modell-Bruecken liegen in `providers/` und importieren keine
Modell-Libs beim Serverstart.

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
Konfiguration gesetzt, nicht in Git gespeichert. Compose setzt bereits sinnvolle
Provider-Commands; echte Repos/Gewichte muessen lokal gemountet werden.

### Erwartetes Modell-Layout

Standard-Mount:

```text
workspace/models/
  voice/
    piper/
      <piper voice files>
  liveportrait/
    LivePortrait/
      inference.py
      animations/
  vibevideo/
    MuseTalk/
      scripts/
      models/
        musetalkV15/
          unet.pth
          musetalk.json
```

Alternativ kann `LAURA_MODELS_ROOT` auf einen externen Modellordner zeigen.

### LivePortrait

Endpoint: `POST /reenact`

Multipart-Files:

- `portrait`
- `driving`

Command-Env:

```text
LAURA_LIVEPORTRAIT_COMMAND="python -m providers.liveportrait_runner --portrait {portrait} --driving {driving} --output {output} --model-root {model_root}"
```

Verfuegbare Platzhalter: `{portrait}`, `{driving}`, `{output}`, `{model_root}`,
`{fps_num}`, `{fps_den}`.

Provider-Env:

```text
LAURA_LIVEPORTRAIT_REPO=/models/LivePortrait
LAURA_LIVEPORTRAIT_OUTPUT_GLOB=animations/*.mp4
LAURA_LIVEPORTRAIT_EXTRA_ARGS=--flag_crop_driving_video
```

Der Runner ruft im Repo `python inference.py -s <portrait> -d <driving>` auf
und kopiert das neueste MP4 aus `animations/*.mp4` nach Lauras `{output}`.
Das entspricht dem offiziellen LivePortrait-CLI-Vertrag; die Quelle bleibt ein
lokaler Clone/Gewichtsordner.

### VibeVideo / Lipsync

Endpoints: `POST /probe`, `POST /lipsync`

Multipart-Files:

- `video`
- `audio`

Command-Env:

```text
LAURA_VIBEVIDEO_COMMAND="python -m providers.musetalk_runner --video {video} --audio {audio} --output {output} --model-root {model_root}"
LAURA_VIBEVIDEO_PROBE_COMMAND=""
```

Alternativ wird `LAURA_LIPSYNC_COMMAND` gelesen.

Verfuegbare Platzhalter: `{video}`, `{audio}`, `{output}`, `{model_root}`,
`{fps_num}`, `{fps_den}`. Fuer Probe-Commands zusaetzlich `{probe_json}`.

Provider-Env:

```text
LAURA_MUSETALK_REPO=/models/MuseTalk
LAURA_MUSETALK_RESULT_DIR=/workspace/musetalk-results
LAURA_MUSETALK_VERSION=v15
LAURA_MUSETALK_OUTPUT_GLOB=**/*.mp4
LAURA_MUSETALK_EXTRA_ARGS=--skip_save_images
```

Der Runner schreibt eine temporare MuseTalk-Inferenz-YAML mit `video_path` und
`audio_path`, ruft `python -m scripts.inference ...` im MuseTalk-Repo auf und
kopiert das neueste Resultat-MP4 nach Lauras `{output}`. `POST /probe` nutzt
optional `LAURA_VIBEVIDEO_PROBE_COMMAND`; ohne Probe-Command bleibt die leichte
Input-Presence-Probe aktiv.

### Voice

Endpoint: `POST /voiceover`

JSON-Body: `text`, `duration_frames`, `fps_num`, `fps_den`, `sample_rate`,
optional `language`.

Command-Env:

```text
LAURA_VOICE_COMMAND="python -m providers.piper_voice_runner --request {request_json} --output {output} --model-root {model_root}"
```

Alternativ wird `LAURA_VOICEOVER_COMMAND` gelesen.

Verfuegbare Platzhalter: `{request_json}`, `{output}`, `{model_root}`,
`{duration_frames}`, `{fps_num}`, `{fps_den}`, `{sample_rate}`.

Provider-Env:

```text
LAURA_PIPER_VOICE=en_US-lessac-medium
LAURA_PIPER_DATA_DIR=/models/piper
LAURA_PIPER_EXTRA_ARGS=--sentence-silence 0.05
```

Der Runner liest Lauras `request.json`, extrahiert `text` und ruft Pipers CLI
als `python -m piper -m <voice> -f <output> -- <text>` auf. Piper selbst wird
im Modell-/Sidecar-Environment installiert, nicht im Laura-Core.

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

Fuer Modellmodus:

```powershell
.\scripts\register-ai-sidecars.ps1 -ApiUrl http://127.0.0.1:8765 -Mode model -LicenseStatus accepted
```

Optional koennen Commands ueberschrieben werden:

```powershell
.\scripts\register-ai-sidecars.ps1 `
  -Mode model `
  -VoiceCommand "python -m providers.piper_voice_runner --request {request_json} --output {output} --model-root {model_root}" `
  -LivePortraitCommand "python -m providers.liveportrait_runner --portrait {portrait} --driving {driving} --output {output} --model-root {model_root}" `
  -VibeVideoCommand "python -m providers.musetalk_runner --video {video} --audio {audio} --output {output} --model-root {model_root}"
```

## Gewichte und Lizenzen

Keine Modellgewichte, gated Repositories oder Lizenzartefakte gehoeren in Git.
Lege Modellcode und Gewichte lokal unter `workspace/models/<runtime>` oder einem
externen Modellpfad ab und mounte diesen Pfad als `/models`.

Referenzen, die fuer die Default-Provider verwendet wurden:

- LivePortrait: offizieller Clone/Inference-Pfad `python inference.py -s ... -d ...`.
- MuseTalk: offizieller Inference-Pfad `python -m scripts.inference --inference_config ...`.
- Piper: offizieller CLI-Pfad `python -m piper -m <voice> -f <wav> -- <text>`.
