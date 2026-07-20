# Fenster-Bias: gehaltene Screens verlieren Bildzeit an Bewegung

**Datum:** 2026-07-20 · **Status:** vom User freigegeben · **Portion:** 20.E (letzter offener
Punkt) · **Ansatz:** A (Prompt-Rubrik + Gewichts-Entkopplung + Messung), vom User gewählt.

## Problem

Das Review-VLM ist Reel-trainiert: Es bewertet Bewegung. Für den Score wurde das bereits
korrigiert („stillness is not a penalty", Remainder-Runde), für die **Fensterlängen** nicht —
und die Fensterlänge wirkt im Schnitt als Gewicht: `_segment_duration_s` deckelt das
Basisgewicht eines Segments bei `max(window.duration_s, 2s)`, die Ein-Faktor-Skalierung in
`_scale_chapter_durations` erhält die Proportion. Ein gehaltener, lesbarer Screen — in einer
Screen-Recording-Produktion oft der informationstragende Shot — bekommt anteilig wenig
Bildzeit gegenüber einem Bewegungsfenster im selben Kapitel.

## Baseline (gemessen 2026-07-20, 88 echte VLM-Reviews aus 12 Livetest-Läufen)

Regex-Klassifikation „static" über description/whats_happening (`no significant change`,
`static`, `remains`, `unchanged`, `slightly`, `stationary`, `little change`):

| Gruppe  | n  | Median der Fenster-Mediane | Median hook_score | Reviews mit Sub-Sekunden-Fenster |
|---------|----|----------------------------|-------------------|----------------------------------|
| static  | 36 | 2,00 s                     | **5,0**           | **8**                            |
| moving  | 52 | 2,38 s                     | **6,5**           | 5                                |

Ankerfälle: Szene 1 (45s Org-Chart, VLM schreibt selbst „no significant changes") bekam in
den Film-Läufen `ee65e23a` und `1f0438b8` **drei 0,5s-Fenster und hook_score 3**. Dieselbe
Szene bekam in anderen Läufen 15s- oder 45s-Fenster — die VLM-Fensterlänge ist hochvariant
(0,5s/15s/45s für identisches Material). Alle Baseline-Reviews entstanden VOR dem
Score-Rubrik-Fix; der Score-Teil misst also den unbehandelten Zustand, die Fensterlängen den
heute noch unbehandelten.

## Entscheidungen (User)

1. **Messen + fixen in einem** — Baseline aus den vorhandenen Boards (oben), Fix per TDD;
   der Nachher-Vergleich läuft im nächsten regulären Live-Lauf, **kein** Live-Lauf in dieser
   Portion.
2. **Ansatz A**: beide Wurzeln — Prompt-Rubrik für Fensterlängen UND Code-Entkopplung der
   Gewichte. (B „nur Prompt" verworfen: 7b-Compliance ist probabilistisch, der strukturelle
   Verstärker bliebe. C „Staticness aus Embeddings" verworfen: neue Kopplung, YAGNI.)

## Design

### 1. Prompt: Fenster-Rubrik (`_REVIEW_PROMPT`, production_tools.py)

Die `windows`-Anweisung erhält die Anti-Reel-Regel, die der Score schon hat: ein gehaltener
Screen, dessen Inhalt lesbar bleibt, ist **EIN Fenster über die gesamte lesbare Strecke**;
Stillstand wird nicht in Sub-Sekunden-Beats gehackt; mehrere Fenster nur für tatsächlich
verschiedene Beats. Test: String-Containment auf die Prompt-Konstante (Muster des
bestehenden Score-Rubrik-Tests).

### 2. Code: Fensterlänge ist kein Gewicht (`_segment_duration_s`, production_tools.py)

`window.duration_s` entfällt als Deckel des Basisgewichts:

```python
# vorher: upper = max(window.duration_s, _SEGMENT_FLOOR_S); base = min(max(floor, min(budget, upper)), scene)
# nachher:
base = min(max(_SEGMENT_FLOOR_S, budget), scene_duration_s)
```

Das Fenster bestimmt nur noch den **Start-Offset** und *welcher* Beat geschnitten wird
(Storyline-Referenz `{scene, window}`); wie lang, sagt das Kapitelbudget bzw. das
Audio-Fenster. Die Entkopplung gilt einheitlich — auch im Sidecar-losen Fallback (ein Deckel
nur dort holte den Bias durch die Hintertür zurück). Unverändert bleiben:
`segment_capacity_seconds` (Stretch-Cap ab Offset), `_scale_chapter_durations`
(Ein-Faktor-Skalierung mit Floor/Cap) und `storyline_material_seconds` (konservativer
Skript-Deckel — bewusst weiter fensterbasiert; der Prompt-Fix hebt ihn indirekt).

Kerntest: zwei Szenen im selben Kapitel, 0,5s- vs. 8s-Fenster, gleiches Audio-Fenster →
**gleiche** Segmentdauern (vorher ~1:4). Bestehende Cutlist-Tests, die
Fenster-Proportionalität annehmen, werden auf die neue Semantik gehoben (der Plan
identifiziert sie einzeln).

### 3. Messung: `services/local-api/scripts/measure_window_bias.py`

Standalone-Skript (keine Produktions-Dependency, kein Import aus `laura.*` nötig): liest
`workspace-livetest/agent-runs/*/board/scene_reviews/*.json` (nur aktuelle Reviews;
archivierte `versions/` bleiben außen vor), tabelliert pro Review Szenenlänge, Fensterlängen (min/median), hook_score,
Static-Indiz, degraded-Flag, und druckt die Gruppen-Zusammenfassung (Tabelle oben). Zweck:
dieselbe Auswertung nach dem nächsten Live-Lauf = Vorher/Nachher-Vergleich für Prompt-Fix
UND Score-Rubrik (die bisher unbewiesen live ist).

### 4. Risiko & Abwägung

Ein bewusst kurzes Highlight in einer langen Szene bekommt künftig gleich viel Bildzeit wie
ein großer Beat desselben Kapitels. Akzeptiert: Die Betonung steuert die Storyline über
*welche* Fenster sie referenziert (Multi-Window-Referenzen existieren seit f8d4a46), der
Stretch über das Fenster hinaus existiert schon heute, und hook_score bleibt das
Qualitätssignal für den Architekten.

## Tests (Zusammenfassung)

- Prompt-Konstante enthält die Fenster-Rubrik (Containment).
- `_segment_duration_s`: kurzes Fenster deckelt das Gewicht nicht mehr (0,5s-Fenster →
  Budget-Gewicht); Floor 2s und Szenen-Clamp bleiben; Sidecar-loser Fallback identisch
  entkoppelt.
- build_cutlist Ende-zu-Ende: 0,5s- und 8s-Fenster im selben Kapitel → gleiche Dauern;
  Offset bleibt Segmentstart; Stretch-Cap unverändert wirksam.
- Angepasste Alt-Tests: jede Änderung einzeln begründet (welcher Test, welche alte Annahme).
- Messskript: läuft gegen ein Fixture-Board-Verzeichnis (2-3 synthetische Reviews) und
  produziert die erwartete Tabelle/Zusammenfassung.

## Nicht in diesem Scope

- Kein Live-Lauf (Entscheidung 1); der Nachher-Vergleich ist normale Folgearbeit.
- `visual_hook` im v1-MCP-Kandidaten-Pfad (0.6·Shift-Formel) — vom v2-Board unbenutzt.
- Keine Embedding-Kopplung der Cutlist; keine Änderung an `storyline_material_seconds`,
  `segment_capacity_seconds`, `_scale_chapter_durations`.
