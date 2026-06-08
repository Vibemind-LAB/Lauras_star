# Laura ↔ VibeMind — Video-Space-Integration (Design / Spec v2)

**Datum:** 2026-06-08
**Status:** Design (owner-verifiziert) → writing-plans erledigt (`plans/2026-06-08-vibemind-laura-integration-v2.md`)
**Quelle:** vom VibeMind-Owner verifizierte Code-Recherche in beiden Repos (Vibemind_V1 + Laura). Supersedet die v1 (`2026-06-03-vibemind-laura-integration-design.md`), deren Tauri-/Event-Agent-Annahmen falsch waren.

> Hinweis: relative `../../../`-Links im Original zeigen auf `Vibemind_V1`-Pfade; hier zur Referenz unverändert dokumentiert.

## §0 — TL;DR
- **Laura** = lokales frame-genaues KI-Film-Editing (Electron + React/Vite, **FastAPI :8765**, FFmpeg, OTIO als Wahrheitsquelle, SQLite). Remote `Vibemind-LAB/Lauras_star`.
- **Integrationsentscheidungen (Owner):**
  1. UI: Lauras Renderer als **BrowserView-Space** in VibeMinds bestehender **Electron**-Shell (Muster `rowboat-manager.js`) — **nicht** Tauri, **keine** zweite Electron-Runtime.
  2. Backend: **native venv-Phase** in `Vibemind_V1/scripts/vibemind-start.ps1` (Phase 3, `Start-VenvPython`).
  3. Repo: **Git-Submodul** `vibemind-os/spaces/video/laura`.
  4. Steuerung: **MCP-Tools** via zentralem **OpenFang-MCP-Hub** (:4200/mcp). Kein neuer Event-Agent, keine Intent-Classifier-Änderung in Iteration 1.
- **Sequenz:** Lauras Eigenarbeit (UI+Backend, Render→MP4) = **Phase 0**, geht vor allem; danach Submodul-Commit pinnen.
- **Korrigierte Falschannahmen:** :8765 in VibeMind = Coding-Engine-**WebSocket** (kein Konflikt). `VideoBackendAgent` ist für Laura irrelevant (Laura hat eigene REST). `voice/python/spaces/video` ist **Import-Alias**, kein Duplikat.

## §1 — Verifizierter Ist-Zustand (Kurzfassung)
**VibeMind start:** `Vibemind_V1/scripts/vibemind-start.ps1` (phasenbasiert; Phase 3 = venv-Services via `Start-VenvPython` mit `-Name -VenvPath -WorkDir -ScriptArgs -ExtraEnv -Port`, PIDs → `logs/services.registry.jsonl`). Interaktiv: `Vibemind.debug.ps1`. Stop: `scripts/vibemind-stop.ps1` (port/cmdline tree-kill).
**Video-Space:** existiert (`VideoBackendAgent`, Stream `events:tasks:video`, vibevideo/-deepfake-Submodule, media_server :8977 → `~/.rowboat/Videos/`). Koexistiert mit Laura.
**UI-Muster:** Spaces = `BrowserView` in Electron-Shell (`voice/electron-app/rowboat-manager.js`; `space-navigator.js`). Tauri-Apps (`launcher-app`) = Bootstrap, nicht UI-Heimat.
**MCP:** zentraler OpenFang-Hub :4200/mcp; Server unter `openfang/mcp/`; `mcp_stdio_bridge.py`.
**Laura:** FastAPI :8765 (`/healthz`, projects/assets/import(+url)/jobs/analysis/timelines(+ops/exports/captions)/search); Auth `LAURA_TOKEN` (zufällig pro Start); Electron-IPC `laura:service-info`/`pick-*`/`laura-media://`; Export OTIO/EDL/FCP7/FCPXML/SRT/VTT; Output `{workspace}/exports/{timeline_id}/`. Invarianten: frame-/sample-genau, end-exclusive, OTIO-Wahrheit, local-first.

