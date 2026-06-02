# 09 — Sicherheit & Datenschutz

**Local-first ist Architekturwert, kein Marketing.** Footage, Analyse und Suchanfragen verlassen
den Rechner nicht (Vorbild: Adobe lokale Media-Intelligence). Cloud ist strikt opt-in.

## Sicherheitsmodell

| Ebene | Empfehlung |
|---|---|
| Desktop-Secrets | Electron `safeStorage` + OS-Keychain (macOS) / DPAPI (Windows) |
| Workspace-Schutz | projektlokale Verschlüsselung optional; Secrets **getrennt** von Workspace |
| Cloud-Zugriff | project-scoped Access Tokens, kurze TTL, explizites Device Linking |
| Team-Modus | Rollen: `owner`, `editor`, `reviewer`, `exporter`, `admin` |
| Storage | Signed URLs für Objektzugriff, **nie** rohe Bucket-Credentials im Client |
| Audit | Export- und Analyse-Aktivitäten mit Provenance loggen |

## Electron-Härtung (Pflicht)

- `contextIsolation: true`, `nodeIntegration: false`, `sandbox: true` im Renderer.
- IPC ausschließlich über **typisierte Preload-Bridge** (`contextBridge.exposeInMainWorld`),
  Allowlist an Kanälen; kein direkter `ipcRenderer`-Durchgriff.
- Renderer hat **keinen** FS-/Modell-Zugriff — alles über die lokale API (Audit + Least Privilege).
- Lokale API bindet nur an `127.0.0.1`; Zugriff über lokales Session-Token (Main setzt Header).
- Externe Links nie ungeprüft öffnen; Navigationsschutz im Main (`will-navigate`/`setWindowOpenHandler`).
- Auto-Update signiert; Code Signing (Win) + Notarization (macOS) verpflichtend für Releases.

## Cloud-/Team-Betrieb (später)

- **RLS** in Supabase/Postgres als Kernmechanismus für Datenzugriff.
- **API-Key-Schutz** + Verschlüsselungsoptionen in Qdrant.
- Self-hosted Supabase ist brauchbar, aber **nicht feature-identisch** zur gehosteten Variante — als Annahme einplanen.

## Datenschutz-Default

Kein Telemetrie-Upload ohne explizites Opt-in. Analyse-/Suchindex bleibt lokal. Modelle laufen
lokal; Cloud-ASR nur als ausdrücklich gewählter Fallback.
