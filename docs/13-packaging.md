# 13 — Packaging, Signing & Release (Portion 10)

Ziel: signierte Desktop-Builds (Windows + macOS) mit **gebündelter Python-Runtime,
FFmpeg und libmpv**, plus reproduzierbare Demo-Projekte. Electron Forge deckt
Packaging/Installer/Signing/Publishing ab (`apps/desktop/forge.config.ts`).

> Status: Konfiguration + packaged-mode-Service-Auflösung sind im Code vorbereitet.
> Die eigentlichen signierten Builds (`pnpm make`) brauchen echte Zertifikate und ein
> OS-Setup und werden **nicht** headless ausgeführt.

## 1. Standalone-Service bauen

Der Renderer spricht nur mit dem lokalen Service. Im **packaged** Modus startet
`service.ts` kein `uv run`, sondern ein gebündeltes Binary unter
`process.resourcesPath/service/laura-api(.exe)` (siehe `resolveServiceCommand`).

Bauoptionen für das Binary:
- **PyInstaller** (`pyinstaller --onedir -n laura-api src/laura/main.py` mit Entry, der
  `laura.main:run` aufruft) → `dist/service/`.
- Alternativ eine gebündelte venv + Launcher-Skript.

Wichtig: die **optionalen ML-Extras** (asr/diarize) NICHT in den Standard-Build zwingen
(Größe!). Standard-Build = ingest/probe/proxy/waveform/analysis(scene)/interchange.
Schwere Modelle als optionaler Nachlade-Download.

## 2. FFmpeg & libmpv bündeln

- **FFmpeg/ffprobe**: LGPL-konforme Builds neben dem Service ablegen; Service findet sie
  über `LAURA_FFMPEG`/`LAURA_FFPROBE` (env) oder PATH. Beim Start setzt der Main-Prozess
  diese env-Variablen auf die gebündelten Pfade.
- **libmpv**: native Bibliothek + (später) Node-Bindings/`--wid`-Embedding für den
  primären Playback-Layer (ADR-0002). MVP nutzt den `<video>`-Proxy-Player (Portion 9);
  libmpv ist der geplante Pro-Pfad.

## 3. extraResource verdrahten

In `forge.config.ts` `packagerConfig.extraResource` auf die gebauten Artefakte zeigen:

```ts
extraResource: ["../../dist/service", "../../dist/ffmpeg"]
```

Dadurch landen sie unter `process.resourcesPath/...` im Build.

## 4. Signing & Notarization

- **Windows**: `@electron-forge/maker-squirrel` mit `certificateFile`/`certificatePassword`
  (oder Azure Trusted Signing). Code Signing verpflichtend für vertrauenswürdige Installer.
- **macOS**: `packagerConfig.osxSign` + `osxNotarize` (`@electron/notarize`, Apple-ID/Team-ID
  + App-spezifisches Passwort). Notarisierung praktisch Pflicht.
- Auto-Update signiert ausliefern.

## 5. Build-Kommandos

```bash
# 1) Service-Binary bauen (Beispiel)
cd services/local-api && uv run pyinstaller ...   # -> dist/service

# 2) Installer bauen (mit gesetzten Signing-Env-Variablen)
cd apps/desktop && pnpm make
```

`pnpm make` erzeugt plattformspezifische Installer unter `apps/desktop/out/`.

## 6. Release-Checkliste

- [ ] Standalone-Service gebaut, startet ohne `uv`/Python im PATH
- [ ] FFmpeg/libmpv gebündelt, Pfade via env gesetzt
- [ ] `extraResource` zeigt auf die Artefakte
- [ ] Windows signiert, macOS signiert + notarisiert
- [ ] Smoke: frisch installierte App startet Service, `/healthz` ok, Import→Proxy→Playback
- [ ] Reproduzierbares Demo-Projekt beigelegt
- [ ] Auto-Update getestet
