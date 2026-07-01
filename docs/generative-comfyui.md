# Generative Videos via ComfyUI (LTX-Video)

Laura kann generierte Clips (Text-to-Video) über ein **lokales ComfyUI** in den Editorial-Pool
holen: `POST /projects/{id}/generate-video` erzeugt einen Clip und registriert ihn als
`synthetic`-Asset (`ai_effect="generate_video"`) — danach wird er wie jeder Import geschnitten.

Ohne Konfiguration läuft ein **modellfreier Stub** (einfarbiger Platzhalter-Clip via ffmpeg), damit
die Pipeline auch ohne GPU/Modell funktioniert. Für echte Generierung zeigst du Laura auf dein
ComfyUI.

## Konfiguration

Zwei Umgebungsvariablen aktivieren den echten Backend (sonst → Stub):

| Variable | Bedeutung |
|----------|-----------|
| `LAURA_COMFYUI_URL` | Basis-URL des laufenden ComfyUI, z. B. `http://127.0.0.1:8188` |
| `LAURA_COMFYUI_WORKFLOW` | Pfad zu deinem exportierten Workflow-JSON (API-Format) |

Fehlt/unlesbar der Workflow, fällt Laura **auf den Stub zurück** (bricht nie ab; Warnung im Log).

## Workflow exportieren (dein exakter LTX-Graph)

Der Node-Graph ist setup-spezifisch — Laura hardcodet ihn **nicht**, sondern lädt deinen. So
kommst du ran:

1. In ComfyUI deinen funktionierenden **LTX-Video Text-to-Video** Graph bauen (auf der RTX validiert).
2. Settings → **Enable Dev mode Options** aktivieren.
3. **Save (API Format)** → das ergibt das JSON, das ComfyUI's `/prompt`-Endpoint erwartet.
4. Diese Datei als `LAURA_COMFYUI_WORKFLOW` hinterlegen.

## Platzhalter (Prompt + Länge injizieren)

Laura ersetzt vor dem Submit zwei Platzhalter im Workflow — trag sie an den passenden Node-Inputs
deines Graphen ein:

- **`%PROMPT%`** — als *Substring* in einem Text-Feld (z. B. der positive Prompt eines
  `CLIPTextEncode`), z. B. `"text": "%PROMPT%, cinematic"`.
- **`%FRAMES%`** — als *ganzer String-Wert* dort, wo die Frame-Anzahl steht (z. B.
  `"num_frames": "%FRAMES%"`); wird durch die Ganzzahl `duration_frames` ersetzt.

Das Output-File (erstes Video/GIF/Bild der Output-Nodes) wird über `/view` heruntergeladen und als
Asset registriert.

## Nicht verifizierbar im Sandbox

Der echte ComfyUI-Roundtrip ist **manuell zu prüfen** (braucht ein laufendes ComfyUI). Die
HTTP-Logik (submit → poll → download) ist gegen einen Fake-Server getestet
(`tests/test_comfyui_client.py`, `tests/test_comfyui_backend.py`).
