# Resilienter URL-Ingest mit Integritätsprüfung — Design

- **Datum:** 2026-06-03
- **Status:** Entwurf (zur Review)
- **Betrifft:** `services/local-api` — Ingest-Pipeline

## Kontext & Problem

Große Mediendateien (Größenordnung 30 GB) werden über instabile Verbindungen
heruntergeladen. Browser-Downloads erkennen Abbrüche und unvollständige Dateien oft
nicht und schreiben die fehlerhaften Bytes einfach weg. Ergebnis: Klötzchen, eingefrorene
Bilder, Tonaussetzer — „Bugs", die in der Originaldatei nicht vorhanden sind.

Lauras Ingest nimmt heute ausschließlich eine **bereits lokal vorhandene Datei** über
`source_path` entgegen (`api/assets.py::import_asset` → Job `ingest.probe`). Es gibt
keinen Download-Schritt und keine Prüfung, ob eine Datei beschädigt/unvollständig ist —
obwohl der Probe-Schritt bereits einen SHA-256 pro Datei berechnet und ablegt
(`ingest/probe.py::sha256_file`, `repos.add_asset_file`).

Über TCP ist In-Transit-Bitkorruption bereits geprüft; die realen „Bugs" stammen fast
immer aus **abgebrochenen oder abgeschnittenen Downloads** bzw. fehlerhaftem Resume.
Lauras Mehrwert: solche Dateien zuverlässig **erkennen** und robust **nachladen**.

## Ziel

Ein zusammenhängender Ingest-Weg „von URL":

```
URL rein → robust laden (resumable) → auf Korruption prüfen → bestehende Probe/Proxy-Pipeline
```

mit der Policy **Auto-Retry, dann melden**.

### Nicht-Ziele (YAGNI)

- Kein Torrent/Metalink, kein externer Downloader (aria2). Reines Python.
- **Kein Google-Drive-HTML-Scraping** (Confirm-Token-Flow). Stattdessen wird für Drive ein
  direkter `googleusercontent`-Link verwendet (manuell gezogen) — siehe „Quellen-Handling".
- Keine UI-Arbeit im Desktop-Renderer in diesem Spec (separater Folgeschritt).

## Architektur — neuer Ingest-Pfad

Bestehend bleibt unverändert:

```
import(source_path) → ingest.probe → proxy.build / audio.extract → waveform.build
```

Neu, davorgeschaltet bei URL-Quellen:

```
import(source_url) → ingest.fetch ──(ok)──> ingest.probe → … (bestehende Kette)
                         │
                         ├─ download_resumable()   (httpx Range, → dest.part)
                         └─ verify_decode()         (Größe + ffmpeg-Decode-Scan)
```

- `ingest.fetch` läuft auf der bestehenden Queue `ingest.io` (`QUEUE_INGEST`).
- **Download und Verify stecken in EINEM Job-Handler.** Nur so kann ein fehlgeschlagener
  Verify über den vorhandenen Job-Retry (`runner.enqueue(max_attempts=…)`) einen erneuten
  Lade-/Prüfzyklus auslösen.
- Bei Erfolg reiht `handle_fetch` `ingest.probe` ein — exakt wie heute `import_asset`,
  mit demselben Idempotenz-Key-Schema (`probe:{asset_id}`).

## Komponenten

### `ingest/download.py` (neu)

```python
def download_resumable(
    url: str,
    dest: Path,
    *,
    expected_sha256: str | None = None,
    chunk_bytes: int = 1 << 20,
    on_progress: Callable[[int, int | None], None] | None = None,
) -> DownloadResult: ...
```

- Lädt nach `dest.part`. Existiert `dest.part` bereits, wird dessen Größe als
  `Range: bytes=<n>-` gesendet → **Resume**.
- Folgt Redirects (`follow_redirects=True`) — nötig für Drive-/CDN-Links.
- Streaming-Read in `chunk_bytes`-Blöcken; `on_progress(downloaded, total)` für späteres
  UI-Feedback.
- Nach Abschluss: Endgröße gegen `Content-Length` (bzw. `Content-Range`-Total) prüfen;
  wenn `expected_sha256` gesetzt, SHA-256 verifizieren.
- Erst bei bestandener Größen-/Hash-Prüfung **atomares `os.replace(dest.part, dest)`**.
- Reine Netz-/IO-Fehler (Timeout, Connection-Reset, unvollständig) → `DownloadError`;
  `dest.part` bleibt erhalten (Resume möglich).
- Nutzt `httpx` (bereits Laufzeit-Dependency, `httpx>=0.27`). Keine neue Abhängigkeit.

### `ingest/integrity.py` (neu)

```python
@dataclass(frozen=True)
class IntegrityReport:
    ok: bool
    container_ok: bool
    decode_errors: int
    detail: str

def verify_decode(path: Path, *, full_scan: bool = True) -> IntegrityReport: ...
```

- **Container-Check (immer, günstig):** vorhandenes `ffmpeg.probe(path)` — schlägt fehl,
  wenn der Container kaputt/abgeschnitten ist.
- **Decode-Scan (Default an):** `ffmpeg -v error -i <path> -f null -`, stderr-Fehlerzeilen
  zählen. `decode_errors > 0` ⇒ `ok = False`. Das fängt kaputte Frames/Pakete.
- `full_scan=False` überspringt den teuren Decode-Scan (nur Container-Check) — abschaltbar
  über Job-Payload/Env, da der volle Scan einer 30-GB-Datei mehrere Minuten CPU kostet.
- Baut auf den bestehenden `ffmpeg_bin()`/`FFmpegError`-Wrappern in `ingest/ffmpeg.py` auf;
  neuer Wrapper `decode_scan(path) -> int` dort ergänzen.

