# 06 — Storage-Layout

Strukturiert für lokale Verstehbarkeit, Backup und optionale Cloud-Sync. Root = `workspace/`
(gitignored). OTIO ist Timeline-Truth; Sidecar-/Cache-Dateien sind ableitbar/reproduzierbar.

```text
workspace/
  project-{project_id}/
    originals/
      {asset_id}/source.ext
    proxies/
      {asset_id}/proxy-1080p-intra.mov
    audio/
      {asset_id}/mono-16k.wav          # für ASR/Alignment
      {asset_id}/mix-48k.wav           # für Playback/Waveform
    waveforms/
      {asset_id}/waveform.json         # UI-Peaks (downsampled)
      {asset_id}/peaks.bin             # rohe Peak-Daten
    analysis/
      {asset_id}/
        manifest.json                  # welche Stufen, welche pipeline_version, Hashes
        shots.json
        scenes.json
        transcript.segments.json
        transcript.words.json
        speakers.json
        embeddings.json
        thumbnails/
    timelines/
      {timeline_id}.otio.json          # kanonisch
      {timeline_id}.cache.json         # materialisierte Clips / UI-Cache
    exports/
      {export_id}/
        timeline.otio
        timeline.fcpxml
        timeline.fcp7.xml
        timeline.edl
        captions.srt
        captions.vtt
        diagnostics.json
    cache/
      decode/
      thumbnails/
      search/
```

## Regeln

- **Atomare Writes:** immer in Temp schreiben, dann `rename` (crash-safe, Reopen-fest).
- **Reproduzierbarkeit:** Alles unter `analysis/`, `proxies/`, `waveforms/`, `cache/`, `exports/`
  ist aus Originalen + `pipeline_version` neu erzeugbar. Backup-Minimum = `originals/` + DB +
  `timelines/*.otio.json`.
- **Pfad-Integrität:** Medienpfade können sich ändern (externe Disks). Asset trägt `sha256`;
  beim Reopen Pfad-Relink über Checksum, nicht über absoluten Pfad.
- **Manifest:** `analysis/{asset_id}/manifest.json` listet je Stufe `pipeline_version`,
  Input-Hash, Output-Hash, Zeitstempel → Idempotenz & Audit.
- **Optionaler Objektstore:** Layout mappt 1:1 auf S3/MinIO/NAS-Prefixes (Collaboration Plane).

## Vorbild

Premiere nutzt für lokale Medienanalyse ebenfalls Sidecar-/Cache-Artefakte und hält Analyse +
Suche lokal. Genau dieser Charakter ist hier gewünscht (local-first, kein Hidden Upload).

## URL-Ingest: Downloads

Per URL importierte Quellen werden vor der Probe nach
`workspace/project-{project_id}/downloads/{asset_id}/<dateiname>` geladen. Während des Downloads
existiert eine `<dateiname>.part`-Datei; sie wird erst nach bestandener Größen-/Decode-
Prüfung atomar zur finalen Datei. Schlägt die Prüfung endgültig fehl, bleibt das Asset
`online=false` und ein `integrity.json` (als `asset_file` kind `integrity`) hält den
Grund fest.

**Google Drive:** Kein Confirm-Token-Handling. Für große Drive-Dateien den direkten
`googleusercontent`-Link verwenden (umgeht die „Virenscan"-Bestätigungsseite, unterstützt
Resume).

**Manueller „kaputtes Netz"-Test:** Einen impairing Proxy (toxiproxy, cross-platform) oder
`clumsy` (Windows) zwischen Service und Quelle schalten und Latenz/Bandbreitenlimit/
Verbindungsabbrüche injizieren. Headless nicht automatisierbar → manuell zu prüfen.

### Engines

HTTP(S)-Downloads laufen über die eigene httpx-Engine mit **Multi-Connection-
Segmentierung** (parallele Range-Requests, Resume pro Segment, Fallback auf Single-Stream
wenn der Server kein Range unterstützt). Zahl der Verbindungen: Env
`LAURA_DOWNLOAD_CONNECTIONS` (Default 8); ab `LAURA_DOWNLOAD_MIN_SEGMENT_BYTES`
(Default 8 MiB) wird segmentiert.

Torrent/Magnet/FTP/Metalink laufen über das optionale Binary **`aria2c`** (One-shot,
als separater Prozess aufgerufen — GPL bleibt arm's length, wie bei ffmpeg). Fehlt
`aria2c`, sind nur diese Protokolle nicht verfügbar; HTTP(S) funktioniert voll. Ein
Mehrdatei-Torrent erzeugt **ein Asset pro Mediendatei**; Nicht-Mediendateien (`.nfo`
etc.) werden ignoriert.
