# Revert in die UI: der Provenienz-Kreis schließt sich

**Datum:** 2026-07-21 · **Status:** vom User freigegeben · **Scope:** Punkt D der
Restpunkte-Liste (nach dem Härtungs-Batch A+B+C; E „Auto-Short" folgt als eigener Zyklus).

## Problem

Die Chips zeigen seit dem Provenienz-Arc stale/restored — aber es gibt keinen Weg für den
User, darauf zu HANDELN: `revert_artifact` existiert nur als Agenten-Tool (ein Revert per
Chat-Nachricht kostet einen nondeterministischen Team-Lauf). Dabei liegt alles bereit: der
Status liefert `archived_versions` pro Artefakt, und `Board.restore_coherent_suffix()` heilt
den Suffix deterministisch — ganz ohne Agenten.

## Entscheidungen (User)

1. **UI-Form: Chip-Aktion** — Klick auf einen Artefakt-Chip öffnet ein kleines Dropdown der
   archivierten Versionen mit Zurückdrehen-Bestätigung (kein eigenes Versions-Panel).
2. Der Zyklus beginnt mit dem **Typed-Contract-Sync** (die aus dem Härtungs-Review vertagten
   Minors): `warnings?: string[]` auf den beiden Enqueue-Responses, `target_ratio?: number`
   am Artefakt-Status — plus die Typen, die dieser Zyklus selbst braucht.

## Design

### 1. Endpoint: `POST /production/{session_id}/revert`

Body `{artifact: str, version: int}`. Reihenfolge der Prüfungen:

1. Session unbekannt oder Board fehlt → **404** (wie der Status-Endpoint).
2. **Laufender Job auf der Session → 409** („run in progress — revert would race the team").
   Prüfung über `latest_job_id` wie im Status-Endpoint; `queued`/`running` blockieren.
3. Unbekannter Artefakt-Name → **422** mit der Liste gültiger Namen (Tool-Parität).
4. Nie archivierte Version → **422** mit `"no archived <name> v<version>"` (Tool-Parität).

Dann synchron, ohne Job und ohne Agenten:

```python
invalidated = <präsente downstream-Artefakte VOR dem Revert>   # wie revert_artifact
board.revert(artifact, version)
restored = board.restore_coherent_suffix()
```

Response `200`: `{ok: true, artifact, version, invalidated, restored, status}` — `status` ist
der komplette `Board.status()`-Block wie beim GET, sodass die UI ohne zweiten Fetch
aktualisiert. `storyline` bleibt revertierbar (Tool-Parität); sie restauriert wie gehabt nie
automatisch, ihr Downstream ggf. schon (der Walk prüft die Eltern-Hashes).

### 2. Frontend: Chip-Aktion im ChatPanel

- `api.ts`: `revertProduction(sessionId, artifact, version)` → typisierte Response
  (`invalidated`/`restored`/`status`); Typed-Contract-Sync als eigener erster Commit
  (`ProductionCreated.warnings?: string[]`, Message-Response analog,
  `ProductionArtifactState.target_ratio?: number`, `archived_versions: number[]` falls noch
  untypisiert).
- ChatPanel/SessionChips: Klick auf einen Artefakt-Chip mit nicht-leeren
  `archived_versions` öffnet ein kleines Dropdown („v1 · v2 · …", aktuelle Version markiert);
  Auswahl + Bestätigen ruft den Endpoint. Danach: Chips rendern aus `response.status`,
  `response.restored` erscheint als ♻️-Hinweis (bestehendes Muster des restored-Chips).
  Chips ohne archivierte Versionen bleiben reine Anzeige (kein leeres Dropdown).
- Fehlerpfade: 409 → kurzer Hinweis „Lauf aktiv — warte, bis der Job fertig ist"; 422 →
  Reason anzeigen. Kein Retry-Automatismus.

### 3. Tests

- Backend (`tests/`): Happy-Path — Board mit voller Kette, Cutlist auf v1 zurück →
  `invalidated` enthält sheet/render/qa, `restored` bringt den kohärenten Suffix zurück,
  `status` im Response aktuell; 404 (Session/Board), 409 (Job queued und running), 422
  (Name/Version); `storyline`-Revert restauriert die Storyline nicht, heilt aber präsente
  Downstream-Kohärenz gemäß Walk.
- Frontend (vitest): Dropdown erscheint nur bei archivierten Versionen; Auswahl ruft
  `revertProduction` mit den richtigen Argumenten; Chips aktualisieren aus der Response;
  409/422 zeigen den Hinweis. `pnpm test` + `tsc --noEmit` (Desktop-CI fährt vitest).

## Nicht in diesem Scope

- Kein Diff-/Preview der Versionen (nur Versionsnummern im Dropdown).
- Kein Revert über den Agenten-Chat hinaus verändert (`revert_artifact` bleibt unangetastet).
- Kein Auto-Rebuild nach dem Revert (der User entscheidet, ob/was er als Nächstes anstößt;
  der Restore-Walk holt nur, was hash-kohärent ist).
- Keine Versionshistorie-Ansicht (das wäre das verworfene Panel).
