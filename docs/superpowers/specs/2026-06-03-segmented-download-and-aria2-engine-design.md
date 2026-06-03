# Segmentierter HTTP-Download + optionale aria2-Engine — Design

- **Datum:** 2026-06-03
- **Status:** Entwurf (zur Review)
- **Betrifft:** `services/local-api` — Ingest/Download
- **Baut auf:** [`2026-06-03-resilient-url-ingest-design.md`](2026-06-03-resilient-url-ingest-design.md) (resumebarer URL-Ingest, bereits implementiert)

## Kontext & Problem

Der bestehende URL-Ingest lädt eine HTTP-Datei **single-stream** (eine Verbindung,
Resume über `.part`). Das Kernproblem bleibt: bei **instabiler Leitung und großen
Dateien** (30 GB) bricht die eine Verbindung wiederholt ab — Resume hilft, ist aber
langsam und fragil. Zusätzlich fehlen Protokolle (Torrent/Magnet/FTP/Metalink), die ein
Download-Manager wie Motrix (via `aria2`) abdeckt.

Ziel: **Flaky-Netz-Robustheit für HTTP** über Multi-Connection-Segmentierung **in unserer
eigenen httpx-Engine** (sicher, verifiziert, abhängigkeitsfrei), plus eine **optionale
`aria2`-Engine** für genau die Protokolle, die httpx nicht kann.

## Entscheidungen (aus dem Brainstorming)

1. **Segmentierung in httpx** ist die HTTP-Robustheitsquelle — *nicht* HTTP über aria2
   routen (kleinere Angriffsfläche, verifizierter Pfad, keine Abhängigkeit).
2. **aria2-Anbindung: One-shot CLI** (`aria2c` als blockierender Subprozess, wie der
   bestehende ffmpeg-Wrapper) — kein RPC-Daemon.
3. **Engine-Auswahl: protokoll-basiert.** `http(s)` → segmentierte httpx-Engine;
   `magnet:`/`.torrent`/`.metalink`/`.meta4`/`ftp(s)://`/`sftp://` → aria2 (Pflicht).
4. **Mehrdatei-Torrents: ein Asset pro Mediendatei** (Fan-out im Fetch-Handler).

### Nicht-Ziele (YAGNI)

- Kein HTTP-über-aria2, kein aria2-RPC-Daemon, kein Engine-Override in der API.
- Keine Download-GUI, kein Browser-Capture, kein nutzerseitiges Queue-/Scheduling-UI.
- Throttle/Proxy für aria2 nur über Env/Config, nicht in der API.

## Architektur — Engine-Abstraktion

Ein gemeinsames Konzept, zwei Engines hinter dem Fetch-Handler:

```
ingest.fetch
  └─ select_engine(url)
       ├─ http(s)                         → download_segmented()  → 1 Datei
       └─ magnet/.torrent/.metalink/ftp   → aria2_download()      → 1..N Dateien
  └─ für jede resultierende Mediendatei: Asset zuordnen/erzeugen → verify_decode → ingest.probe
```

Die httpx-Engine liefert immer **eine** Datei; die aria2-Engine **1..N**. Der Fan-out im
Handler behandelt beide einheitlich über eine Dateiliste.

## Komponenten

### `ingest/download.py` — Segmentierung (Kernstück)

`download_resumable(...)` bleibt die öffentliche Einstiegsfunktion und bekommt einen
Parameter `connections: int = 8` (Default aus Env `LAURA_DOWNLOAD_CONNECTIONS`).

Ablauf:

1. **Range-Probe:** `GET` mit `Range: bytes=0-0`. Antwort `206` + `Content-Range:
   bytes 0-0/<total>` ⇒ Server unterstützt Range, Gesamtgröße bekannt.
2. **Fallback:** kein `206`/keine Gesamtgröße, oder `connections <= 1`, oder Datei kleiner
   als `min_segment_bytes` (z. B. 8 MiB) ⇒ **bestehender Single-Stream-Pfad** (unverändert).
3. **Segmentplan:** Gesamtgröße `S`, `N = connections`. Segmentlänge `ceil(S/N)`. Jedes
   Segment hat ein Byte-Range `[off, off+len)`.
