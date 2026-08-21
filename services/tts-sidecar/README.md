# Laura TTS-Sidecar (Chatterbox)

Eigenstaendiger Chatterbox-Voiceover-Sidecar fuer Laura. Erfuellt den
`SidecarVoiceoverBackend`-Kontrakt aus
`services/local-api/src/laura/ai/voiceover_backend.py`: Laura spricht nur HTTP,
importiert nie Torch/CUDA/Chatterbox selbst. Das Skript laeuft in Chatterbox'
eigenem venv (`E:\chatterbox`) -- **kein** Eintrag in `services/local-api`s
`pyproject.toml`.

## Start-Rezept

```powershell
E:\chatterbox\.venv\Scripts\python.exe `
  C:\Users\User\Desktop\Laura\services\tts-sidecar\chatterbox_sidecar.py `
  --port 8898
```

`--host` (Default `127.0.0.1`) und `--port` (Default `8898`, passend zu
`DEFAULT_VOICEOVER_URL` in `voiceover_backend.py`) sind die einzigen CLI-Flags.

Der erste `POST /voiceover`-Request laedt `ChatterboxTTS.from_pretrained(...)`
lazy (thread-lock-geschuetzt) -- dabei werden **beim allerersten Lauf ~2GB**
Modellgewichte heruntergeladen (Cache-Ziel: `HF_HOME`). `GET /healthz`
beruehrt das Modell nicht und antwortet sofort mit `200 ok` -- das ist die
schnelle Readiness-Probe, die Lauras `SidecarVoiceoverBackend.available()`
aufruft.

## Env Vars

| Variable              | Zweck                                                                 | Default                              |
|------------------------|------------------------------------------------------------------------|----------------------------------------|
| `CHATTERBOX_VOICE_REF` | Pfad zur Referenz-WAV fuer Voice-Cloning                               | `felix_ref.wav` neben diesem Skript   |
| `CHATTERBOX_DEVICE`    | Torch-Device fuer `ChatterboxTTS.from_pretrained`                      | `cuda`                                 |
| `HF_HOME`              | HuggingFace-Cache-Verzeichnis (respektiert, nicht gesetzt vom Skript)  | HF-Standard (`~/.cache/huggingface`)   |
| `LAURA_FFMPEG`         | Optionaler expliziter ffmpeg-Pfad fuers Resampling (hoechste Prioritaet) | `ffmpeg` von PATH                    |
| `FFMPEG_BIN`           | Fallback-ffmpeg-Pfad, falls `LAURA_FFMPEG` nicht gesetzt ist            | `ffmpeg` von PATH                    |

Referenz-Aufloesung pro Request (erste existierende Datei gewinnt):
`voice_id` aus dem Payload (falls ein existierender Pfad) ->
`CHATTERBOX_VOICE_REF` -> `felix_ref.wav` neben dem Skript. Existiert keine der
drei, antwortet der Sidecar mit `500` und nennt alle drei versuchten Pfade im
Klartext-Body.

Beispiel fuer diese Workstation:

```powershell
$env:HF_HOME = "E:\huggingface_cache"
$env:CHATTERBOX_VOICE_REF = "E:\chatterbox\felix_ref.wav"
$env:CHATTERBOX_DEVICE = "cuda"
```

## Kontrakt

- `GET /healthz` -> `200` Body `ok` (kein Modell-Load).
- `POST /voiceover` JSON-Body -> `audio/wav`-Bytes.

Payload-Felder: `text`, `duration_frames`, `fps_num`, `fps_den`,
`sample_rate`, `language` (optional, nur geloggt -- Chatterbox erkennt die
Sprache selbst), `voice_id` (optional), `fit_to_slot` (optional, **Default
`false`**). Bei `fit_to_slot: true` wird der Clip zusaetzlich exakt auf den
Slot gepasst -- `apad` polstert zu kurze Sprache, `-t <duration_seconds>`
kappt zu lange, mit `duration_seconds = duration_frames * fps_den / fps_num`
(identisch zu Lauras `WindowsSapiVoiceoverBackend`/`ElevenLabsVoiceoverBackend`).
Immer resampled: mono, `sample_rate` aus dem Payload, `pcm_s16le` via
ffmpeg-Subprozess.

Jeder Fehlerfall (fehlende Referenz-WAV, Modell-Load-Fehler,
Chatterbox-Generierungsfehler, ffmpeg-Fehler, kaputtes JSON) antwortet mit
`500` und einem Klartext-Body, der die Ursache benennt -- nie ein leerer
Body, nie ein Stacktrace.

## Kopplung an Laura

```powershell
$env:LAURA_VOICEOVER_BACKEND = "sidecar"
$env:LAURA_VOICEOVER_URL = "http://127.0.0.1:8898"
```

Damit loest `resolve_voiceover_backend()` in `voiceover_backend.py` auf
`SidecarVoiceoverBackend`, das genau den oben beschriebenen Kontrakt spricht.

## Setuptools-Falle im Chatterbox-venv

`chatterbox-tts` zieht Abhaengigkeiten, die zur Laufzeit `pkg_resources`
importieren. Mit `setuptools>=81` ist `pkg_resources` aus dem Paket entfernt
-- der Import schlaegt fehl. Falls `E:\chatterbox\.venv` neu aufgesetzt
werden muss: `setuptools<81` pinnen (z.B.
`E:\chatterbox\.venv\Scripts\pip.exe install "setuptools<81"`), bevor
`chatterbox-tts` installiert/importiert wird.

## Syntax-/Typcheck (kein Modell-Lauf)

Torch/Chatterbox sind in Lauras `services/local-api`-venv nicht installiert
-- deshalb importiert `chatterbox_sidecar.py` sie nur innerhalb von
Funktionen (lazy), nie auf Modulebene. Aus `services/local-api`:

```powershell
uv run python -m py_compile ../tts-sidecar/chatterbox_sidecar.py
uv run mypy ../tts-sidecar/chatterbox_sidecar.py --ignore-missing-imports
```

Kein Pytest-Gate hier (eigenes venv, kein Teil der Laura-Suite). Der echte
Smoke-Test (Modell laden, WAV erzeugen, gegen `SidecarVoiceoverBackend`
sprechen) passiert manuell in Task 9.
