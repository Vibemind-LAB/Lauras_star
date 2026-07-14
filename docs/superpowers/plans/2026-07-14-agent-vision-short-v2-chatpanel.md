# Agent-Vision-Short v2 — ChatPanel-Sessions (Slice 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Slice 5 der Spec [2026-07-13-agent-vision-short-v2-design.md](../specs/2026-07-13-agent-vision-short-v2-design.md): das **ChatPanel** kann v2-Produktions-Sessions starten, den Board-Fortschritt als Chips zeigen (Job-Polling — überlebt Reloads), und Folge-Nachrichten (adjust/revert als Freitext) in dieselbe Session schicken. Live-Validierung = Slice 6.

**Architecture:** Drei Schichten, additiv zum bestehenden v1-Stream-Pfad: (1) `api.ts` bekommt die drei Session-Methoden + Typen; (2) ein Hook `useProductionSession` kapselt Session-Zustand (sessionId/jobId persistiert in `localStorage` pro Asset, Poll-Loop über Job-Status + Board-Status); (3) `ChatPanel` bekommt einen Session-Modus-Umschalter — v1-Stream bleibt Default und unverändert. Kein SSE: Polling (Job alle ~2,5s bis terminal, Board-Status je Poll) — genau das macht Sessions reload-fest.

**Tech Stack:** TypeScript strict (niemals `any` — `unknown` + narrowen) · React · Tailwind · vitest (Desktop-CI führt vitest aus → `pnpm test` ist Pflicht-Gate).

## Global Constraints

- **v1-Stream-Pfad unangetastet:** bestehende ChatPanel-Funktionen (streamAutoShort, AgentBubble, DoneCard …) und ihre Tests bleiben grün und unverändert; der Session-Modus ist ein zusätzlicher Zweig.
- TS strict, kein `any`; neue API-Typen explizit; Fehlerpfade (404/503/Netz) landen als lesbare Panel-Meldung, nie als Crash.
- Backend-Verträge (nicht raten — implementiert und getestet in Slice 4): `POST /assets/{id}/production` `{task, target_seconds}` → 202 `{session_id, job_id}`; `POST /production/{sid}/message` `{text}` → 202 `{session_id, job_id}`; `GET /production/{sid}` → `{meta:{session_id,asset_id,created_utc,task,format,target_seconds,status}, scene_reviews:{count,scenes:number[]}, artifacts:{storyline|script|voice|cutlist|render_report|qa_report: {version:number|null, archived_versions:number[]}}, resume_point:string}`. Job-Status über die BESTEHENDE Job-Methode des Clients (in api.ts nachsehen: getJob/jobStatus — Namen übernehmen).
- Arbeitsverzeichnis Frontend-Kommandos: `apps/desktop/` (`pnpm typecheck`, `pnpm test`). Backend läuft für diese Slice NICHT (alle Tests mocken fetch).
- Conventional Commits, explizite `git add`-Pfade; Doku Deutsch, Code Englisch.

**Referenzdateien (vor jedem Task lesen):** `apps/desktop/src/api.ts` (LauraClient-Muster: fetch mit X-Laura-Token, Fehlerbehandlung, bestehende Job-Methode), `apps/desktop/src/components/ChatPanel.tsx` + `ChatPanel.test.tsx` (Struktur, Test-Muster mit gemocktem Client), ggf. `apps/desktop/src/hooks/` für Hook-Konventionen.

---

### Task F1: `api.ts` — Session-Methoden + Typen

**Files:** Modify `apps/desktop/src/api.ts` · Test `apps/desktop/src/api.production.test.ts` (neu; Muster eines bestehenden api-Tests spiegeln, sonst neben ChatPanel.test.tsx anlegen wie die Nachbarn es tun)

**Interfaces (Produces):**

```ts
export interface ProductionArtifactState { version: number | null; archived_versions: number[] }
export interface ProductionStatus {
  meta: { session_id: string; asset_id: string; created_utc: string; task: string;
          format: string; target_seconds: number; status: string };
  scene_reviews: { count: number; scenes: number[] };
  artifacts: Record<"storyline" | "script" | "voice" | "cutlist" | "render_report" | "qa_report",
                    ProductionArtifactState>;
  resume_point: string;
}
export interface ProductionCreated { session_id: string; job_id: string }
// LauraClient methods:
createProduction(assetId: string, task: string, targetSeconds?: number): Promise<ProductionCreated>
sendProductionMessage(sessionId: string, text: string): Promise<ProductionCreated>
getProductionStatus(sessionId: string): Promise<ProductionStatus>
```

Fehlersemantik wie die Nachbar-Methoden (Nicht-2xx → Error mit Status/Detail-Text). TDD: Tests mocken `fetch` (Aufruf-URL, Methode, Header, Body, Response-Mapping; ein 404-Fall je Methode). Commit: `feat(desktop): production session client methods`.