4. **Parallel laden:** `ThreadPoolExecutor(max_workers=N)`; jeder Worker lädt sein Range
   per `Range`-Request in eine eigene Teildatei `<dest>.parts/seg-<i>` (resumebar: Worker
   setzt aus vorhandener Teilgröße per Sub-Range fort). **Retry pro Segment** (z. B. 3×)
   bei transientem Fehler.
5. **Reassembly:** Segmente in Reihenfolge zu `<dest>.part` konkatenieren.
6. **Verifikation:** Gesamtgröße gegen `S`, optional SHA-256 (bestehende Checks).
7. **Atomic promote:** `os.replace(<dest>.part, <dest>)`; `.parts/`-Ordner aufräumen.

Ein endgültig fehlgeschlagenes Segment wirft `DownloadError` → Job-Retry; bereits
vollständige Segmente bleiben liegen und werden beim Retry übersprungen (Resume auf
Segment-Ebene). httpx-`Client` wird pro Worker erstellt (thread-sicheres Senden).

### `ingest/aria2.py` (neu)

```python
def aria2_available() -> bool: ...
def aria2_download(url: str, dest_dir: Path, *, opts: Aria2Opts | None = None) -> list[Path]:
    """One-shot aria2c into dest_dir. Returns the downloaded file paths
    (HTTP/single-file torrent: 1; multi-file torrent: N). Raises Aria2Error."""
```

- Ruft `aria2c <url> -d <dest_dir> --auto-file-renaming=false --summary-interval=…`
  mit Segmentierung (`-x16 -s16`), optional `--max-overall-download-limit` (Throttle) und
  `--all-proxy` (Proxy) aus Env/Config. Kein Shell, Listen-Args (wie `ffmpeg.py`).
- aria2 verwaltet **Resume selbst** über seine `*.aria2`-Control-Datei im `dest_dir` —
  ein Job-Retry ruft `aria2c` erneut auf und setzt fort.
- **Ergebnis-Enumeration:** Nach Exit 0 werden die im `dest_dir` entstandenen Dateien
  gelistet (Control-/Metadaten-Dateien `*.aria2`, `*.torrent` ausgenommen).
- `Aria2Error` bei Exit ≠ 0 (Tail der stderr), analog `FFmpegError`.

### `ingest/integrity.py` — Mediendatei-Filter

Kleiner Helfer `is_media_file(path) -> bool`: leichter `ffprobe`-Check (Container lesbar
**und** hat einen Audio-/Video-Stream). Dient dem Aussortieren von `.nfo`/`.txt`/Samples
bei Torrents. Baut auf dem vorhandenen `ffmpeg.probe` auf.

### `ingest/handlers.py` — `handle_fetch` mit Fan-out

```
url = payload["source_url"]
engine = select_engine(url)                 # protokoll-basiert
if engine == "aria2" and not aria2_available():
    raise ValueError("aria2c required for this source but not installed")

dest_dir = workspace/.../downloads/<asset_id>/
files = engine.download(...)                # httpx: [datei]; aria2: [1..N]
media = [f for f in files if is_media_file(f)]
if not media:
    integrity.json + raise                  # → retry/fail, Asset offline

assign(media[0] -> Platzhalter-Asset)        # set_asset_source(online=true) + probe
for extra in media[1:]:
    a = repos.create_asset(project, display_name=extra.name, source_path=str(extra), online=true)
    enqueue ingest.probe für a
# verify_decode pro Datei (s. Fehlerlogik)
```

`select_engine(url)` ist eine reine Funktion (URL-Schema/Endung → `"httpx"`/`"aria2"`),
unabhängig testbar.

### `api/models.py` / `api/assets.py`

**Unverändert** gegenüber dem bestehenden URL-Ingest — `source_url` genügt, die
Engine-Wahl passiert serverseitig protokoll-basiert. Kein neues API-Feld.

## Datenfluss

```
import(source_url) → Platzhalter-Asset (online=false) → ingest.fetch
  → select_engine → download (segmentiert ODER aria2)
  → Mediendateien filtern
  → media[0] an Platzhalter; media[1:] je neues Asset; alle → verify_decode → ingest.probe
  → nicht-Medien ignoriert
```

