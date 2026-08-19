# Laura-MCP — Design (2026-08-19)

Die vollständige Laura-App als MCP-Server für Claude-Sessions nutzbar machen: Media-Import
(Datei/Ordner/URL), Analyse, Produktion mit Gates, Editorial/Schnitt und Export. Die treibende
Claude-Session (Claude Code oder Claude Desktop) übernimmt die Rolle, die heute das AutoGen-Team
mit `openrouter/free` spielt — mit deutlich stärkerem Modell, ohne API-Kosten (läuft übers Abo)
und ohne dass die serverseitigen Qualitäts-Invarianten (Gates, Guards, Provenienz) umgangen
werden können.

## Entschiedene Rahmenbedingungen

| Frage | Entscheidung |
|---|---|
| Clients | Claude Code **und** Claude Desktop, beide über denselben stdio-Server. Kein Remote/HTTP-Transport. |
| Lifecycle | **Nur andocken.** Der MCP funktioniert, während die Laura-App läuft (Backend auf 127.0.0.1:8765). Backend nicht erreichbar → klare Fehlermeldung, kein eigenes Backend-Starten. |
| Tool-Zuschnitt | **Ansatz A:** ~26 aufgabenförmige Tools + ein generischer `laura_api`-Notausgang für seltene Endpunkte. Kein 1:1-Spiegel. |
| Destruktives | `delete_project` / `delete_asset` / `delete_production` bekommen keine erstklassigen Tools — nur über den Notausgang, und die Session fragt vor jedem Löschen nach. |

## Architektur

Neues Paket **`services/mcp`** (eigenes uv-Projekt `laura-mcp`), Abhängigkeiten nur `mcp`
(offizielles Python-SDK, FastMCP-Server) und `httpx`. Der Server ist ein **reiner HTTP-Client**
gegen das laufende Backend — er importiert keinen Laura-Backend-Code. Dadurch:

- Null Kopplung: funktioniert identisch gegen die von der Electron-App gestartete Instanz.
- Der Workspace ist immer der der laufenden App — der MCP hat keinen eigenen Workspace-Begriff.
- Konfiguration über Env: `LAURA_TOKEN` (Pflicht, wandert als `X-Laura-Token`-Header mit),
  Base-URL fest `http://127.0.0.1:8765` (bewusst nicht konfigurierbar — kein Weg, den MCP auf
  fremde Hosts zu richten).

**Fehlerbild:** `httpx.ConnectError` → jede Tool-Antwort lautet
`"Laura app is not running — start the Laura desktop app, then retry."` HTTP-Fehler geben den
`detail`-Satz des Backends weiter (nicht das rohe JSON — gleiche Regel wie `errorText` im
Desktop-Client). Timeouts: 30 s Standard, 120 s für Render-/Analyse-Anstöße (die Arbeit selbst
läuft asynchron über Jobs).

**Bilder:** `get_frame` und `get_contact_sheet` liefern MCP-Image-Content (PNG, base64) — die
Session kann Frames tatsächlich ansehen, bevor sie schneidet.

**Registrierung:** `claude mcp add laura -- uv run --directory <repo>/services/mcp laura-mcp`
für Claude Code; für Claude Desktop der entsprechende `mcpServers`-Eintrag in dessen Config.
Beide Snippets kommen in `services/mcp/README.md`. `LAURA_TOKEN` steht im jeweiligen
`env`-Block der Registrierung, nie im Repo.

## Tool-Katalog

Signaturen sind bindend; jedes Tool kapselt die genannten Endpunkte. Die Beschreibungstexte der
Editorial-Tools tragen die Zeitkern-Invarianten (Ganzzahl-Frames, `out_frame_exclusive`,
OTIO ist Source of Truth), damit jeder Client sie ohne Repo-Wissen korrekt bedient.

### Media (4)

| Tool | Signatur | Endpunkte |
|---|---|---|
| `list_projects` | `()` | `GET /projects` |
| `list_assets` | `(project_id)` | `GET /projects/{pid}/assets` |
| `import_media` | `(project_id, source, display_name?, format?, cookies_from_browser?)` — `source` ist Dateipfad **oder** URL; Playlist-URLs fächern serverseitig auf | `POST /projects/{pid}/assets/import` |
| `import_status` | `(asset_id)` | `GET /assets/{aid}/import-status` |

### Analyse & Suche (4)

| Tool | Signatur | Endpunkte |
|---|---|---|
| `analyze_asset` | `(asset_id)` → job_id | `POST /assets/{aid}/analysis` |
| `get_transcript` | `(asset_id, start_frame?, end_frame_exclusive?)` — Segmente + Wörter, optional auf einen Frame-Bereich beschnitten | `GET /assets/{aid}/transcript` |
| `get_shots_and_scenes` | `(project_id, asset_id)` — Shots + Rough-Cut-Szenen in einem Ruf | `GET /assets/{aid}/shots`, `GET /projects/{pid}/assets/{aid}/rough-cut` |
| `search_material` | `(project_id, query, mode?, limit?)` — semantisch (qdrant) mit lexikalischem Fallback | `POST /search` |

