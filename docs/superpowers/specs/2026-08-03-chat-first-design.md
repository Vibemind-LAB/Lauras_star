# Chat-first: Laura als interaktive Chat-App (Design)

Datum: 2026-08-03 · Status: freigegeben (Design-Dialog, Ansatz A) · Autor: Brainstorming-Session

## Ziel

Laura bekommt eine Chat-Bühne als Hauptoberfläche: wie Lovable Code baut, baut Laura im
Dialog Videos. Der Nutzer tippt, was er will („bau mir einen 60s-Short über X", „hol dir
diese Drive-URLs", „Hook kürzer"), sieht live erzählt, was die Agenten tun, und bekommt das
Ergebnis als Preview neben dem Faden. Eingreifende Aktionen (Imports von URLs) passieren
nie ohne ausdrückliche Freigabe über eine Approval-Karte im Faden.

**Entschieden im Dialog:**

1. **Chat wird die Bühne** — Standard-Ansicht der App. Die 7 bestehenden Views (Download,
   Import, Rough Cut, Feinschnitt, Zusammenfügen, Shorts, Export) bleiben unverändert als
   „Werkzeug-Modus" hinter einem Umschalter erreichbar.
2. **Der Chat kann alles** — Filmbau, Folgerunden, Zurückdrehen, Import, Projekt
   anlegen/wechseln. Eingreifende Aktionen nur mit serverseitig erzwungenem Approval.
3. **Global mit Chat-Liste** — wie ChatGPT/Claude: links eine Liste der Unterhaltungen,
   „Neuer Chat" startet frisch, jeder Faden darf jedes Projekt berühren. Ein Faden „steht"
   auf einem aktiven Projekt (wechselbar per Kommando).
4. **V1-Umfang: alle vier Fähigkeiten** — Film bauen + Folgerunden, Live-Erzählung,
   Import per Chat + Approval, Projekt anlegen/wechseln per Chat.

## Architektur-Überblick

```
Chat-Bühne (Desktop)          Lokale API                      Bestehende Maschinerie
┌──────────────────┐   ┌──────────────────────────┐   ┌────────────────────────────┐
│ Chat-Liste       │──▶│ /conversations …          │   │ auto-short / auto-overview │
│ Faden + Karten   │   │   Router-Agent (1 Turn)   │──▶│ /production + /message     │
│ Preview rechts   │◀──│   Approval-Vollzug        │   │ /assets/import (Jobs)      │
│ Werkzeug-Modus   │   │ /production/{sid}/events  │◀──│ agent-runs/*/runs/*.ndjson │
└──────────────────┘   └──────────────────────────┘   └────────────────────────────┘
```

Drei neue Bausteine — Konversationsschicht (DB + Endpunkte), Router-Agent, Chat-Bühne.
Alles Schwere (Produktion, Discovery, Import, Render) existiert und wird nur orchestriert.
Der Router ist **eine LLM-Runde pro Nachricht** (kein Team): Nachricht + Kontext rein,
genau ein Tool-Call raus. Lange Arbeit läuft wie bisher als Job; der Faden referenziert sie.

## Datenmodell (Migration 0034)

**`conversations`**

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | TEXT PK | uuid hex |
| `title` | TEXT | erste Nutzer-Nachricht, auf 60 Zeichen gekürzt |
| `active_project_id` | TEXT NULL | das Projekt, auf dem der Faden gerade „steht" |
| `created_at` / `updated_at` | TEXT | ISO-UTC |

**`conversation_messages`**

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | TEXT PK | uuid hex |
| `conversation_id` | TEXT FK | |
| `seq` | INTEGER | lückenlose Ordnung pro Konversation (1..n) |
| `role` | TEXT | `user` \| `assistant` |
| `kind` | TEXT | `text` \| `approval_request` \| `action` |
| `content_json` | TEXT | kind-abhängige Nutzlast (unten) |
| `created_at` | TEXT | ISO-UTC |

**Kinds:**

- `text`: `{ "text": str }` — normale Blase.
- `approval_request`: `{ "action_type": "import_urls", "payload": { "urls": [str], "project_id": str }, "status": "pending"|"approved"|"rejected"|"executed", "decided_at": str|null, "result": {...}|null }`.
  Die Entscheidung wird **in der Nachricht** persistiert — der Verlauf zeigt für immer, was
  freigegeben wurde. `action_type` ist in v1 nur `import_urls`; das Feld existiert, damit
  spätere eingreifende Tools denselben Mechanismus nutzen.
- `action`: `{ "tool": str, "args": {...}, "refs": { "session_id"?: str, "job_id"?: str, "export_id"?: str, "asset_ids"?: [str] }, "outcome": "running"|"done"|"failed", "error"?: str }`.
  Referenzen, damit der Faden nach einem App-Neustart seine Fortschritts- und
  Ergebnis-Karten aus Job-/Board-Status wieder aufbauen kann.

## Router-Agent (`src/laura/chat/router.py`)

Nutzt `resolve_from_env()` + `build_model_client` — dieselbe Provider-Leiter wie die
Produktion (gpt-4o etc.), kein neues Konfigurationssystem.

