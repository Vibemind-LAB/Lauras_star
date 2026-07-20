# Auto-Rough-Cut: Nach der Analyse steht der erste Schnitt von selbst

**Datum:** 2026-07-20 · **Status:** vom User freigegeben · **Ansatz:** A (eigener Job,
gekettet an den Analyse-Erfolg), vom User gewählt.

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

### 2. Kern-Extraktion (`editing/rough_cut_build.py`, neu)

Der Build-Kern des Endpoints `timeline_from_shots` (`api/timelines.py`) wandert in eine
freie Funktion: Shots des Analysis-Runs laden → Quality-Filter (Default an, wie der
Endpoint) → Clip-Rows (ein Clip pro behaltenem Shot, back-to-back, end-exclusive, speed
1/1) → Timeline anlegen bzw. befüllen → OTIO schreiben. Signatur liefert Timeline-Row +
kept/dropped-Shots zurück. Der Endpoint behält seine Response-Extras (Split-Cut-Empfehlungen,
`dropped`-Liste, Quality-Verdict) und ruft dieselbe Funktion — Verhalten byte-gleich, die
bestehenden Endpoint-Tests bleiben unverändert grün. Kein API-Import im `editing/`-Modul
(Schichtung: editing kennt db/analysis, nie api).

### 3. Job-Handler (`edit.rough_cut`)

Neuer Job-Kind auf der CPU-Queue (`queue_for`-Default). Handler:

1. Kern aus §2 mit `created_from=asset_id`, Name **„Rough Cut (Auto)"**.
2. Danach Szenen auf dem frischen Timeline: das pure `group_into_scenes` + Scene-Repos —
   dasselbe, was `scenes:generate` tut (auch hier: Kern nutzen, nicht den API-Endpoint).
3. Ergebnis: `{"ok": True, "timeline_id": ..., "clips": n, "scenes": m}`.

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
- Extraktions-Äquivalenz: die bestehenden `from-shots`-Endpoint-Tests laufen unverändert
  grün (das IST der Regressions-Beweis der Extraktion; keine neuen Endpoint-Tests nötig).

## Nicht in diesem Scope

- Frontend-Änderungen (Entscheidung 2) — die UI sieht den Cut beim nächsten Laden.
- Mehrfach-Auto-Cuts oder Auto-Rebuild nach Re-Analyse (Entscheidung 1).
- Änderungen an Quality-Filter-Defaults, Split-Cut-Empfehlungen oder dem v2-Production-Board.
- Der Auto-Analyze-Trigger selbst (`_maybe_auto_analyze`) bleibt unangetastet.
