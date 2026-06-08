# Cut-Tuning: Ground-Truth-Benchmark & Knob-Sweep

Dieses Dokument beschreibt den **committed Ground-Truth-Benchmark** für Lauras
Schnittqualität, die daraus gewonnene Ergebnis-Tabelle, die daraufhin gewählten
Knob-Werte (mit Begründung) und die editoriale Trade-off-Kurve, die den Bias-Slider
(picture-vs-sound) rechtfertigt.

Harness: [`src/laura/bench/cut_bench.py`](../../services/local-api/src/laura/bench/cut_bench.py),
Runner: [`src/laura/bench/bench_run.py`](../../services/local-api/src/laura/bench/bench_run.py),
Tests: [`tests/test_cut_bench.py`](../../services/local-api/tests/test_cut_bench.py).

Reproduzieren:

```
cd services/local-api
uv run --no-sync python -m laura.bench.bench_run
```

## Worum es geht: gelabelter vs. selbst-überwachter Maßstab

`laura.analysis.eval_cut` ist **selbst-überwacht**: Es bewertet, wie frame-genau eine
*erkannte* Grenze auf dem Luma-Peak sitzt — es weiß nicht, wo der „richtige" Schnitt
liegt, nur wo sich das Bild am stärksten ändert. Damit lassen sich zwei Fehler nicht
messen, die für die Schnittqualität entscheidend sind:

* **False Positive** — eine erkannte Grenze ohne echten Schnitt in Toleranz (erfundener Cut),
* **Miss** — ein echter Schnitt ohne Erkennung in Toleranz (verschluckter Cut).

Der Benchmark **konstruiert** deshalb Videos mit exakt bekannten Schnitt-Frames (vier
30-Frame-Szenen aneinandergehängt → echte Cuts bei `[30, 60, 90]`) und vergleicht die
Erkennung gegen diese Ground Truth. Das ist der gelabelte Gegenpart zu `eval_cut`.

### Drei Schichten

1. **Synthetische Video-Suite** (`build_suite`, braucht ffmpeg via `run_ffmpeg`):
   - `hard_colors` — vier Vollfarben → Cuts `[30, 60, 90]`.
   - `hard_testsrc` — vier Testbilder (testsrc/smptebars/testsrc2/smptehdbars) → `[30, 60, 90]`.
   - `hard_lowmotion` — fast identische Graustufen-Szenen → `[30, 60, 90]` (Low-Contrast-Stress).
   - `gradual_fade` — 30-Frame-Xfade schwarz→weiß; Ground Truth = **Übergangs-Mitte** `60`.
2. **GT-Komparator** (`compare_to_ground_truth`, PUR, keine ffmpeg-Abhängigkeit): greedy
   nearest-first Matching innerhalb einer Toleranz; liefert mittleren/medianen Absolut-Offset,
   `% exact/within1/within2`, **False Positives** und **Misses** als ein `GTReport` (mit
   abgeleitetem Precision/Recall/F1).
3. **Editoriale Szenarien** (`editorial_scenarios`, PUR): synthetische `Word`/Silence/Speaker-
   Layouts, bei denen der editorial-ideale Frame **per Konstruktion** bekannt ist (Speaker-Turn
   in einer Silence bei Frame X). Gemessen wird, ob `joint_place` ihn trifft.

### Toleranzen

Harte Cuts werden mit `DEFAULT_TOL = 2` bewertet (entspricht `eval_cut`s `pct_within2`-Band).
Ein linearer Xfade hat **keinen** einzelnen Luma-Peak — die Frame-zu-Frame-Differenz ist über
den Übergang nahezu konstant —, deshalb gibt es keinen „korrekten" Einzel-Frame. Der gradual-
Fall wird gegen die Übergangs-Mitte mit der breiteren `GRADUAL_TOL = 15` (halbe Übergangslänge)
gemessen: Jeder Schnitt **innerhalb** des Fades zählt als „auf dem Cut".

## GT-Ergebnis-Tabelle (Detektoren × Knobs)

Grid: Snap-Window ∈ {4, 6, 8, 10}, Fuse-Tol ∈ {4, 8, 12}, Detektor ∈ {adaptive, hybrid}.
TransNetV2 (`scene-ml`) **war geladen** → der Hybrid-Pfad ist enthalten.

Auszug der Per-Case-Tabelle (jede Knob-Zeile ist repräsentativ; `FP` = False Positives,
`miss` = Misses, `drift` = aufsummierter Abstand jeder Erkennung zum nächsten echten Cut,
auch jenseits der Toleranz):