---

### Task F2: `useProductionSession`-Hook

**Files:** Create `apps/desktop/src/hooks/useProductionSession.ts` · Test `apps/desktop/src/hooks/useProductionSession.test.ts`

**Produces:**

```ts
export type ProductionPhase = "idle" | "running" | "done" | "error";
export interface ProductionSessionState {
  phase: ProductionPhase;
  sessionId: string | null;
  jobId: string | null;
  status: ProductionStatus | null;   // letzter Board-Stand
  jobResult: unknown | null;         // result_json des terminalen Jobs (ok/weak/export_id …)
  error: string | null;
}
export function useProductionSession(client: LauraClient, assetId: string | null): {
  state: ProductionSessionState;
  start(task: string, targetSeconds?: number): Promise<void>;
  sendMessage(text: string): Promise<void>;
  reset(): void;                      // Session vergessen (localStorage-Eintrag löschen)
}
```

Verhalten: `start` → createProduction, sessionId/jobId in `localStorage` (`laura.production.<assetId>`), phase running, Poll-Loop starten. Poll-Loop: alle 2500 ms Job holen (bestehende Client-Job-Methode); zusätzlich `getProductionStatus` (Fehler beim Status-Poll nur loggen/ignorieren solange der Job läuft — das Board existiert erst nach Job-Start). Job terminal `succeeded` → phase done, jobResult setzen, letzten Status holen; `failed` → phase error mit Fehlertext. `sendMessage` → sendProductionMessage (neuer jobId, phase running, Loop weiter). Mount mit vorhandenem localStorage-Eintrag → Session wiederaufnehmen (Status holen; wenn der gespeicherte Job noch läuft, weiterpollen; sonst phase done mit Status). Unmount/asset-Wechsel → Timer aufräumen; **kein Poll-Leak** (vitest mit fake timers testet: start→poll→done stoppt Timer; unmount stoppt Timer). Kein `any`; Timer via `window.setInterval`-Rückgabetyp `number`.

Tests (fake timers + gemockter Client als Objekt mit vi.fn()): start-happy (Reihenfolge create→poll→done, localStorage gesetzt), resume-from-storage, sendMessage startet neuen Poll, Fehlerpfad (create wirft → phase error), Timer-Cleanup. Commit: `feat(desktop): useProductionSession hook (job+board polling, reload-safe)`.

---

### Task F3: ChatPanel-Integration — Session-Modus

**Files:** Modify `apps/desktop/src/components/ChatPanel.tsx` · Test erweitern `apps/desktop/src/components/ChatPanel.test.tsx`

**Verhalten:**
- Kopfzeile bekommt einen kleinen Umschalter (zwei Buttons/Tabs): `Stream (v1)` — Default, unverändert — und `Session (v2)`.
- Session-Modus rendert: (a) Eingabefeld + Start-Button (nutzt `useProductionSession.start` mit dem eingegebenen Task-Text); (b) während running: Spinner-Zeile mit `resume_point` („⚙ script …") + **Board-Chips** aus `status.artifacts`: Reviews `🎬 {scene_reviews.count}`, je Singleton mit `version !== null` ein Chip `storyline v2` etc.; `render_report` vorhanden → Chip mit Export-Kurz-Id; (c) done: DoneCard-ähnliche Karte (ok/weak aus jobResult via defensivem Narrowing von `unknown`) + Eingabefeld für **Folge-Nachrichten** (sendMessage), Placeholder `„z. B. Kapitel 2 andere Szene — oder: zurück zu storyline v1"`; (d) error: rote Meldung + Retry-Hinweis.
- Bestehende v1-Renderpfade und Tests unverändert (nur der Umschalter kommt oben dazu — bestehende Tests dürfen höchstens um den Default-Modus-Wrapper ergänzt werden, Assertions bleiben).
- Kein Live-Backend in Tests: Hook wird über einen gemockten Client getrieben (wie ChatPanel.test.tsx den Client heute mockt — Muster übernehmen).

Tests: Umschalter zeigt Session-UI; running-Zustand rendert resume_point + Chips (Status-Fixture mit storyline v2 + 5 Reviews); done rendert Folge-Input; Folge-Nachricht ruft sendMessage mit Text. Commit: `feat(desktop): ChatPanel v2 session mode (board chips + follow-up input)`.

---

### Task F4: Gesamt-Verifikation Slice 5

- [ ] `pnpm typecheck` (apps/desktop) → 0 Fehler.
- [ ] `pnpm test` → alle vitest grün (v1-ChatPanel-Tests unverändert grün).
- [ ] Backend-Suite unberührt: `cd services/local-api && uv run pytest -q` → exit 0 (nichts Backend-seitiges angefasst).
- [ ] Ledger-Eintrag. Manuelle Live-Prüfung (echte Session in der App) ist ausdrücklich **Slice 6**.
