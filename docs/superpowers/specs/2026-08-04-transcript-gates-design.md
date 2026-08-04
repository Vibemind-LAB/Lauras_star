# Transkript-Gates: Text-first-Pipeline im Chat — Design

**Datum:** 2026-08-04 · **Status:** Entwurf zur Review

## Warum

Die Video-Produktions-Session vom 2026-08-04 hat gezeigt: Die Filme werden gut, wenn
(1) der Text auf dem echten Transkript steht statt erfunden zu werden, (2) der User den
Text sieht und freigibt, BEVOR Voice und Render Geld und Zeit kosten, und (3) die
Szenenauswahl dem bestätigten Text folgt statt umgekehrt. Heute leistet das ein Operator
von Hand; dieser Arc macht es zum Standard-Chat-Flow („Lovable für Videos").

## Architektur-Überblick

Neue Reihenfolge der Produktionskette mit zwei Freigabe-Gates, beide über die
bestehende Chat-Karten-Maschinerie (Muster: Kontaktbogen-Checkpoint):

```
Import → Auto-Analyse → [GATE A: Quell-Transkript bestätigen]
      → Script (geerdet, Budget-gefüllt) → [GATE B: Sprechertext freigeben]
      → Szenen-Feinauswahl auf bestätigtem Text → Voice → Cutlist → Render
```

## Komponente 1: Gate A — Quell-Transkript

- Nach erfolgreicher Transkription zeigt der Chat eine „Transkript prüfen"-Karte
  (kind `action`, tool `confirm_transcript`): Segmentliste mit Text, scrollbar.
- Korrekturen per Chat-Nachricht („ersetze ‚Carpati' durch ‚Karpathy'", „Segment 12:
  ‚cloud code' → ‚Claude Code'"). Der Executor mappt auf einen neuen Endpunkt
  `POST /assets/{aid}/transcript-corrections`, der `transcript_words.text` patcht
  (wortweise, mit Audit-Spur in einer neuen Tabelle `transcript_corrections`:
  word_id, old_text, new_text, corrected_at).
- Nach Patch: Re-Index der betroffenen Segmente in Qdrant (bestehender
  Backfill-Pfad, segment-scoped).
- Bestätigung setzt `media_assets.transcript_confirmed_at`. Produktionsläufe auf
  unbestätigten Transkripten laufen weiter, tragen aber eine sichtbare Warnung in
  `config_warnings` („Transkript unbestätigt").

## Komponente 2: Gate B — Sprechertext

- Die Produktionskette erhält einen Checkpoint NACH dem Script (gleicher Mechanismus
  wie der Kontaktbogen-Checkpoint): Lauf endet mit `resume_point="script"`, done-Event
  trägt `script_version`.
- Die ActionCard rendert im Script-Zustand die Zeilen szenenweise (Kapitel · Szene ·
  Text) mit „Freigeben"-Weg per Chat: „passt" → Folgerunde setzt fort;
  „Zeile 3 anders: …" → follow_up editiert via `save_script_chapter` und pausiert
  erneut am Gate.
- Erst nach Freigabe laufen Voice-Synthese (Kosten!), Cutlist und Render.

## Komponente 3: Text-first-Szenenauswahl

- Storyline wird ZUM bestätigten Script gebaut (nicht Script zur Storyline):
  ein deterministischer Matching-Schritt ordnet jeder Script-Zeile die Szene/das
  Fenster mit der besten Transkript-Übereinstimmung zu (lexikalisch + semantisch
  über den bestehenden Discovery-Kern); das Team validiert nur noch.
- Framing-Default: `zoom="off"` (Vollbild/Letterbox) — kommt aus dem laufenden
  Framing-Chip; Zooms nur auf expliziten Wunsch.
- Längen-Kontrakt: `script_budget` ist Pflicht vor dem Schreiben; die Wortrate wird
  aus dem Timings-Sidecar der letzten Synthese gemessen statt geschätzt
  (Kalibrier-Fund: 95 Wörter ≈ 50s statt versprochener 60s).

## Komponente 4: Secondbrain für die Agenten (read-only)

- Zwei neue FunctionTools fürs Produktionsteam: `search_second_brain(query)` und
  `read_brain_note(name)` — direkte Dateisystem-Suche auf dem Vault
  (Markdown-Verzeichnis), KEIN MCP-Umweg im Backend.
- Env-gated: `LAURA_SECONDBRAIN_PATH` (unset ⇒ Tools nicht registriert), Konvention
  „optionale Extras". Ausschließlich lesend.
- Zweck: korrekte Produkt-/Eigennamen und Fakten im Script (VibeMind, Agent Farm,
  Rowboat/Skipper, Hands …). Kontrolle bleibt beim User: alles, was aus dem Vault in
  den Text fließt, passiert Gate B.

## Fehlerbehandlung

- Gate-Karten sind idempotent: doppelte Bestätigung → 409 wie bei Import-Approvals.
- Transcript-Patches validieren word_ids; unbekannte → 404 mit Segmentliste.
- Ein abgebrochener Lauf am Gate bleibt wiederaufnehmbar (resume_point), Karten
  zeigen den persistierten Zustand nach Thread-Reload.

## Tests

- Backend: Patch-Endpunkt (Wort-Patch + Audit + confirmed_at), Gate-B-Pause
  (done mit resume_point script), Matching-Schritt (Zeile → Szene deterministisch),
  Secondbrain-Tools (Suche/Read, env-gated, Pfad-Traversal abgewehrt).
- Frontend: Transkript-Karte rendert Segmente, Script-Karte rendert Zeilen,
  Freigabe-Fluss über den Chat (vitest, Muster der bestehenden Karten-Tests).

## Bewusst NICHT in v1

- Video-Generierung als B-Roll-Quelle (ComfyUI/LTX ist angebunden; Einhängung in
  die Szenenauswahl kommt als eigener Arc).
- Avatare/Lipsync auf die generierte Voice (Panels existieren als Bausteine).
- Transkript-Editor-UI im Werkzeug-Modus (v1 editiert per Chat-Nachricht).

## Abhängigkeiten / Reihenfolge

Setzt auf denselben Dateien auf wie die laufenden Chip-Sessions (Framing-Hebel,
Grounding-Kontrakt, SQLite busy_timeout, Board-Heilung). Diese Chips landen ZUERST;
dieser Arc rebased darauf. Merge-Ziel: feat/generate-ui.