### `ingest/handlers.py` (erweitern)

Neuer Handler `handle_fetch` (kind `ingest.fetch`):

1. Asset laden; `source_url` aus Payload.
2. Zielpfad bestimmen: `workspace_root / "downloads" / asset_id / <dateiname>`.
3. **Download-Stufe:** `download_resumable(...)`. Bei `DownloadError` → re-raise
   (`.part` bleibt → Job-Retry *setzt fort*).
4. **Verify-Stufe:** `verify_decode(dest)`. Bei `ok == False`:
   - **`.part`/`dest` verwerfen** (kaputte Bytes lassen sich per Resume nicht reparieren →
     nächster Versuch lädt *komplett neu*),
   - Integrity-Report als `asset_file` kind `integrity` (JSON) ablegen,
   - `IntegrityError` re-raise → Job-Retry.
5. Bei Erfolg: `source_path` des Assets auf `dest` setzen, `online=True`,
   `ingest.probe` einreihen (Idempotenz-Key `probe:{asset_id}`).

Registrierung: `registry["ingest.fetch"] = handle_fetch`; in `jobs/queues.py`
`"ingest.fetch": QUEUE_INGEST` ergänzen.

### `api/assets.py` + `api/models.py` (erweitern)

- `AssetImport`: Feld `source_url: str | None` zusätzlich zu `source_path`. Genau eines von
  beiden muss gesetzt sein (Validator).
- `import_asset`: bei `source_url` Asset mit `type="video"` (Probe korrigiert),
  `online=False`, `source_path=<geplanter Zielpfad>` anlegen und `ingest.fetch` einreihen
  (Idempotenz-Key `fetch:{asset_id}`, `max_attempts=5`). Sonst Verhalten wie bisher.

## Datenfluss & Statusmodell

- `online=False` markiert „noch nicht verfügbar / in Arbeit / fehlgeschlagen".
- Der `integrity`-`asset_file` (JSON) hält bei Fehlschlag fest, **warum** (Decode-Fehler,
  Größen-Mismatch).
- Erfolgreicher Fetch → `online=True` + `source_path` = lokale Datei → ab hier identisch
  zum bestehenden lokalen Ingest.

## Fehler- & Retry-Logik (Policy „Auto-Retry, dann melden")

`ingest.fetch` mit `max_attempts=5`. Zwei Fehlerklassen, **bewusst unterschiedlich**:

| Fehlerklasse | `.part`-Datei | Nächster Versuch |
|---|---|---|
| Download-Abbruch (Netz/Timeout) | bleibt | **Resume** ab Teilgröße |
| Verify schlägt fehl (zu kurz / Decode-Fehler) | wird verworfen | **Voll-Neuladen** |

Nach erschöpften Versuchen: Asset bleibt `online=False`, Integrity-Report erklärt den
Grund, der Job-Fail ist im Job-Log sichtbar. **Kein stiller Erfolg.**

## Quellen-Handling (inkl. Google Drive)

`download.py` bleibt ein generischer HTTP(S)-Downloader (Redirects + Range + Resume +
Größen-/Hash-Verify). Google Drive wird **nicht** über den Confirm-Token-/HTML-Flow
unterstützt; stattdessen:

- Der direkte **`googleusercontent`-Link** (manuell aus dem Browser gezogen) wird als
  `source_url` übergeben. Dieser umgeht die „Virenscan"-Bestätigungsseite und unterstützt
  Range/Resume.
- Dokumentation: kurze Anleitung „wie komme ich an den Direkt-Link" in der Ingest-Doku
  ergänzen.

## Tests & Verifikation

### Ebene 1 — deterministisch, im Suite (kein echtes Netz)

Lokaler Mini-HTTP-Server (threaded `http.server`/ASGI) mit `Range`-Support und gezieltem
Fehlverhalten:

- **Connection-Cut** nach N Bytes → Resume vervollständigt; Endgröße/Hash stimmen.
- **Truncated** (weniger Bytes als `Content-Length`) → Größen-Check schlägt an.
- **Bandbreiten-Drossel / häppchenweise** → langsame Leitung.

`integrity.py`: echtes Video-Fixture + bewusst abgeschnittene Kopie → `verify_decode`
meldet die abgeschnittene als fehlerhaft (**echter ffmpeg-Lauf**, wie von CLAUDE.md für
Ingest gefordert).

Handler-Test: `fetch → probe`-Verkettung; `.part`-Verhalten je Fehlerklasse
(Download-Abbruch ⇒ Resume, Verify-Fail ⇒ Voll-Neuladen).

### Ebene 2 — echtes „kaputtes Netz" (manuell zu prüfen)

Impairing-Proxy zwischen Laura und der echten Quelle:

- **toxiproxy** (cross-platform) — Latenz, Bandbreitenlimit, gezielte Verbindungsabbrüche.
- **clumsy** (Windows) — paketbasiertes Drosseln/Verwerfen, schnell für Ad-hoc-Tests.

Headless nicht automatisierbar → ausdrücklich **„manuell zu prüfen"** markieren.

## Performance-Hinweis

Voller Decode-Scan einer 30-GB-Datei kostet mehrere Minuten CPU. Default: günstiger
Größen-/Container-Check immer, voller Decode-Scan standardmäßig an (das ist der Sinn),
per Flag (`full_scan=False`) abschaltbar.

## Offene Punkte

- Genauer Pfad/Namensschema unter `workspace/downloads/` final festzurren
  (Abgleich mit `docs/06-storage.md`).
- UI-Anbindung (Fortschritt, „corrupt"-Anzeige) ist Folge-Spec.