### Sehen (2)

| Tool | Signatur | Endpunkte |
|---|---|---|
| `get_frame` | `(asset_id, frame)` → PNG | `GET /assets/{aid}/frame/{frame}` |
| `get_contact_sheet` | `(session_id)` → PNG | `GET /production/{sid}/contact-sheet` |

### Produktion (7)

| Tool | Signatur | Endpunkte |
|---|---|---|
| `start_production` | `(asset_id, task, target_seconds?, format?, language?)` — legt Session + Board im **Author-Modus** an (kein Team-Job; Gates S und B aktiv) | `POST /assets/{aid}/production` mit `author: "external"` (neu, s. u.) |
| `production_status` | `(session_id)` — Board-Status inkl. Gates, resume_point, Events-Tail | `GET /production/{sid}`, `GET /production/{sid}/events` |
| `propose_scenes` | `(session_id, candidates)` — Szenen-Vorschlag, armiert Gate S | `PUT /production/{sid}/scene-proposal` (neu) |
| `confirm_scenes` | `(session_id, scene_numbers, selection_version)` | `POST /production/{sid}/scene-selection:confirm` |
| `save_storyline` | `(session_id, red_thread, chapters)` | `PUT /production/{sid}/storyline` (neu) |
| `save_script_chapter` | `(session_id, chapter, lines)` — Kapazitäts-Guard antwortet wie im Team-Pfad (reject-once-then-warn) | `PUT /production/{sid}/script/chapters/{n}` (neu) |
| `approve_script` | `(session_id)` — löst den deterministischen Rest aus (Voice→Cutlist→Sheet→Render→QA) | `POST /production/{sid}/script:approve` (neu) |

### Editorial (4)

| Tool | Signatur | Endpunkte |
|---|---|---|
| `get_timeline` | `(timeline_id \| project_id)` — Clips, Szenenmarker, History-Kopf | `GET /timelines/…`, `GET /projects/{pid}/timelines`, `GET /timelines/{tid}/history` |
| `edit_timeline` | `(timeline_id, operation)` — genau eine Op pro Ruf, `op` ∈ {`trim`, `move`, `delete`, `delete_words`, `lift`, `insert`, `append`, `place_clip`, `set_speed`, `set_audio_offset`}; Payload wird 1:1 an den Operations-Endpunkt gereicht | `POST /timelines/{tid}/operations` |
| `edit_scenes` | `(timeline_id, action, args)` — `action` ∈ {`generate`, `split`, `merge`, `cut_at_frame`, `rename`} | `POST /timelines/{tid}/scenes:generate`, `…/scenes/{sid}/split`, `…/scenes/merge`, `…/cut-at-frame`, `PATCH /scenes/{sid}` |
| `timeline_undo` | `(timeline_id, redo?)` | `POST /timelines/{tid}/undo`, `…/redo` |

Transitions laufen im ersten Wurf über den Notausgang (`PATCH /timelines/{tid}/clips/{cid}/transition`,
`POST …/auto-transitions`, Review/apply-fix); werden sie im Live-Betrieb häufig, ziehen sie als
eigenes `set_transitions`-Tool nach — das ist eine bewusste YAGNI-Grenze, kein Versehen.

### Render & Export (4)

| Tool | Signatur | Endpunkte |
|---|---|---|
| `render_timeline` | `(timeline_id)` → export_id + job_id | `POST /timelines/{tid}/render` |
| `list_exports` | `(project_id)` / `get_export(export_id)` → Pfad, Status, Größe | `GET /projects/{pid}/exports`, `GET /exports/{eid}` |
| `auto_produce` | `(kind ∈ {overview, short}, project_id, topic, …)` — die Ein-Ruf-Automatiken | `POST /projects/{pid}/auto-overview`, `…/auto-short` |

### Jobs & Notausgang (2)

| Tool | Signatur | Endpunkte |
|---|---|---|
| `job_status` | `(job_id, wait_s?)` — pollt serverseitig bis `wait_s` (max 60), spart Client-Runden | `GET /jobs/{jid}` |
| `laura_api` | `(method, path, body?)` — generischer Zugriff auf alles Übrige (Reels, Szenen-Musik, Audio-Clips, Voiceover, Batch, Shorts-Kandidaten, Sequenz-Transitions, Löschen …). Beschreibung nennt explizit die Löschen-nur-nach-Rückfrage-Regel. | beliebig, nur 127.0.0.1:8765 |

## Author-Endpunkte (Backend-Arbeit)

