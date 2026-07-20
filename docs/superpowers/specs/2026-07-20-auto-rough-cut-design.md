# Auto-Rough-Cut: Nach der Analyse steht der erste Schnitt von selbst

**Datum:** 2026-07-20 · **Status:** OBSOLET — bereits implementiert (Befund unten) ·
**Ansatz:** A (eigener Job, gekettet an den Analyse-Erfolg), vom User gewählt.

> **Befund bei der Plan-Recherche (2026-07-20):** Das Feature existiert vollständig und ist
> standardmäßig aktiv. `handle_analysis_run` ruft am Erfolgs-Ende
> `autobuild_asset_edit_ready` (scenes/build.py:279) — get_or_create Rough-Cut, Clips aus
> behaltenen Shots, Szenen-Gruppierung, Auto-Tighten — gated über die Asset-Policy
> (`policy.mode`, Default **auto**, P4-T2), mit `diagnostics["auto_rough_cut"]` im
> Analyse-Ergebnis. Empirisch bestätigt im Livetest-Workspace: alle fünf analysierten Assets
> tragen Policy-Zeilen (`auto`, Quelle env) und auto-gebaute Rough-Cuts mit Szenen (z. B.
> Asset 3a098e6b: 6 Clips / 6 Szenen, 2026-07-16). Die „Lücke" stammte aus einer veralteten
> Memory-Notiz von vor der Autopilot-Portion (PR #9). Es wird kein Implementierungsplan
> geschrieben; dieses Dokument bleibt als Entscheidungs- und Befund-Protokoll.

## Problem

Der Import startet die Analyse bereits automatisch (`_maybe_auto_analyze` am Ende der
Import-Kette, ~18s GPU, 0 Klicks). Danach reißt die Kette ab: Szenen und Rough-Cut baut erst
ein manueller Klick (`POST /projects/{pid}/timelines/from-shots` + `scenes:generate`). Die
Lücke kostet genau den Moment, in dem ein frisch importiertes Video „einfach da" sein sollte.

## Entscheidungen (User)

1. **Nur der ERSTE Cut ist automatisch.** Existiert zum Asset schon irgendein Rough-Cut
   (auto, manuell oder agentisch), überspringt die Automatik still. Mehrere Cuts pro Video
   bleiben uneingeschränkt möglich — über die bestehenden manuellen/agentischen Wege.
2. **Backend-only.** Keine Frontend-Änderung; die UI sieht den Cut beim nächsten Refresh.
3. **Ansatz A**: eigener Job `edit.rough_cut`, enqueued vom Analyse-Handler. (B „inline im
   Analyse-Handler" verworfen: Edit-Logik im Analyse-Handler, Cut-Fehler färbt auf das
   Analyse-Ergebnis ab, bricht das Ketten-Muster. C „Frontend triggert" verworfen:
   widerspricht Entscheidung 2, Race-anfällig.)

## Design

### 1. Trigger & Idempotenz (`analysis/handlers.py`)

`_maybe_auto_rough_cut(ctx, asset_id, run_id)` — aufgerufen am Erfolgs-Ende von
`handle_analysis_run`, nur wenn die Scene-Stage lief (ohne Shots gibt es nichts zu
schneiden). Spiegel von `_maybe_auto_analyze`:

- Opt-out: `LAURA_AUTO_ROUGH_CUT=0` (Werte wie beim Analyze-Opt-out: `0/false/no/off`).
- **Skip-Prüfung zweistufig** — beide Wege gelten als „es existiert schon ein Cut":
  1. Ein `rough_cut`-Timeline mit `created_from=asset_id` (schneller Pfad; deckt Auto-Cuts
     und den RV-Flow `get_or_create_asset_rough_cut`).
  2. Irgendein `rough_cut`-Timeline des Projekts, dessen Clips das Asset sourcen (ein
     `EXISTS`-Query über die Clip-Tabelle) — fängt manuelle `from-shots`-Cuts, die
     `created_from` NICHT setzen.
- Enqueue `edit.rough_cut` mit Payload `{asset_id, analysis_run_id}`, Idempotency-Key
  `rough_cut:{asset_id}:{run_id}`, `caused_by_job_id=ctx.job_id`. Rückgabe Job-Id oder
  `None` (geskippt); die Job-Id landet im Analyse-Job-Result (`rough_cut_job`).

### 2. Wiederverwendung statt Extraktion (Amendment nach Exploration)

Die ursprünglich geplante Extraktion aus dem `from-shots`-Endpoint entfällt: Es existieren
bereits API-freie Kerne, die exakt den manuellen „Szenen erzeugen"-Flow tragen —

1. `repos.get_or_create_asset_rough_cut(db, project_id, asset_id)` → neuester
   `rough_cut`-Timeline mit `created_from=asset_id`, wird bei Bedarf angelegt.
2. `populate_rough_cut_from_shots(db, timeline_id, asset_id, run_id)`
   (`laura/scenes/build.py`) → Clips aus den behaltenen Shots, **No-Op bei gefüllter
   Timeline**, inklusive editorialer Schnittplatzierung (deren Docstring nennt den
   Zero-Click-Import bereits als Zweck).
3. `group_timeline_scenes(db, project_id=…, timeline_id=…, asset=…, run_id=…, clips=…)`
   (`laura/scenes/build.py`) → Szenen-Gruppierung mit Default-Gap.

Der `from-shots`-Endpoint bleibt **komplett unberührt** (sein Quality-Toggle-Filter ist
API-Komfort; die `keep`-Flags der Analyse wirken in `populate` genauso). Der Auto-Cut ist
damit byte-gleich zu dem, was der manuelle Flow heute baut.

### 3. Job-Handler (`edit.rough_cut`)

Neuer Job-Kind auf der CPU-Queue (`queue_for`-Default). Handler komponiert die drei Kerne
aus §2 in genau der Reihenfolge des manuellen Flows:

1. `get_or_create_asset_rough_cut` (liefert die `created_from`-Timeline; angelegt mit dem
   Repo-Default-Namen).
2. `populate_rough_cut_from_shots` — leer → Clips; schon gefüllt → No-Op (Race mit einem
   manuellen Klick löst sich von selbst).
3. `group_timeline_scenes` mit den frischen Clips.
4. Ergebnis: `{"ok": True, "timeline_id": ..., "clips": n, "scenes": m}`.

### 4. Fehler-Semantik

- **Gesunde Skips** (nie ein Job-Fehler, der Runner wertet nur den `error`-Key/`hard_fail`):
  keine behaltenen Shots (Demo-Clips!), kein Scene-Ergebnis am Run, Asset/Projekt
  inzwischen gelöscht, Cut existiert inzwischen doch (Race mit einem manuellen Klick) →
  `{"ok": False, "skipped": "<konkreter Grund>"}`.
- Nur echte Exceptions (DB kaputt, Bug) failen den Job — sichtbar als failed Job, wie überall.
- Ein Cut-Fehler berührt den Analyse-Job nie: eigener Job, eigenes Ergebnis.

### 5. Tests

- Handler, echter DB-Seed: Analysis-Run mit Shots → Handler baut Timeline (`created_from`
  gesetzt, Name „Rough Cut (Auto)", Clips = behaltene Shots) + Szenen; Result-Shape.
- Skips einzeln: Opt-out-Env; existierender `created_from`-Cut; existierender Cut, der das
  Asset nur über Clips referenziert (ohne `created_from`); keine behaltenen Shots →
  `skipped`, Job succeeded.
- Kette: `handle_analysis_run`-Erfolg enqueued genau einen `edit.rough_cut` (Idempotency-Key
  verhindert Doppel bei Analyse-Retry); Scene-Stage aus → kein Enqueue; Opt-out → kein
  Enqueue.
- Kein Endpoint wird angefasst — die bestehenden `from-shots`- und `scenes:generate`-Tests
  bleiben unberührt grün (Voll-Suite-Gate).

## Nicht in diesem Scope

- Frontend-Änderungen (Entscheidung 2) — die UI sieht den Cut beim nächsten Laden.
- Mehrfach-Auto-Cuts oder Auto-Rebuild nach Re-Analyse (Entscheidung 1).
- Änderungen an Quality-Filter-Defaults, Split-Cut-Empfehlungen oder dem v2-Production-Board.
- Der Auto-Analyze-Trigger selbst (`_maybe_auto_analyze`) bleibt unangetastet.
