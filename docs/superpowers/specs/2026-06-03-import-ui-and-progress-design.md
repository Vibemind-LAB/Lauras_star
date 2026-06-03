# Import-UI mit echtem Download-Fortschritt — Design

- **Datum:** 2026-06-03
- **Status:** Entwurf (zur Review)
- **Betrifft:** `services/local-api` (Fortschritt-Backend) + `apps/desktop` (Import-UI)
- **Baut auf:** [`2026-06-03-resilient-url-ingest-design.md`](2026-06-03-resilient-url-ingest-design.md) und [`2026-06-03-segmented-download-and-aria2-engine-design.md`](2026-06-03-segmented-download-and-aria2-engine-design.md) (beide implementiert)

## Kontext & Problem

Der URL-/Datei-Ingest existiert vollständig im Backend (segmentierter httpx-Download +
optionale aria2-Engine + Korruptionsprüfung), aber die Desktop-App kann ihn nur über
einen einzelnen „+ Import"-Button mit Datei-Dialog auslösen (`window.laura.pickMediaFile`
→ `importAsset(source_path)`). Es fehlen: Drag&Drop, URL-Import, Ordner-Batch — und vor
allem **sichtbarer Fortschritt**. Bei 30-GB-Downloads über wackeliges Netz ist „bewegt
sich das noch / wie lange noch?" der zentrale UX-Bedarf, und das Backend gibt aktuell
keinen Byte-Fortschritt nach außen (das Asset ist `online=false` bis fertig).

## Ziel

Eine Import-UI mit vier Wegen — **Dateien per Drag&Drop, URL einfügen/tippen, Link per
Drag&Drop, Ordner/Mehrfach-Batch** — und **echten Fortschrittsbalken** (MB/%, Speed, ETA)
pro Import, gespeist aus einem neuen Backend-Endpoint.

## Entscheidungen (aus dem Brainstorming)

1. **Alle vier Import-Wege.**
2. **Echte Byte-Fortschrittsbalken** (nicht nur grobe Phasen) → Backend persistiert
   Fortschritt für **beide** Engines.
3. **aria2 per Streaming:** `aria2_download` von blockierendem `subprocess.run` auf
   `Popen` + `--summary-interval=1` umstellen, Fortschrittszeilen parsen.
4. **Transport: Polling** (~1 s), wie die App es heute schon für den Import-Abschluss tut
   — kein SSE/WebSocket (YAGNI).
5. **Retry-Button** für fehlgeschlagene Importe ist im Scope.

### Nicht-Ziele (YAGNI)

- Kein SSE/WebSocket, kein Pause/Resume-Button pro Task in der UI (der Job resumed
  ohnehin bei Retry), keine globale Download-Queue-Verwaltung mit Prioritäten.

## Umsetzung in zwei Phasen

Die UI (Phase 2) braucht den Progress-Endpoint aus Phase 1. **Phase 1 zuerst**, sie ist
eigenständig testbar/mergebar.

---

## Phase 1 — Backend: Fortschritt sichtbar machen

### Datenhaltung
- **Migration `0002_job_progress.sql`:** Spalte `progress_json TEXT` auf Tabelle `jobs`
  (nullable). Hält das letzte Fortschritts-Sample des Fetch-Jobs.

### Fortschritts-Quelle pro Engine
- **httpx (segmentiert):** `download_resumable` ruft `on_progress(downloaded, total)`
  bereits auf. Der Fetch-Handler umschließt das mit einem **gedrosselten Writer**
  (≈1×/s), der `{downloaded, total, speed_bps}` ins `progress_json` des laufenden Jobs
  schreibt. Speed = gleitender Mittelwert aus (Δbytes / Δt).
- **aria2:** `aria2_download` wird umgebaut:
  - statt `subprocess.run(...)` → `subprocess.Popen(..., stdout=PIPE, text=True)` mit
    `--summary-interval=1 --console-log-level=warn`,
  - Zeilen lesen; aria2 gibt periodische Zusammenfassungen wie
    `[#abcd 1.2GiB/30GiB(4%) CN:16 DL:5.0MiB ETA:1h]`. Ein kleiner Parser
    (`_parse_aria2_progress(line) -> (downloaded, total, speed_bps) | None`) extrahiert
    die Werte und ruft denselben `on_progress`-Callback,
  - `aria2_download(url, dest_dir, *, filename=None, opts=None, on_progress=None)` —
    `on_progress` ist neu und optional (Rückwärtskompatibilität: bestehende Tests rufen
    ohne auf).
  - Rückgabewert (Liste der Dateien) und Fehlerverhalten (`Aria2Error` bei Exit ≠ 0)
    bleiben gleich; bei Exit ≠ 0 wird der Tail der gesammelten Ausgabe verwendet.

### Endpoint
`GET /assets/{asset_id}/import-status` → `ImportStatusOut`:
```
{ phase, downloaded_bytes, total_bytes, speed_bps, eta_seconds, error }
```
- **Phase-Ableitung** (rein lesend, aus vorhandenem Zustand):
  - kein Fetch-Job für das Asset und `asset.online` → `ready`
  - Fetch-Job `queued`/`leased` ohne Progress → `queued`
  - Fetch-Job `running`/`leased` mit Progress → `downloading` (Werte aus `progress_json`)
  - Download fertig, Probe/Proxy-Jobs laufen noch (Asset online, aber Proxy/Waveform
    fehlen) → `verifying`/`analyzing` (anhand vorhandener `asset_files` abgeleitet)
  - alle Artefakte da → `ready`
  - Fetch-Job `failed` (Versuche erschöpft) → `error`, `error` = Grund aus dem
    `integrity`-`asset_file` (falls vorhanden) bzw. Job-`error_json`.