Die vier Schreib-Operationen der Produktions-Familie existieren heute nur als in-process
FunctionTools des AutoGen-Teams (`short_creator/production_tools.py`: `propose_scene_selection`,
`save_storyline`, `save_script_chapter`) bzw. als Chat-Executor-Handler
(`chat/executor.py::_handle_approve_script`). Dort sitzen die Invarianten: Grounding-Regeln,
Kapazitäts-Guard pro Zeile, `selection_version`, `content_hash`-Bindung von Gate B,
Write-then-enqueue beim Approve.

**Umsetzung:** Die Kernlogik dieser Funktionen wird in board-nahe Service-Funktionen gezogen
(Modul `short_creator/authoring.py`), die **beide** Aufrufer verwenden — die FunctionTool-Closures
des Teams und die neuen Endpunkte. Kein doppelter Guard-Code.

Neue Endpunkte (alle mit demselben Busy-Guard wie `delete_production`: 409, solange ein
Team-/Produktions-Job auf der Session läuft):

- `POST /assets/{aid}/production` lernt `author: "external"` — legt Session + Board an, erzwingt
  `scene_gate=True` und `script_gate=True`, **enqueued keinen Team-Job**. Alle Folge-Schritte
  kommen von außen. `POST /production/{sid}/message` weist Author-Sessions ab (409 mit Hinweis),
  damit nicht Team und externer Autor auf demselben Board schreiben.
- `PUT /production/{sid}/scene-proposal` → `propose_scene_selection`-Kern (armiert Gate S,
  bumpt `selection_version`).
- `PUT /production/{sid}/storyline` → `save_storyline`-Kern (Fenster-Referenzen validiert).
- `PUT /production/{sid}/script/chapters/{n}` → `save_script_chapter`-Kern (Kapazitäts-Guard,
  Under-Budget-Soft-Gate).
- `POST /production/{sid}/script:approve` → Approve-Kern aus `_handle_approve_script`
  (Hash-gebunden, startet den deterministischen Rest als Job; Doppel-Approve bleibt idempotent).

Der bestehende Team-Pfad (Chat in der App) bleibt unverändert; `author` ist opt-in pro Session.

## laura-producer-Skill

`.claude/skills/laura-producer/SKILL.md` im Repo: der Produktions-Vertrag für treibende
Sessions — Gate-Reihenfolge (Szenen → Storyline → Script → Approve), Grounding-Regel (Script
nur aus dem Transkript des gewählten Materials), Sprachregel (Produktionssprache folgt der
Anweisungssprache), Zielraten (Sprechrate aus dem Timings-Sidecar messen, nie schätzen),
Sehen-vor-Schneiden (`get_frame`/`get_contact_sheet`), Lösch-Rückfrage-Regel. Claude Desktop
bekommt keinen Skill — dort tragen die Tool-Beschreibungen die Semantik.

## Tests

- **MCP-Paket:** pytest gegen `httpx.MockTransport` — pro Tool mindestens: Happy-Path-Mapping
  (richtiger Endpunkt, richtiger Body), Fehler-Durchreichung (`detail`-Satz), Backend-down-Meldung.
  Kein laufendes Backend in CI.
- **Author-Endpunkte:** bestehende Backend-Test-Muster (`tests/test_api_scene_selection.py` als
  Vorlage): Guards greifen (Kapazitäts-Reject, `selection_version`-409, Busy-409,
  Team-Session-weist-Author-Writes-ab und umgekehrt), Approve enqueued genau einen Job.
- **Manuelle Live-Prüfliste:** ein kompletter Durchlauf über den MCP — Import (URL) → Analyse →
  `start_production` → Szenen → Storyline → Script → Approve → Export abspielbar; parallel in der
  App sichtbar (Open Productions, Contact Sheet, Export-Tab).

## Slices

1. **MCP-Grundgerüst + Lese-/Editorial-Familien:** `services/mcp` mit Media, Analyse & Suche,
   Sehen, Editorial, Render & Export, Jobs, Notausgang + Tests + README-Registrierung.
2. **Author-Modus:** `authoring.py`-Extraktion, die fünf Backend-Änderungen + Produktions-Familie
   im MCP + `laura-producer`-Skill.
3. **Live-Test + Polish:** manuelle Prüfliste komplett fahren, Claude-Desktop-Registrierung,
   Tool-Beschreibungen nachschärfen, was der Live-Lauf an Lücken zeigt.

## Bewusst außerhalb des Scopes

- Remote-Transport (HTTP/SSE, claude.ai) — lokale Filmdaten bleiben lokal.
- Erstklassige Tools für Admin, AI-Runtimes, Demo, Reenact/Lipsync, Overlays, Voiceover-Stimmen,
  Szenen-Musik — alles über `laura_api` erreichbar.
- Änderungen am App-Chat oder am Team-Pfad (läuft unverändert weiter).
- Backend-Lifecycle-Management durch den MCP (kein Starten/Stoppen).