Die API-Antwort liefert weiterhin **eine** `asset_id` (Platzhalter). Zusätzliche Assets
(Mehrdatei-Torrent) entstehen während des Jobs und erscheinen in der Projekt-Asset-Liste.

## Fehler- & Retry-Logik (pro Engine unterschiedlich)

- **httpx (segmentiert):** transienter Segment-Fehler → Segment-Retry; endgültiger Fehler →
  `DownloadError` → Job-Retry, fertige Segmente bleiben (Resume). Verify-Fehler der
  fertigen Datei → Datei verwerfen → Voll-Neuladen (bestehende Policy).
- **aria2:** Exit ≠ 0 → `Aria2Error` → Job-Retry, aria2 setzt selbst fort. **Verify pro
  Datei:** eine korrupte Mediendatei macht **nur ihr** Asset offline (eigene
  `integrity.json`), bricht den Torrent **nicht** ab; gute Dateien bleiben online.
- **aria2c fehlt, aber Protokoll braucht es** → `ValueError`, Asset bleibt offline.
- **Keine Mediendatei im Ergebnis** → Platzhalter offline + `integrity.json`.

## Tests & Verifikation

### Ebene 1 — deterministisch im Suite

- **Segmentierung:** `download_resumable(connections=4)` gegen den lokalen Flaky-Server
  (`_flaky_http.py`, erweitert um `Range`/`206` für Teil-Ranges) → korrekte Datei +
  SHA; ein Segment-Cut → Segment-Resume vervollständigt.
- **Range-Fallback:** Server ohne Range-Support (immer `200`) → automatischer
  Single-Stream-Fallback, Datei korrekt.
- **`select_engine`:** Unit-Tabelle Schema/Endung → Engine, ohne Netz.
- **Fan-out (ein Asset pro Datei):** `aria2_download` **gemockt** → liefert N vorab
  erzeugte Mediendateien + 1 Nicht-Mediendatei → Test prüft: N Assets angelegt/geprobet,
  Nicht-Mediendatei ignoriert. (Echte httpx/ffmpeg, gemockte aria2-Schicht.)
- **`is_media_file`:** echtes Fixture vs. `.txt` (echter ffprobe).

### Ebene 2 — manuell zu prüfen

- `aria2_download` gegen einen **echten Einzeldatei-Torrent/Magnet** (Tracker/Peers nötig,
  headless nicht automatisierbar).
- Flaky-Netz real via toxiproxy/clumsy (wie im Basis-Spec).

## Lizenz

`aria2` ist **GPLv2+**. Es wird ausschließlich als **separater Prozess** (`aria2c`-CLI)
aufgerufen — *arm's length*, kein Linken/Vendoring. Das hält Lauras Lizenz unberührt,
genau wie beim bereits genutzten ffmpeg. `aria2c` ist ein **optionales Extra**: fehlt es,
funktioniert HTTP(S)-Ingest voll (segmentierte httpx-Engine), nur Torrent/FTP/Metalink
sind dann nicht verfügbar — konsistent mit der CLAUDE.md-Invariante „Backend startet ohne
Extras".

## Umsetzung in zwei Phasen (Empfehlung)

Die zwei Teile sind unabhängig; **Phase 1 löst das eigentliche Flaky-HTTP-Problem** und
sollte zuerst kommen:

- **Phase 1 — Segmentierung in httpx:** `download.py` (Range-Probe, paralleler
  Segment-Download, Reassembly, Resume, Fallback) + Tests. Keine neue Abhängigkeit.
- **Phase 2 — aria2-Engine + Torrent-Fan-out:** `aria2.py`, `is_media_file`,
  `select_engine`, `handle_fetch`-Fan-out, „ein Asset pro Datei" + Tests.

## Offene Punkte

- Default-Wert `connections` (Vorschlag 8) und `min_segment_bytes` (Vorschlag 8 MiB)
  final festzurren.
- Aufräum-Policy für Nicht-Mediendateien aus Torrents (im Download-Ordner belassen vs.
  löschen) — Vorschlag: belassen.
