# 17 — Runbook: Betrieb & Fehlerbehebung

Dieses Dokument ist für den **Betrieb** gedacht: Prozesse starten und beenden, Zustand
prüfen, und die Fehlerbilder, die in der Praxis wirklich aufgetreten sind. Ergänzt
[`06-storage.md`](06-storage.md) (wo liegen die Daten) und
[`10-testing-observability.md`](10-testing-observability.md) (wie wird gemessen) —
Wiederholungen bleiben bewusst aus.

## Prozesse und Ports

| Prozess | Port | Pflicht? | Start |
|---|---|---|---|
| `local-api` (FastAPI + Job-Runner) | `127.0.0.1:8765` | ja | `cd services/local-api && uv run laura-api` |
| Desktop-App (Electron) | — | nein | `pnpm dev` (startet ein eigenes Backend, falls keins läuft) |
| MCP-Server (stdio) | — | nein | vom MCP-Client gestartet, siehe [`services/mcp`](../services/mcp) |
| TTS-Sidecar (Voice-Cloning) | `127.0.0.1:8898` | nein | eigenes venv, siehe [`services/tts-sidecar/README.md`](../services/tts-sidecar/README.md) |
| Qdrant (semantische Suche) | `127.0.0.1:6333` | nein | Docker-Container; ohne ihn fällt die Suche still auf einen leeren In-Memory-Index zurück |

Alles bindet auf Loopback. Es gibt keinen Netzwerkdienst, der von außen erreichbar ist.

## Die zwei wichtigsten Betriebsregeln

**1. Das Backend hält den Port über die App hinaus.** Wird die Electron-App beendet, kann
der Backend-Prozess auf 8765 weiterlaufen — die nächste App-Instanz *verbindet sich dann mit
dem alten Prozess* und führt damit alten Code aus. Wer Code geändert hat und "nichts wirkt"
beobachtet, prüft zuerst, seit wann der Prozess auf 8765 läuft:

```powershell
Get-NetTCPConnection -LocalPort 8765 -State Listen |
  ForEach-Object { Get-Process -Id $_.OwningProcess } |
  Select-Object Id, ProcessName, StartTime
```

Liegt die `StartTime` vor der Codeänderung: Prozess beenden und neu starten.

**2. Das Backend detached starten.** Ein Backend, das an einer Konsole hängt, stirbt am
Ctrl-C-Broadcast dieser Konsole — auch dann, wenn das Signal einem anderen Kommando galt.
Für Live-Tests deshalb als eigenständigen Prozess starten (unter Windows z. B.
`Start-Process`), nicht im Vordergrund einer Shell, die noch für anderes benutzt wird.

## Zustand prüfen

```bash
curl -s http://127.0.0.1:8765/healthz            # Backend lebt
curl -s http://127.0.0.1:8898/healthz            # Sidecar lebt (falls genutzt)
```

Die API ist token-geschützt: jeder Aufruf außer `/healthz` braucht den Header
`X-Laura-Token` mit dem Wert aus `LAURA_TOKEN`.

## Wo die Daten liegen

Alle Laufzeit-Artefakte liegen unter `workspace/` (bzw. dem Pfad aus `LAURA_WORKSPACE`) —
Medien, Proxies, Analyse-Ergebnisse, Renders und die SQLite-Datenbank. Das Verzeichnis ist
gitignored und wächst schnell auf Gigabytes.

- **Sichern** heißt: `workspace/` kopieren (Datenbank und Medien gehören zusammen).
- **Zurücksetzen** heißt: `workspace/` verschieben und das Backend neu starten — es legt ein
  frisches Verzeichnis samt Datenbank an.
- Ein zweites Workspace (`LAURA_WORKSPACE=workspace-livetest`) ist der saubere Weg, Live-Tests
  von echten Projekten zu trennen. `workspace-*` ist ebenfalls gitignored.

## Fehlerbilder aus der Praxis

| Symptom | Ursache | Behebung |
|---|---|---|
| UI meldet **„Failed to fetch"** bei einer schreibenden Aktion | Volle Systemplatte: SQLite kann nicht schreiben, das 500 wird von CORS maskiert | Freien Speicher prüfen (`Get-Volume C`), aufräumen — nicht im Frontend suchen |
| Codeänderung wirkt nicht, obwohl neu gestartet wurde | Alter Backend-Prozess hält 8765, die App hat sich angehängt | Siehe Betriebsregel 1: `StartTime` prüfen, Prozess beenden |
| `database is locked` (sporadisch, unter Last) | Deferred Read→Write-Transaktion in SQLite; `busy_timeout` greift dort nicht | Betroffenen Schreibpfad auf `transaction(immediate=True)` umstellen — Muster in `repos.push_undo_checkpoint` |
| Voice-Job bricht mit **`sidecar unavailable: timed out`** ab | Kaltstart des TTS-Modells: Hugging Face drosselt die HEAD-Prüfungen trotz lokalem Cache | Sidecar mit `HF_HUB_OFFLINE=1` starten (Details im Sidecar-README) |
| Nach einem `uv sync` fehlen Agenten-Funktionen | `uv sync` **entfernt** Extras, die nicht in der Kommandozeile stehen | Mit den benötigten Extras synchronisieren (das Setup-Skript tut das) |
| Semantische Suche liefert nichts, ohne Fehler | Qdrant-Container fehlt **oder** `QDRANT_URL` fehlt in der Prozess-Umgebung → leerer In-Memory-Index | Container starten *und* `QDRANT_URL` in der Umgebung des Backends setzen |
| Schnitt trifft die falsche Bildstelle bei Screen-Recordings | Variable Bildrate: Zeit-Suche und Frame-Index laufen auseinander | Fenster per Frame-Index prüfen (`select='eq(n\,IDX)'`), nicht per Zeit-Suche |
| Render wirkt „schwarz" oder Übergänge fehlen | Filterketten-Fehler, der sich in Filtergraph-Strings nicht zeigt | Ergebnis am Pixel messen (`signalstats`, YAVG), nicht am erzeugten Filterstring |

## Logs

Das Backend loggt auf stdout/stderr des eigenen Prozesses (bei detached Start in die
umgeleiteten Dateien). Job-Fehler stehen zusätzlich strukturiert in der Datenbank am Job
(`error_json`) und sind über die API abrufbar — für fehlgeschlagene Jobs ist das die
verlässlichere Quelle als das Konsolen-Log.

## Verifikation nach Eingriffen

Nach jeder Änderung am Betrieb gilt dieselbe Kette wie in der CI — die drei Gates stehen im
[README](../README.md#verify-it). Für schnelle Rückversicherung reicht der Zeitkern:

```bash
cd services/local-api && uv run pytest -q -k "time or range"
```