- `eta_seconds` = `(total-downloaded)/speed_bps`, wenn beides bekannt, sonst `null`.

### Retry
`POST /assets/{asset_id}/import-retry` → enqueued den `ingest.fetch`-Job erneut
(Idempotency-Key `fetch:{asset_id}`; der Runner setzt einen `failed`-Job mit gleichem Key
neu auf, httpx resumed über `.part`/Segmente, aria2 über seine Control-Datei). Nur
erlaubt, wenn der aktuelle Status `error` ist (sonst `409`).

---

## Phase 2 — Frontend: Import-UI (`apps/desktop`)

### Preload-Bridge (`preload.ts`, `main.ts`) erweitern
- `pathForFile(file: File): string` — über `webUtils.getPathForFile` (Electron 33; `File.path`
  ist entfernt). Wird für gedroppte Dateien gebraucht.
- `pickMediaFiles(): Promise<string[]>` — Mehrfach-Auswahl (ergänzt das bestehende
  Einzel-`pickMediaFile`).
- `pickFolder(): Promise<string | null>` und `listMediaInFolder(path): Promise<string[]>`
  — der **Main-Prozess** enumeriert Mediendateien (Renderer hat kein `fs`); Filter über
  bekannte Endungen (`.mp4/.mov/.mkv/.wav/...`).

### API-Client (`api.ts`)
- `importAssetFromUrl(projectId, url): Promise<ImportAccepted>` — POST mit `source_url`.
- `getImportStatus(assetId): Promise<ImportStatus>` — GET `import-status`.
- `retryImport(assetId): Promise<void>` — POST `import-retry`.
- Bestehendes `importAsset(projectId, sourcePath)` bleibt.

### Komponenten (klein, fokussiert)
- **`DropZone`** — fensterweites Overlay, erscheint bei `dragenter`. Klassifiziert den
  Drop über `dataTransfer`:
  - Dateien → Pfade via `pathForFile` → `importAsset` je Datei,
  - Ordner (ein `DataTransferItem` mit Directory-Entry) → `pickFolder`-Pfad bzw.
    Drop-Pfad → `listMediaInFolder` → je Datei importieren,
  - `text/uri-list`/`text/plain` mit URL → `importAssetFromUrl`.
- **`ImportBar`** — in der linken Sidebar: URL-Eingabefeld (+ Einfügen/Enter) und Buttons
  „+ Datei(en)" (`pickMediaFiles`) / „+ Ordner" (`pickFolder`).
- **`ImportProgress`** — pro Asset in der Liste, solange nicht `ready`: Balken +
  Prozent + `5,0 MiB/s · ETA 12:30` + Phasen-Label. Bei `error`: Grundtext + **Retry**.
- Ein `useImportStatus(assetId)`-Hook pollt `getImportStatus` (~1 s), stoppt bei
  `ready`/`error`.

### Batch
Mehrere Dateien oder ein Ordner → N Einzel-Importe; jedes Asset bekommt seine eigene
`ImportProgress`-Zeile. (Mehrdatei-Torrents erzeugen serverseitig bereits mehrere Assets
— die tauchen via Projekt-Asset-Liste auf.)

## Datenfluss

```
Drop/Paste/Pick → (Datei → source_path | URL → source_url) → import → asset_id
   → useImportStatus pollt GET /assets/{id}/import-status (~1 s)
   → Balken (downloaded/total, speed, eta) bis phase=ready
   → bei phase=error: Grund + Retry (POST import-retry)
```

## Fehler & Edge-Cases

- **Korrupt/abgebrochen** → `phase=error` + Grund (aus bestehender `integrity.json`-Logik);
  Retry vorhanden.
- **Torrent-Link ohne `aria2c`** → der Fetch-Job schlägt mit klarer Meldung fehl
  („aria2c required …"); die UI zeigt sie im Error-Status.
- **Drop gemischter Inhalte** (Dateien + URL gleichzeitig) → jede Quelle einzeln
  importieren.
- **Doppel-Import derselben Datei** → eigenes Asset je Import (bestehendes Verhalten;
  keine Dedupe in dieser Iteration).

## Tests

### Backend (pytest)
- `_parse_aria2_progress` Unit-Test mit echten aria2-Ausgabezeilen (gemockt) → korrekte
  `(downloaded, total, speed)`-Extraktion; robuste `None`-Rückgabe bei Nicht-Progress-Zeilen.
- Progress-Persistenz: ein gemockter `download_resumable`, der `on_progress` aufruft,
  schreibt erwartete `progress_json`-Werte (gedrosselt).
- Endpoint-Phasen: Asset+Job in definierten Zuständen anlegen → `import-status` liefert
  `queued/downloading/verifying/ready/error` korrekt (inkl. ETA-Rechnung).
- Retry: `error`-Zustand → `import-retry` enqueued einen Fetch-Job; falscher Zustand → `409`.

### Frontend (Vitest + Testing Library)
- `DropZone` klassifiziert Files / Ordner / `text/uri-list` korrekt und ruft die richtige
  Import-Funktion (Bridge gemockt).
- `ImportProgress` rendert Balken/Speed/ETA aus Status-Props; `error` zeigt Grund + Retry.
- `api.ts`: `importAssetFromUrl`/`getImportStatus`/`retryImport` senden die richtigen
  Requests (fetch gemockt).
- `useImportStatus` stoppt das Polling bei `ready`/`error`.

## Offene Punkte

- Genaues aria2-Ausgabeformat je Version: der Parser muss tolerant sein (mehrere
  Einheiten `KiB/MiB/GiB`, fehlende ETA) und bei Unparsebarem `None` liefern, statt zu
  werfen.
- Drossel-Intervall für `progress_json`-Writes (Vorschlag 1 s) final festzurren.
