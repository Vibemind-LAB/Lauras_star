# Zentraler Client-Cache für Schnittdaten-Sync (Plan)

_Datum: 2026-06-26 · Status: Entwurf, wartet auf Review · Branch-Ziel: eigener `feat/`-Branch_

## Ziel

Eine **einzige Quelle der Wahrheit** im Renderer für Schnittdaten (Assets, Szenen, Rough-Cut-
Timeline + Clips, Sequenz + Items, Transkript, Audio-Clips). Alle Views (Rough Cut, Feinschnitt,
Zusammenfügen) lesen aus demselben Cache; eine Mutation invalidiert gezielt die abhängigen Daten,
sodass **Schnittdaten immer sync** sind — statt heute pro View unabhängig zu fetchen und über
`reloadKey`-Zähler + `reload()`-Callbacks zu raten.

Das behebt die Klasse von Bugs, von der `2c55a7a` (orphaned `sequence_items`) nur ein Symptom war.

## Problem heute (aus der Architektur-Analyse)

- **Keine zentrale State-Schicht.** Jeder Hook (`useScenes`, `useSequence`, `useRoughCutTranscript`,
  `useAnalysis`, …) fetcht selbst. Kein Dedup, keine geteilte Invalidierung.
- **Mehrfach-Fetches derselben Entität.** Szenen werden von App, RoughCutView (`useScenes`),
  FineCutView (`useRoughCutTranscript`) und AssembleView (`listProjectScenes`) **unabhängig** geladen.
  Rough-Cut-Clips ebenso. → Driften auseinander.
- **Instabile IDs = Wurzel des Desyncs:**
  | Operation | Szenen | Timeline | Clips | Segmente | Wörter |
  |---|---|---|---|---|---|
  | `generateScenes` / `replace_scenes` | **neu** | – | – | – | – |
  | `buildRoughCutFromShots` (rebuild) | – | **neu** | **neu** | – | – |
  | `applyOperation` (split/merge/delete) | – | gleich | **neu** | gleich | – |
  | `deleteWords` / `cutAtFrame` | **neu** | gleich | **neu** | gleich | **neu** |
  | `realignTranscript` | – | – | – | gleich | **neu** |
  | Re-Analyse | – | – | – | **neu** | **neu** |
- **Bekannte Desync-Gefahren (über `2c55a7a` hinaus):**
  1. **Stale Clip-Selektion:** `selectedClipId` in App.tsx wird beim Rough-Cut-Rebuild (neue Clip-IDs)
     nicht zurückgesetzt → zeigt ins Leere.
  2. **Stale Audio-Clips:** FineCut lädt Audio-Clips nur bei `roughCutId`-Wechsel oder VO-Job-Ende,
     **nicht** nach beliebigen Clip-Ops → Lippensync/Overlay driftet.
  3. **Szenen-Churn im Feinschnitt:** `useRoughCutTranscript` holt Szenen einmalig; regenerieren die
     Szenen mitten in der Session, sind die Marker stale.
  4. **Export-Polling fehlt:** ExportView pollt nicht → fertige Exporte erscheinen erst nach manuellem
     Reload.
  5. **Cross-View-Undo:** Undo-Historie ist pro Hook/Timeline; Undo im Feinschnitt invalidiert die
     Szenen-Cache der RoughCutView nicht.

## Ansatz

### Empfehlung: **TanStack Query** (`@tanstack/react-query`)

Cache-aside-Fetch + automatisches **Dedup** + **gezielte Invalidierung** + **stale-while-revalidate**
+ integriertes Polling. Trifft exakt dein „Stand in den Cache laden, dann anpassen, immer sync".
Ersetzt den `reloadKey`/`reload()`-Wildwuchs durch deklarative Query-Keys + `invalidateQueries`.

**Alternativen (bewusst verworfen):**
- **Eigener Context/Zustand-Store:** keine neue Dependency, aber Invalidierung, Dedup und
  stale-while-revalidate müsste man von Hand bauen — genau die Logik, die TanStack erprobt liefert.
- **Status quo lassen + punktuell reconcilen** (wie `2c55a7a`): heilt Symptome, nicht die Klasse.

> **Entscheidung nötig:** TanStack Query (empfohlen) vs. eigener Store. Der Plan unten nimmt
> TanStack an; bei „eigener Store" bleiben Phasen/Keys gleich, nur die Mechanik ändert sich.

### Cache-Design

**Query-Keys (normalisiert nach Entität + ID), Auszug:**
```
["projects"]                         ["assets", projectId]
["analysis", assetId]                ["shots", assetId]   ["transcript", assetId]
["roughCut", projectId, assetId]     ["timeline", timelineId]   ["scenes", timelineId]
["projectScenes", projectId]         ["sequence", projectId]
["sequenceFlattened", sequenceId]    ["sequenceTranscript", sequenceId]
["audioClips", timelineId]           ["exports", projectId]   ["job", jobId]
```