## §2 — Antworten auf die 10 Fragen (verbindlich)
1. **Start-Skript:** `Vibemind_V1/scripts/vibemind-start.ps1` (+ `Vibemind.debug.ps1`).
2. **VideoAgent läuft**, ist für Laura aber **irrelevant** (eigene REST). Koexistenz.
3. **Laura-Backend:** Hintergrund-Service via neue **Phase-3-`Start-VenvPython`-Zeile**, `-Port 8765`, `-ExtraEnv` (HOST/PORT/TOKEN/WORKSPACE/FFMPEG/FFPROBE). Kein Docker/Sidecar.
4. **UI:** **BrowserView** (Electron), Muster `rowboat-manager.js`; Lauras `dist` als Space „laura"; Lauras `main.ts` entfällt im integrierten Modus.
5. **Tauri-Plugins:** ❌ nicht nötig (UI = Electron). Lauras File-Dialoge auf VibeMind-Electron-Preload umziehen (`laura-preload.js`).
6. **Submodul:** `spaces/video/laura` ✅. „Duplikat" = Import-Alias, kein Sync/Symlink.
7. **Laura-Stand:** zuerst Phase 0 (Lauras Eigenarbeit), dann pinnen. Working Tree clean machen, `.claude/`+`build/` ignorieren. Bis dahin `feat/pipeline-foundation`.
8. **Steuerung:** **MCP-Tools** (`laura_project_create/import/analyze/timeline_op/export/status`) via OpenFang-Hub — **nicht** neue Bus-Events/Voice. Intent-Classifier bleibt (Phase 4 optional).
9. **Token:** `LAURA_TOKEN` zufällig pro Start; die VibeMind-Startphase erzeugt es, setzt `-ExtraEnv` **und** schreibt es nach `logs/laura.token` (Kontrakt); MCP-Server + BrowserView lesen von dort. `HF_TOKEN` aus VibeMind-`.env`.
10. **Medien:** Projekt-State → `~/.rowboat/Laura/` (`LAURA_WORKSPACE`); gerenderte **MP4** → `~/.rowboat/Videos/` (Gallery + media_server). MP4-Render ist Teil von Lauras Phase 0 (export stage plan).

## §3 — Integrationsstufen
- **Phase 0 (Laura):** UI+Backend fertig (inkl. Render→MP4), Renderer von `main.ts` entkoppeln (einsetzbares Preload), Tree clean. Abschluss: headless `uv run laura-api` grün, `dist` reproduzierbar, pin-fähiger Commit.
- **Phase 1 (VibeMind):** Submodul + venv + Phase-3-Start-Zeile (Token-Gen + Token-File) + Stop + Health-Gate. Abschluss: Laura startet mit dem Stack, `/healthz` grün.
- **Phase 2 (VibeMind):** `laura-manager.js` (BrowserView) + `laura-preload.js` (`window.laura.*` → VibeMind-IPC; `service-info` liest Token-File) + Space-Registrierung + `laura-media://`. Abschluss: Space „Laura" funktioniert gegen :8765.
- **Phase 3 (VibeMind):** `openfang/mcp/laura_server.py` (FastMCP → Laura-REST mit Token-File) + Hub-Registrierung. Abschluss: `laura_status`/`laura_import` über Hub E2E.
- **Phase 4 (optional):** Voice/Intent für `laura.*`.

## §4 — Risiken
Per-Start-Token → Token-File-Kontrakt. Renderer/IPC-Kopplung → Phase-0-Vorbedingung. FFmpeg/torch-venv-Größe → gebündeltes ffmpeg, ML optional. Render→MP4 fehlt heute → Lauras Phase 0. Port 8765 → kein realer Konflikt (CE=WebSocket), trotzdem Health-Gate. Unclean Tree → Phase-0-Vorbedingung. Fallback „eigenes Fenster" falls BrowserView scheitert (nicht bevorzugt).

## §5 — Out of scope (YAGNI)
Kein Voice/Intent (Iter 1), kein Event-Agent (MCP genügt), kein Docker (venv), kein Sync für Import-Alias, keine Tauri-Plugins, kein Merge in `VideoBackendAgent` (Koexistenz).