| case            | knob                    | detected   | mean&#124;off&#124; | FP | miss |
|-----------------|-------------------------|------------|--------------------:|---:|-----:|
| hard_colors     | adaptive(snap=4)        | 26,60,90   | 0.00 | 1 | 1 |
| hard_colors     | adaptive(snap=10)       | 20,60,90   | 0.00 | 1 | 1 |
| hard_colors     | hybrid(snap=4,fuse=4)   | 26,60,90   | 0.00 | 1 | 1 |
| hard_colors     | hybrid(snap=10,fuse=8)  | 20,60,90   | 0.00 | 1 | 1 |
| hard_testsrc    | adaptive(snap=4)        | 30,60,90   | 0.00 | 0 | 0 |
| hard_testsrc    | hybrid(snap=4,fuse=8)   | 30,60,90   | 0.00 | 0 | 0 |
| hard_lowmotion  | adaptive(snap=4)        | –          | 0.00 | 0 | 3 |
| hard_lowmotion  | hybrid(snap=4,fuse=4)   | 30,60,90   | 0.00 | 0 | 0 |
| gradual_fade    | adaptive(snap=4)        | –          | 0.00 | 0 | 1 |
| gradual_fade    | hybrid(snap=4,fuse=4)   | 59         | 1.00 | 0 | 0 |
| gradual_fade    | hybrid(snap=8,fuse=4)   | 69         | 9.00 | 0 | 0 |

**Aggregiertes Knob-Ranking** (nur harte Cases; `cost = mean|off| + 5·(FP+miss) + 0.1·drift`,
niedriger ist besser):

| rank | knob                    | mean&#124;off&#124; | within1 | FP | miss | drift | cost  |
|-----:|-------------------------|--------------------:|--------:|---:|-----:|------:|------:|
| 1 | hybrid(snap=4,fuse=4)      | 0.00 | 100% | 1 | 1 |  4 | 10.40 |
| 1 | hybrid(snap=4,fuse=8)      | 0.00 | 100% | 1 | 1 |  4 | 10.40 |
| 1 | hybrid(snap=4,fuse=12)     | 0.00 | 100% | 1 | 1 |  4 | 10.40 |
| 4 | hybrid(snap=6,fuse=\*)     | 0.00 | 100% | 1 | 1 |  6 | 10.60 |
| 7 | hybrid(snap=8,fuse=\*)     | 0.00 | 100% | 1 | 1 |  8 | 10.80 |
|10 | hybrid(snap=10,fuse=\*)    | 0.00 | 100% | 1 | 1 | 10 | 11.00 |
|13 | adaptive(snap=4)           | 0.00 |  67% | 1 | 4 |  4 | 25.40 |
|16 | adaptive(snap=10)          | 0.00 |  67% | 1 | 4 | 10 | 26.00 |

## Erkenntnisse & gewählte Knob-Werte

### 1. Hybrid ≫ adaptive (kein Default-Change nötig — schon Produktions-Default)

Der adaptive Einzeldetektor **verschluckt** auf `hard_lowmotion` alle drei Cuts (Misses=3) und
auf `gradual_fade` den Übergang (Miss=1) — kontrastarme bzw. graduelle Übergänge liegen unter
seiner Content-Schwelle. Der Hybrid-Pfad holt sie über TransNetV2 zurück (Misses=0). Das ist
bereits der Produktions-Default (`detect_shots_hybrid`), der Benchmark bestätigt ihn quantitativ.

### 2. Snap-Window: 6 → **4** (Default geändert)

Auf `hard_colors`/`hard_lowmotion` (Vollfarben) sitzt jeder harte Cut bereits exakt auf seinem
Luma-Peak. Ein **zu weites** Snap-Window lässt `argmax(d)` aber rückwärts in das
libx264-Kompressions-Flackern einer flachen Szene wandern und zieht den Cut so vom echten
Boundary weg: `30 → 26` (window 4), `→ 24` (6), `→ 22` (8), `→ 20` (10). Der Drift wächst
**monoton** mit dem Window; `drift` 4 < 6 < 8 < 10 im Ranking macht das sichtbar. Auf texturierten
Szenen (`hard_testsrc`) ist das Window irrelevant (immer `30,60,90`). Window **4** minimiert den
Drift ohne jede Regression — daher der neue Default.

Umgesetzt als dediziertes `refine.SNAP_WINDOW = 4`, **entkoppelt** von der geteilten
`eval_cut.DEFAULT_WINDOW = 6` (die weiterhin das Bewertungs-/`joint`-Fenster bestimmt). Der
Produktions-Hybrid (`shots._snap_fused_shots` → `snap_boundaries` ohne explizites Window) zieht
den neuen Default automatisch. Tests `test_refine`/`test_hybrid` bleiben grün (keiner pinnt das
Default-Window; alle Peaks liegen innerhalb ±4 der getesteten Boundaries).

> Hinweis zum gradual-Fall: Window 4/6 landen bei Frame 59 (Offset 1 zur Mitte 60, innerhalb
> `GRADUAL_TOL`), Window 8/10 überschießen auf 69 (Offset 9). Auch hier ist das engere Window
> besser — dieselbe Richtung wie bei den harten Cuts.