**Invalidierungs-Map (Mutation → invalidiert):**
| Mutation | invalidiert |
|---|---|
| `generateScenes` / `buildRoughCutFromShots` | `scenes(tl)`, `timeline(tl)`, `roughCut(proj,asset)`, `projectScenes`, `sequence`, `sequenceFlattened` |
| `setSequenceScenes` | `sequence`, `sequenceFlattened`, `sequenceTranscript` |
| `applyOperation` (split/merge/delete/trim) | `timeline(tl)`, `scenes(tl)`, `audioClips(tl)`, ggf. `sequenceFlattened` |
| `deleteWords` / `cutAtFrame` | `timeline(tl)`, `scenes(tl)`, `transcript(asset)` |
| `createVoiceover` (bei Job-Ende) | `audioClips(tl)`, `timeline(tl)` |
| `realignTranscript` | `transcript(asset)`, `sequenceTranscript` |
| Import/Analyse fertig | `assets`, `analysis(asset)`, `shots`, `transcript`, `roughCut` |

**Polling** (heute custom): `useQuery({ refetchInterval })` für `job`, `importStatus`; `analysis`
behält seine **Generations-Token-Semantik** (langer Lauf darf keine stale Writes landen) → vorsichtig
migrieren oder vorerst als Sonderfall belassen.

## Inkrementeller Rollout (Strangler-Fig, kein Big-Bang)

Jede Phase ist einzeln lauffähig + testbar; alte Hooks laufen weiter, bis ihre Phase sie ersetzt.

- **P0 — Fundament.** `@tanstack/react-query` hinzufügen, `<QueryClientProvider>` um App,
  `src/cache/queryKeys.ts` (Key-Fabrik) + `src/cache/invalidate.ts` (zentrale Invalidierungs-Helfer).
  Kein Verhaltenswechsel. _Fixt nichts, ermöglicht alles._
- **P1 — Szenen + Sequenz (Desync-Epizentrum).** `useScenes`, `listProjectScenes`, `useSequence`
  auf `useQuery` umstellen; App/RoughCut/Assemble teilen **eine** Szenen-Query. Mutationen
  (`generate`, `setSequenceScenes`) invalidieren statt manuell zu reloaden. _Fixt #3, härtet `2c55a7a`._
- **P2 — Rough-Cut-Timeline + Clips.** `useRoughCutTranscript`-Fetch + App-Rough-Cut auf
  `["timeline", id]` / `["roughCut", …]`. `selectedClipId` beim Timeline-Wechsel leeren. _Fixt #1 + Clip-Churn._
- **P3 — Audio-Clips + Transkript.** `["audioClips", tl]` invalidiert bei **jeder** Clip-Op (nicht nur VO);
  `["transcript"]` / `["sequenceTranscript"]`. _Fixt #2._
- **P4 — Polling-Hooks.** `useJobStatus`, `useImportStatus` → `useQuery({refetchInterval})`; ExportView
  pollt `["exports"]`/`["job"]`. `useAnalysis` zuletzt + behutsam (Generations-Token). _Fixt #4._
- **P5 — Aufräumen.** `reloadKey`/`setReloadKey`/manuelle `reload()`-Callbacks + toten Code entfernen.

## Invarianten (nicht verhandelbar)

- **Frame-genau bleibt frame-genau.** Cache ändert nur *wann* gefetcht wird, nie die Daten/Form.
- **`LauraClient` (api.ts) bleibt unverändert** — der Cache ruft dieselben Methoden als `queryFn`.
- **Local-first/offline:** QueryClient mit `retry` konservativ; kein Online-Zwang.
- **Determinismus/Idempotenz** der Backend-Ops unberührt.
- **Generations-Token** von `useAnalysis` darf nicht durch naive Migration kaputtgehen.

## Tests

- **Pro Query/Hook:** Render-Test mit gemocktem `LauraClient` (lädt, cached, dedupliziert).
- **Invalidierung:** Mutation X → die laut Map abhängigen Queries refetchen (Mock-Client-Call-Counts).
- **Desync-Regression:** je Hazard (#1–#4) ein Test, der den Stale-Zustand erzeugt und beweist, dass
  der Cache ihn nach der Mutation/Navigation heilt. (#5 separat.)
- **Bestehende Komponenten-Tests** (AssembleView 13, FineCutView 9, …) müssen grün bleiben — pro Phase
  laufen lassen. `tsc` strikt, kein `any`.

## Risiken

- **Bundle:** react-query ~13 kB gz — vernachlässigbar in Electron.
- **`useAnalysis`-Migration** ist der heikelste Punkt (langer Poll + Generations-Token) → zuletzt, mit
  eigenem Test; zur Not als nicht-migrierter Sonderfall belassen.
- **Über-Invalidierung** (zu breit) → unnötige Refetches; die Map oben bewusst eng halten.
- **Doppelter State** während der Migration (alter Hook + Query parallel) → Phasen klein schneiden,
  pro Phase den alten Pfad vollständig entfernen.

## Aufwand + Reihenfolge

P0 klein · P1/P2 je mittel (das Gros des Werts) · P3/P4 klein–mittel · P5 Cleanup. Empfohlen genau in
dieser Reihenfolge — P1 liefert sofort den größten Sync-Gewinn (Szenen/Sequenz), der Rest baut darauf.

## Offene Entscheidungen (vor der Umsetzung)

1. **TanStack Query** (empfohlen) vs. eigener Context/Zustand-Store?
2. `useAnalysis` jetzt mit-migrieren oder als Sonderfall belassen?
3. Umsetzung als **SDD** (Subagenten pro Phase, mit Review) oder direkt von mir, Phase für Phase?