**Kontext pro Turn:** Systemprompt (was Laura kann, Werkzeugliste, Regeln), aktives
Projekt (Name, Asset-Zahl, laufende Jobs kompakt), die letzten **20** Nachrichten des Fadens
kompaktiert (Karten als einzeilige Zusammenfassung inkl. Approval-Status).

**Werkzeugkasten (bewusst klein):**

| Tool | Wirkung |
|---|---|
| `reply(text)` | antworten / rückfragen — Default |
| `create_project(name)` | Projekt anlegen, wird aktives Projekt des Fadens |
| `switch_project(ref)` | per Name oder Id wechseln; bei Mehrdeutigkeit → `reply`-Rückfrage |
| `propose_import(urls[])` | erzeugt NUR eine `approval_request`-Nachricht — führt nie aus |
| `start_short(topic, target_seconds?, format?)` | projektweiter Auto-Short (bestehender Endpunkt-Pfad) |
| `start_overview(topic, target_seconds?)` | Auto-Übersicht (bestehender Pfad) |
| `follow_up(session_ref, text)` | Folgerunde via `/production/{sid}/message`-Maschinerie |
| `revert(session_ref, artifact, version)` | Zurückdrehen via Revert-Maschinerie |

**Regeln:**

- Genau ein Tool-Call pro Turn. Unklare Absicht oder fehlendes aktives Projekt → `reply`
  mit Rückfrage, nie raten.