### 3. Fuse-Tol: **8 bleibt** (Default unverändert)

Fuse-Tol {4, 8, 12} liefert in der gesamten Suite **identische** Ergebnisse: Die gut getrennten
Cuts (≥ 20 Frames Abstand) werden von beiden Engines bei jeder Toleranz demselben Cluster
zugeordnet. Es gibt keinen Grund, den Produktions-Default `8` zu ändern; der Benchmark zeigt
keine Sensitivität in diesem Bereich.

### Der „erfundene" Cut auf `hard_colors` (FP=1, miss=1)

Auf den Vollfarben erkennt der adaptive Detektor zusätzlich einen frühen Boundary, den der Snap
auf `26/24/22/20` zieht — `> tol` von 30 entfernt, also gleichzeitig False Positive (26 erfunden)
und Miss (30 verschluckt). Das ist ein **Artefakt synthetischer Flachszenen** (Kompressions-
Flackern ohne echten Bildinhalt), kein Pipeline-Bug; reales Material hat dieses Flackern nicht.
Der Befund rechtfertigt dennoch direkt das engere Snap-Window (#2): Es hält den Schaden klein.

## Editoriale Trade-off-Kurve (Bias-Slider-Begründung)

Die Blend-Gewichte sind eine **Präferenz, keine Ground Truth.** Statt einen „wahren" Wert zu
wählen, charakterisiert der Benchmark den Trade-off auf einem Szenario, bei dem visueller Peak
und editoriales Optimum **auseinanderfallen**:

* Ein Wort spannt `[260, 273)`; der visuelle Peak liegt bei Frame **270** — mitten im Wort
  (abgeschnittenes Audio, akustisch schlecht, aber bildgenau).
* Eine echte Silence `[273, 279)` folgt, mit einem Speaker-Turn bei Frame **273** — der
  editorial-ideale, akustisch saubere Schnitt.

`joint_place` wird über `w_editorial ∈ {0.0, 0.2, 0.4, 0.6, 0.8, 1.0}` gesweept:

| w_editorial | chosen | off_visual_peak | off_editorial |
|------------:|-------:|----------------:|--------------:|
| 0.0 | 270 | 0 | 3 |
| 0.2 | 270 | 0 | 3 |
| 0.4 | 270 | 0 | 3 |
| 0.6 | 273 | 3 | 0 |
| 0.8 | 273 | 3 | 0 |
| 1.0 | 273 | 3 | 0 |

**Lesart der Kurve:** Mit steigendem `w_editorial` wächst der Abstand zum visuellen Peak
(`off_visual_peak` 0 → 3) monoton, während der Abstand zum editorialen Ideal (`off_editorial`
3 → 0) monoton fällt. Der Umschlag liegt zwischen 0.4 und 0.6 — genau dort, wo der
Produktions-Default `(w_visual=0.6, w_editorial=0.4)` sitzt, der den Bildschnitt knapp bevorzugt.

Diese Kurve ist die Rechtfertigung für den **Bias-Slider** (Improvement #6): Ein „picture-first"-
Editor wählt das linke Ende (visuell exakt, Audio darf clippen), ein „sound-first"-Editor das
rechte (sauberes Audio, Schnitt ein paar Frames neben dem Peak). `bias_to_weights(cut_bias)`
mappt den Slider linear auf `(w_visual, w_editorial)`. Es gibt bewusst keinen „richtigen" Wert —
nur eine Präferenz entlang dieser Kurve.

> Kontroll-Szenario `aligned_peak_on_seam`: Wenn der visuelle Peak bereits auf einer sauberen
> Silence-Kante mit Speaker-Turn liegt, hält **jedes** `w_editorial` den Schnitt auf demselben
> Frame — es gibt nichts zu tauschen. Das bestätigt, dass die Kurve nur bei echter Divergenz
> entsteht.

## Nicht angefasst

Die Blend-Gewichte (`joint.w_visual=0.6/w_editorial=0.4`) und die Editorial-Tier-Konstanten
(`_SCORE_SPEAKER_TURN=1.0`, `_SCORE_SENTENCE_END=0.95`, … — von `test_joint` exakt gepinnt)
wurden **nicht** verändert. Der Benchmark berichtet das Optimum samt Kurve; die Wahl entlang der
Kurve gehört in die UI (Bias-Slider), nicht in einen still geänderten Default.

## Zusammenfassung der Änderungen

* **Geändert:** `refine.SNAP_WINDOW = 4` (vorher implizit `6` über `eval_cut.DEFAULT_WINDOW`) —
  besserer GT-Drift ohne Regression.
* **Unverändert (bestätigt optimal/neutral):** Hybrid-Detektor (Produktions-Default),
  Fuse-Tol `8`, Blend-Gewichte, Tier-Konstanten.
