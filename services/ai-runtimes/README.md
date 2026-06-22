# Laura AI Runtime Sidecars

Diese Images kapseln die schweren Persona-Modelle ausserhalb des Laura-Kerns. Der
FastAPI-Prozess importiert keine Torch/CUDA/LivePortrait/VibeVideo/Voice-Libs,
sondern spricht nur den HTTP-Vertrag dieser Sidecars.

## Images

- `laura-runtime-liveportrait:local` auf Port `8899`, Effekt `reenact`
- `laura-runtime-vibevideo:local` auf Port `8901`, Effekt `lipsync`
- `laura-runtime-voice:local` auf Port `8898`, Effekt `voice`
- `laura-runtime-liveportrait-model:local` optionales CUDA-Image fuer echte LivePortrait-Ausgabe
- `laura-runtime-musetalk-model:local` optionales CUDA-Image fuer echte MuseTalk-Ausgabe

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

In `model` mode ist eine Runtime nur ready, wenn ein Modell-Command gesetzt ist,
`LAURA_MODEL_ROOT` existiert und die runtime-spezifischen Pflichtartefakte
vorhanden sind. `/healthz` und `/capabilities` liefern `required_model_paths`
und `missing_model_paths`, damit falsche Mounts oder unvollstaendige Downloads
vor dem ersten Job sichtbar werden. Die Commands werden als lokale Container-
Konfiguration gesetzt, nicht in Git gespeichert. Compose setzt bereits sinnvolle
Provider-Commands; echte Repos/Gewichte muessen lokal gemountet werden.

### Erwartetes Modell-Layout

Standard-Mount auf dieser Windows-Workstation ist `E:\Laura\models` (via
`scripts/ai-runtimes.ps1` und `scripts/setup-ai-runtime-models.ps1`). Auf
Hosts ohne `E:` fallen die Skripte auf `workspace/models` zurueck. Direkte
`docker compose`-Aufrufe koennen denselben Pfad ueber `LAURA_MODELS_ROOT`
setzen.

```text
E:\Laura\models\
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
        dwpose/
        face-parse-bisent/
        musetalkV15/
          unet.pth
          musetalk.json
        sd-vae/
        syncnet/
        whisper/
```

Alternativ kann `LAURA_MODELS_ROOT` auf einen externen Modellordner zeigen.
Wenn ein Provider ein anderes Layout nutzt, kann `LAURA_MODEL_REQUIRED_PATHS`
im Container gesetzt werden. Mehrere relative oder absolute Pfade werden mit
Komma oder Semikolon getrennt; ein leer gesetzter Wert deaktiviert die
Pflichtartefakt-Liste fuer diesen Runtime-Container.

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
LAURA_LIVEPORTRAIT_OUTPUT_GLOB=*.mp4
LAURA_LIVEPORTRAIT_EXTRA_ARGS=--flag_crop_driving_video
```

Der Runner ruft im Repo `python inference.py -s <portrait> -d <driving> -o <tmp>/animations`
auf und kopiert das neueste MP4 aus dieser temporaren Output-Dir nach Lauras `{output}`.
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
LAURA_MUSETALK_EXTRA_ARGS=--use_float16
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
.\scripts\ai-runtimes.ps1 -Action build
.\scripts\ai-runtimes.ps1 -Action up
```

Mit GPU-Override:

```powershell
.\scripts\ai-runtimes.ps1 -Action up -Gpu
```

Mit echten Modell-Images:

```powershell
.\scripts\setup-ai-runtime-models.ps1 -Runtime all
.\scripts\ai-runtimes.ps1 -Action build -Mode model -Gpu
.\scripts\ai-runtimes.ps1 -Action up -Mode model -Gpu
```

Git-Bash/WSL nutzt denselben Ablauf:

```bash
scripts/ai-runtimes.sh build model --gpu
scripts/ai-runtimes.sh up model --gpu
```

`setup-ai-runtime-models.ps1` laedt Modellgewichte standardmaessig unter
`E:\Laura\models\<runtime>` und setzt die Dateien nach Docker-Downloads auf
`a+rX`, damit die nicht-root Sidecar-User die Read-only-Mounts lesen koennen.
Wenn vorhanden, nutzt das Script ausserdem `E:\laura-hf-cache`,
`E:\huggingface_cache` und `E:\uv-cache` als Prozess-Caches.
`ai-runtimes.sh` bevorzugt auf Windows-Bash/WSL ebenfalls `E:\Laura\models`
(`/e/Laura/models` bzw. `/mnt/e/Laura/models`) und faellt nur ohne E:-Drive auf
`workspace/models` zurueck.

Health:

```powershell
.\scripts\ai-runtimes.ps1 -Action health -Mode model
```

Die `health`-Action gibt die drei `/healthz`-Payloads aus und bricht mit einem
Fehler ab, sobald ein Sidecar zwar antwortet, aber `ready=false` bzw. `ok=false`
meldet.

Prereq-/Doctor-Check:

```powershell
.\scripts\check-ai-runtime-prereqs.ps1
```

Der Doctor ist nicht-destruktiv und prueft:

- freie Laufwerke und den aktiven `ModelRoot`
- erwartete Modellordner fuer Piper, LivePortrait und MuseTalk
- Docker CLI, Docker Desktop Service und Docker Engine
- `docker-desktop` WSL-Distro
- Sidecar-Ports `8898`, `8899`, `8901`

Wenn `Docker Desktop Service status=Stopped` und `Docker engine ... pipe ...
not found` erscheint, muss Docker Desktop ausserhalb dieser Skriptumgebung
gestartet werden. Falls `sc start com.docker.service` mit `Zugriff verweigert`
scheitert, braucht der Service-Start Admin-/Desktop-Rechte.

## Registrierung in Laura

Wenn die lokale API auf `http://127.0.0.1:8765` laeuft:

```powershell
.\scripts\register-ai-sidecars.ps1 -ApiUrl http://127.0.0.1:8765 -Mode smoke
```

Das legt drei `container`-Runtimes an. Die Runtimes verwenden die Standardports
`8898`, `8899` und `8901`, also dieselben Defaults, die die bestehenden
Voiceover/Reenact/Lipsync-Jobhandler bereits als Sidecar-Backends nutzen.
`-WhatIf` gibt die JSON-Definitionen aus, ohne die API zu kontaktieren.

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
Lege Modellcode und Gewichte lokal bevorzugt unter `E:\Laura\models\<runtime>`
oder einem externen Modellpfad ab und mounte diesen Pfad als `/models`. Der
Repo-Fallback `workspace/models/<runtime>` ist nur fuer kleine Smoke-/Dev-Setups
gedacht.

Referenzen, die fuer die Default-Provider verwendet wurden:

- LivePortrait: offizieller Clone/Inference-Pfad `python inference.py -s ... -d ...`.
- MuseTalk: offizieller Inference-Pfad `python -m scripts.inference --inference_config ...`.
- Piper: offizieller CLI-Pfad `python -m piper -m <voice> -f <wav> -- <text>`.