- Discovery-422s („no material found for topic", „target_seconds kürzer als kürzester
  Clip …") gehen **wörtlich** als ehrliche Assistenten-Antwort in den Faden — kein
  Fehlerton, die strukturierten Gründe existieren seit den Ehrlichkeits-Fixes.
- `session_ref` löst der Router aus dem Faden-Kontext auf (die letzte/benannte
  Produktions-`action`); kann er nicht auflösen → Rückfrage.

## Endpunkte (`src/laura/api/chat.py`)

| Route | Verhalten |
|---|---|
| `POST /conversations` | neuer Faden → `{id}` |
| `GET /conversations` | Liste: `id`, `title`, `updated_at` (neueste zuerst) |
| `GET /conversations/{id}` | alle Nachrichten in `seq`-Ordnung + Kopf |
| `DELETE /conversations/{id}` | Faden löschen (Nachrichten kaskadieren) |
| `POST /conversations/{id}/message {text}` | synchroner Router-Turn (~1–3 s): persistiert die User-Nachricht, führt den Tool-Call aus (bzw. legt die Approval-Karte an), antwortet mit den neu entstandenen Nachrichten |
| `POST /conversations/{id}/approvals/{message_id} {decision: approve\|reject}` | **einziger Vollzugsort** eingreifender Aktionen; Details unten |
| `GET /production/{session_id}/events?after=N` | liest das Lauf-NDJSON ab Zeilenindex N (0-basiert, `after=0` = alles): `{events, next, done}` — `next` ist der Cursor für den Folge-Poll, `events` die rohen NDJSON-Objekte, `done` true bei terminalem Lauf. Kein SSE in v1 |

Auth wie überall: `X-Laura-Token`; Fehlerformen wie im Repo üblich (404 unbekannter Faden,
422 Validierung, 409 Approval-Wettlauf).

## Approval-Fluss (serverseitig erzwungen)

Der Vollzugscode für Imports existiert **ausschließlich** hinter dem Approval-Endpunkt —
das Router-Tool `propose_import` schreibt nur die Karte und kann nichts ausführen. Ein
UI-Bug kann nichts auslösen; ein Neustart verliert nichts (Karte + Entscheidung = DB-Zeile).

1. Nutzer: „hol dir diese vier Drive-URLs" → Router → `approval_request` mit URL-Liste
   und Zielprojekt → UI rendert Karte mit **Freigeben / Ablehnen**.
2. **Freigeben** → Backend prüft `status == "pending"` (sonst 409), setzt `approved`,
   startet je URL den bestehenden Import-Pfad (`/assets/import`-Maschinerie → Jobs mit
   Fortschritt), hängt eine `action`-Nachricht mit `asset_ids`/`job_ids` an, setzt die
   Karte auf `executed`.
3. **Ablehnen** → `rejected`; der Router sieht den Status beim nächsten Turn im Kontext.

## Live-Erzählung

Die Agent-Events jedes Produktionslaufs liegen bereits als NDJSON unter
`agent-runs/<sid>/runs/*.ndjson` — der neue Events-Endpunkt macht sie lesbar
(`after`-Cursor = Zeilennummer, `done` = Lauf terminal). Eine laufende Produktions-Karte
pollt im 2,5-s-Takt (App-Muster) und rendert die Events als erzählende Zeilen darunter,
eingeklappt auf die letzten ~5, aufklappbar:

> 🎬 Szene 1 geprüft — Hook 7/10, 3 Fenster · ✍️ Storyline: 3 Kapitel · 🎙 Stimme: 19,8 s · 🎞 Render läuft …

Das Event-Format entspricht dem des v1-Streams — der `EventLine`-Renderer im ChatPanel
wird wiederverwendet, nicht neu erfunden. Endet der Lauf, ersetzt die Ergebnis-Karte die
Erzählung: Export-Verweis, `target_ratio` („33 % der Ziellänge"), QA-Urteil, „▶ ansehen".

## Chat-Bühne (Desktop)

`App.tsx`: `stage === "chat"` wird **Standard**; die Nav führt „💬 Chat" als ersten
Punkt, dahinter unverändert die 7 Views („Werkzeuge"). Neue Komponenten, klein geschnitten:

| Komponente | Verantwortung |
|---|---|
| `ChatStage` | Dreispalter: Chat-Liste \| Faden \| Preview |
| `ConversationList` | Fäden (Titel + Datum), „Neuer Chat", Löschen |
| `ChatThread` | rendert Nachrichten nach `kind`: Text-Blasen, `ApprovalCard`, `ActionCard` |
| `ChatComposer` | Eingabe + Senden; gesperrt, während ein Turn läuft |
| `ApprovalCard` | URL-Liste + Freigeben/Ablehnen; zeigt den persistierten Status |
| `ActionCard` | running → Erzählung (Events-Poll); done → Ergebnis; failed → Grund |
| `ChatPreview` | rechts: Artefakt der fokussierten Karte (Klick fokussiert), Standard = neueste |

**Preview-Inhalte:** Kontaktbogen als Bild (Client-Methode `contactSheetUrl` existiert);
fertige Filme als Video. Dafür lernt das `laura-media://`-Protokoll eine zweite Spur:
**`laura-media://export/{export_id}`** — dieselbe Range-Streaming-Logik, Pfadauflösung über
die Exports-Tabelle. Das schließt nebenbei die bestehende Lücke, dass die App Exporte
bisher gar nicht abspielen kann (der Export-Tab bietet nur „Im Ordner zeigen").

Neue Client-Methoden in `api.ts`: `createConversation`, `listConversations`,
`getConversation`, `deleteConversation`, `sendChatMessage`, `decideApproval`,
`getProductionEvents`.

## Fehlerbehandlung

- **Router-LLM scheitert** → Assistenten-Nachricht mit dem Fehlertext; der Faden bleibt
  intakt, erneut senden = Retry. Der Faden crasht nie an einer Nachricht.
- **Discovery-422** → wörtlich als ehrliche Antwort im Chat (kein Fehlerton).
- **Job stirbt** → `ActionCard` zeigt failed + Grund aus `error_json`; bei Produktionen
  sagen `complete`/`stopped_at`, wo die Kette stand.
- **Approval-Wettläufe** (Doppelklick, zweite UI) → 409; die UI lädt den Nachrichtenstand
  nach und zeigt den echten Status.
- Polling stoppt an Terminalzuständen (Muster `useJobStatus`).

## Tests

**Backend:**
- Repos: Anlegen/Listen/Löschen, `seq`-Ordnung, Kaskade.
- Router mit **Fake-Model-Client** (deterministische Tool-Wahl — Muster der
  Produktions-Agent-Tests): jede Tool-Route einmal; Mehrdeutigkeit → `reply`; 422-Durchreichung.
- Approval-Kette: propose → approve → führt aus (Import-Jobs entstehen, `action`-Nachricht,
  Karte `executed`); reject; Doppel-approve → 409.
- Events-Reader gegen ein NDJSON-Fixture: `after`-Cursor, `done` bei Terminal-Event,
  fehlende Datei → leer statt 500.

**Frontend:**
- `ChatStage` mit gemocktem Client: Senden erzeugt Turns; Approval-Karte ruft
  `decideApproval`; Erzählzeilen erscheinen aus Events; Ergebnis-Karte zeigt Ratio;
  Preview wechselt mit dem Fokus; Neuer-Chat/Löschen wirken auf die Liste.

## Bewusst NICHT in v1

- Kein SSE (Polling ist das Muster der App), kein Streaming der Router-Antwort.
- Kein Feinschnitt-Editing per Chat (Wörter löschen etc. — bleibt im Werkzeug-Modus).
- Kein Projekt-Löschen per Chat (nur anlegen/wechseln — Löschen bleibt ein Werkzeug-Klick).
- Keine Änderung an den 7 Views; der Werkzeug-Modus ist die heutige Oberfläche.
- Keine Mehrfach-Tool-Ketten pro Turn (ein Tool-Call, dann antwortet der Faden).

## Risiken

- **Fehlrouting des Router-LLM** — abgefedert durch kleinen Werkzeugkasten, Rückfrage-Regel
  und serverseitiges Approval für alles Eingreifende.
- **Kontextwachstum langer Fäden** — Kompaktierung der Historie im Router-Kontext;
  Chat-Liste macht frische Fäden billig.
- **Events-Reader vs. rotierende Logs** — der Reader bindet sich an die neueste
  Run-Datei der Session und liefert `done`, sobald der Lauf terminal ist.
