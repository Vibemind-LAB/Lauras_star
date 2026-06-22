# VLM-Übergangs-Smoothness-Review — Design-Spec (v2)

**Datum:** 2026-06-19
**Status:** Genehmigt (Brainstorming) → v3 (adversarischer Review + „bestes, kein halbgares Produkt")
**Strang:** „Schnitt/Alignment besser" — Teil 3 von 3 (Video-Modell-Review)

> **v2-Änderungen** (adversarischer Review gegen den echten Code): Cache-Key auf **semantische
> Identität** umgestellt (driftete vorher bei Edits); Frame-Mathe, resnap-Grenzen,
> `enumerate_boundaries` pro Timeline-Art, Determinismus (ffmpeg/Sampling/Modell-Download) präzisiert.
>
> **v3-Änderungen** (User: „immer das Beste"): Der Renderer kann heute nur `fade` (Abblende durch
> Schwarz). Statt das als Krücke zu akzeptieren, kommt ein **echter Crossfade (`xfade`)** in den Renderer
> und **Transition-Felder auf alle Timeline-Arten** (`timeline_clips`, Migration 0023) — der Jump-Cut-Fix
> ist damit ein **echtes Dissolve, einheitlich auf rough_cut/scene/sequence**. Wegen der Größe wird die
> Umsetzung in §12 in **drei geordnete Pläne** zerlegt.

## 1. Problem & Ziel

Lauras Auto-Cut setzt Schnitte an zwei Stellen: an **Bild-Schnittgrenzen** (Shot-Detection +
Diff-Peak-Snapping, [`refine.py`](../../../services/local-api/src/laura/analysis/refine.py)) und an
**Sprech-Grenzen** beim Dead-Air-Entfernen
([`tighten_rough_cut`](../../../services/local-api/src/laura/scenes/build.py)). Der zweite Fall erzeugt
die häufigste „unflüssige" Stelle: Wird eine Pause *innerhalb* einer durchgehenden Einstellung entfernt
(zwei behaltene Teilstücke derselben Quelle, kontiguierend), springen Sprecher/Hintergrund → **Jump-Cut**.

Dieses Feature fügt einen optionalen, lokalen, freien **VLM-Review** hinzu: pro Schnitt ein Urteil
*wie flüssig der Übergang ist*, mit Begründung und Fix-Vorschlag.

**Nicht-Ziel:** Den deterministischen Schnitt ersetzen. Das VLM ist eine **Review-/Verfeinerungsschicht
nach** `refine.py`/`align_cut`, nie das Fundament.

## 2. Nicht-verhandelbare Invarianten (aus [CLAUDE.md](../../../CLAUDE.md))

1. **Optional & local-first:** optionales Extra `[vlm]`; Backend startet/schneidet ohne GPU/Modell.
   `vlm_available()`-Guard + graceful skip (Muster
   [`align.py:whisperx_available`](../../../services/local-api/src/laura/analysis/align.py)).
2. **Determinismus / Idempotenz (#7):** siehe §3. Modell-Digest Teil der Review-Identität.
3. **Frame-Raum (#1, #2):** alle Grenzen **Ganzzahl-Source-Frames**, **end-exclusive**; nie Float-Sekunden.
4. **OTIO Source of Truth (#6):** Review schreibt nur in eigene Tabelle; „Anwenden" nutzt bestehende
   Timeline-/Transition-Mutationen ([`editing/operations.py`](../../../services/local-api/src/laura/editing/operations.py)).

## 3. Idempotenz & Cache-Kohärenz (neu, löst Review-Blocker)

**Semantische Boundary-Identität** (NICHT die Sequence-Position, die bei Edits driftet):
```
identity = (timeline_id, asset_a, asset_b, src_out_a, src_in_b)
```
- `boundary_signature(boundary, k, proxy_version) := sha256(timeline_id | asset_a | asset_b | src_out_a |
  src_in_b | removed_gap_frames | int(same_source) | k | proxy_version)`.
- `model_digest`: Content-Hash des Modells, zur Laufzeit von Ollama (`GET /api/tags`) geholt; **nicht** Teil
  von `pipeline_version`, sondern orthogonal — pro Review persistiert.
- **Cache-Key (UNIQUE):** `(timeline_id, asset_a, asset_b, src_out_a, src_in_b, model_digest)`.
  `boundary_seq_frame` ist **denormalisiert** für UI/Lookup, **nicht** Teil der Identität.
- **Verhalten:** Re-Run mit gleichem Input+Modell → Cache-Hit, keine Inferenz. Upstream-Edit, der die
  *semantische* Grenze nicht ändert (z. B. ein früherer Clip getrimmt) → weiterhin Cache-Hit. Modellwechsel
  (neuer Digest) → neue Zeilen, alte bleiben als Audit erhalten (nie überschrieben).
- **`pipeline_version`** bleibt Lauras globale Analyse-Version; ein Bump invalidiert nicht die VLM-Reviews
  (die hängen am `model_digest`), aber dokumentiert die Kernlogik-Version.

## 4. Architektur

```
enumerate_boundaries(timeline) ─► [Boundary]            (NEU; kind-bewusst)
   for each Boundary:
     frame_strip_plan(b,k) ─► [int src-frame idx]       (pure, Ganzzahl-Mathe)
     extract_frames(proxy, idx) ─► [bytes]              (ffmpeg, deterministisch)
     VlmBackend.review(frames, meta) ─► TransitionVerdict (pinned, temp0)
     upsert_transition_review(...)                       (Cache, §3-Identität)
apply_fix(timeline, identity, fix):
     resnap   ─► Clip-Grenzen + re-pack via operations.py   (alle kinds)
     transition ─► sequence_items.transition_after_* (fade) (nur kind=sequence)
```
Neues Modul **`analysis/transition_review.py`** (reine Logik + `VlmBackend`-Protocol). Alle unten genannten
Funktionen sind **NEU** (kein Bestandscode), folgen aber bestehenden Mustern (`flatten_sequence`,
`operations.py`, `align.py`).

### 4.1 Datentypen (pure)
```python
@dataclass(frozen=True)
class Boundary:
    timeline_id: str
    kind: Literal["rough_cut", "scene", "sequence"]   # für Fix-Gating
    asset_a: str          # Quell-Asset von Clip A (synthetic/AI → dessen generierte asset_id)
    asset_b: str
    src_in_a: int; src_out_a: int     # end-exclusive
    src_in_b: int; src_out_b: int
    seq_in_a: int; seq_out_a: int     # = boundary_seq_frame (denormalisiert)
    speed_a_num: int; speed_a_den: int
    removed_gap_frames: int           # max(0, src_in_b - src_out_a) wenn asset_a==asset_b, sonst 0
    same_source: bool                 # asset_a==asset_b AND removed_gap_frames==0 (kontiguierende Quelle)

@dataclass(frozen=True)
class SuggestedFix:
    kind: Literal["none", "resnap", "transition"]
    resnap_delta_frames: int = 0      # Source-Frames, Vorzeichen siehe §5
    transition_style: Literal["crossfade", "fade"] = "crossfade"  # crossfade = echtes Dissolve (Default); fade = Abblende
    transition_frames: int = 0        # TIMELINE-Frames (transition_after_frames)
    applicable_on_kinds: tuple[str, ...] = ("rough_cut", "scene", "sequence")  # resnap UND transition überall (nach 0023)

@dataclass(frozen=True)
class TransitionVerdict:
    smoothness: float                 # 0..1
    label: Literal["smooth", "jump_cut", "hard_jolt", "motion_break"]
    reason: str
    suggested_fix: SuggestedFix
```

### 4.2 `enumerate_boundaries(db, timeline_id) -> list[Boundary]` (NEU, kind-bewusst)
Vgl. [[flatten-only-sequences]]:
- **rough_cut / scene:** `timeline_clips WHERE lane=0 ORDER BY seq_in_frame`. Grenze = zwischen Clip i und
  i+1. `seq_in_a/seq_out_a` aus den Clip-Sequence-Frames; `boundary_seq_frame = seq_out_a (= seq_in_b)`.
  (Scene ist nur eine Gruppierungsschicht über demselben Clip-Modell — gleiche Aufzählung wie rough_cut.)
- **sequence:** `sequence_items ORDER BY order_index`; jedes Paar (Szene A, Szene B) → eine Grenze. Die
  beteiligten Quell-Frames stammen aus dem **letzten Clip von A** bzw. **ersten Clip von B** der
  aufgelösten Szenen-Timelines (über `resolve_clip_rows`/`flatten_sequence`). `boundary_seq_frame` = das
  Sequence-Frame, an dem Szene A endet/B beginnt.
- **Nur lane 0** (Video). Audio-only-Clips (lane 1) sind keine eigenen Boundaries; ihr
  `audio_offset_samples` reist als Head-Eigenschaft mit dem lane-0-Clip und bleibt bei resnap über
  `_normalize_offsets` erhalten. (Multi-Lane-Review = Future Work.)
- **`same_source`/`removed_gap_frames`** exakt wie in §4.1 berechnet — keine Fuzzy-Schwelle: nur
  `src_out_a == src_in_b` bei gleichem Asset gilt als kontiguierender Dead-Air-Jump.

*Worked example.* rough_cut, 3 lane-0-Clips
`C0[seq 0–100], C1[seq 100–160], C2[seq 160–240]` → 2 Boundaries: `b1` (seq 100, zwischen C0/C1),
`b2` (seq 160, zwischen C1/C2). Sequenz mit 2 Szenen S0,S1 → 1 Boundary an der S0/S1-Grenze.

### 4.3 `frame_strip_plan(boundary, k) -> list[FrameRef]` (pure)
`FrameRef = (asset_id, src_frame_idx)`. Gibt geordnete Ganzzahl-Indizes zurück:
- **A-Seite:** `[max(src_in_a, src_out_a - k) .. src_out_a - 1]` (die letzten ≤k Frames, inklusive
  `src_out_a - 1`; end-exclusive-Bereich `[src_out_a-k, src_out_a)`).
- **B-Seite:** `[src_in_b .. min(src_in_b + k, src_out_b) - 1]` (die ersten ≤k Frames).
- **Kurze Clips:** ist A bzw. B kürzer als k, werden eben weniger Frames geliefert (alle vorhandenen). Die
  tatsächliche Anzahl wird zurückgegeben; das Backend adaptiert die Frame-Zahl dynamisch.
- Reihenfolge: A-Frames (alt→neu) dann B-Frames (alt→neu), mit Markierung der A/B-Seite im `meta`.

### 4.4 Frame-Extraktion (IO, deterministisch)
`extract_frames(proxy_path, frame_refs, rate_num, rate_den) -> list[bytes]`:
- **Proxy-Pfad:** dynamisch auflösen — `GET /assets/{asset_id}` → Datei-Liste → Proxy-Eintrag → `path`
  (kein festes Disk-Schema; `laura-media://` ist nur das Protokoll-Interface, vgl.
  [`apps/desktop/src/main.ts`](../../../apps/desktop/src/main.ts) `resolveMediaPath`).
- **Frame→Zeit:** `t_sec = src_frame_idx * rate_den / rate_num`; ffmpeg pro Frame
  `ffmpeg -ss {t_sec} -i {proxy} -frames:v 1 -q:v 2 frame.jpg` (festes JPEG-Q für Pixel-Konsistenz; PNG als
  verlustfreie Alternative). Kein Keyframe-Snap — exakter Seek.
- **Proxy ist CFR** (Editorial-Proxy, Invariante #5) und teilt die Frame-Indizierung der Source → direkte
  Index→Zeit-Abbildung. `proxy_version` (Digest des Proxy-Files) fließt in `boundary_signature`.
- **Out-of-bounds:** auf `[0, proxy_duration_frames)` clampen, geloggt.
- **Kein Disk-Cache** in v1 (frische Extraktion pro Lauf → Pixel-Konsistenz); optionaler Cache später.

### 4.5 `VlmBackend` (Protocol) + Implementierungen
```python
class VlmBackend(Protocol):
    def available(self) -> bool: ...        # Modell lokal vorhanden (Ollama /api/tags)?
    def model_id(self) -> str: ...          # z.B. "qwen3-vl-8b"
    def model_digest(self) -> str: ...      # Ollama-SHA256 (voll)
    def review(self, frames: list[bytes], meta: dict) -> TransitionVerdict: ...
```
- **`OllamaVlmBackend` (Default):** lokales Ollama (`127.0.0.1:11434`). **Sampling:** `temperature=0,
  top_k=1, top_p=1.0, seed=fix`. **Erzwungenes JSON** via `format`-Schema (Retry bei Mismatch). Frames als
  base64-Bilder in Reihenfolge; `meta` enthält `{"same_source": bool, "removed_gap_frames": int,
  "k": int, "a_count": int, "b_count": int}`.
- **Modell-Verfügbarkeit/Download:** `available()` prüft `/api/tags`. Bei erstem Lauf/Modellwechsel zieht
  `review()` per `model_pull(model_id)` (idempotent). Schlägt der Pull fehl → `ModelUnavailable`; der Job
  retryt mit Backoff (max 3). UI zeigt persistenten „Modell wird geladen…"-Status.
- **`StubVlmBackend` (Tests, kein IO):** deterministische Canned-Verdicts:
  `same_source AND removed_gap_frames == 0` → `label=jump_cut, suggested_fix=resnap`; sonst
  `label=smooth, suggested_fix=none`. (Korrigiert: die alte Bedingung war logisch unmöglich.)

### 4.6 Persistenz — Migration `0024_transition_reviews.sql` (Plan B)
```sql
CREATE TABLE transition_reviews (
    id TEXT PRIMARY KEY,
    timeline_id TEXT NOT NULL REFERENCES timelines(id) ON DELETE CASCADE,
    asset_a TEXT NOT NULL, asset_b TEXT NOT NULL,
    src_out_a INTEGER NOT NULL, src_in_b INTEGER NOT NULL,
    boundary_seq_frame INTEGER NOT NULL,        -- denormalisiert (UI/Lookup), NICHT Identität
    boundary_signature TEXT NOT NULL,
    smoothness REAL NOT NULL, label TEXT NOT NULL, reason TEXT NOT NULL,
    suggested_fix_json TEXT NOT NULL,
    model_id TEXT NOT NULL, model_digest TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(timeline_id, asset_a, asset_b, src_out_a, src_in_b, model_digest)
);
CREATE INDEX idx_transition_reviews_timeline ON transition_reviews(timeline_id);
```
> **FK-Sorgfalt** ([[verify-against-fresh-build]] / delete_project-Orphan-Fix): `ON DELETE CASCADE` auf
> `timelines(id)` — kein Orphan beim Timeline-/Projekt-Löschen.

Repos: `upsert_transition_review`, `list_transition_reviews(timeline_id)` (aktuellste pro Identität),
`get_cached_review(identity, model_digest)`.

### 4.7 Transitions auf allen Timeline-Arten — Migration `0023_clip_transitions.sql` (v3)
Heute liegen Transition-Felder nur auf `sequence_items` (0021). Für den einheitlichen Fix bekommen sie auch
die Clip-Ebene:
```sql
ALTER TABLE timeline_clips ADD COLUMN transition_after_kind TEXT NOT NULL DEFAULT 'hard';
ALTER TABLE timeline_clips ADD COLUMN transition_after_frames INTEGER NOT NULL DEFAULT 0;
```
`transition_after_kind ∈ {'hard','fade','crossfade'}`. Damit greift `apply_fix transition` auf
rough_cut/scene (Clips) **und** sequence (`sequence_items`) — kein „nur Sequenz" mehr.

## 5. Fix-Anwendung

`apply_fix(db, timeline_id, identity, fix) -> ApplyResult` mit `status: Literal["ok","not_supported","error"]`.

- **`resnap` (primär, alle kinds, deterministisch):** verschiebt die gemeinsame Grenze um
  `resnap_delta_frames` (Source-Frames). **Vorzeichenkonvention:** +δ = Grenze nach **später in der Quelle**
  (mehr von A, weniger von B). Schritte (analog
  [`operations.py`](../../../services/local-api/src/laura/editing/operations.py) `trim`/`move`/`_normalize_offsets`):
  1. `src_out_a += δ`, `src_in_b += δ`.
  2. **Bounds/Clamp:** `δ` so begrenzen, dass `src_in_a < src_out_a <= src_out_a_max` und
     `src_in_b_min <= src_in_b < src_out_b` — konkret `δ ∈ [src_in_a - src_out_a + 1, src_out_b - src_in_b - 1]`
     ∩ Fenster `[-W, +W]` (`W = DEFAULT_WINDOW = 12`, wie `editorial.py`). Außerhalb → auf nächstgültigen Wert
     clampen + Warn-Log. Ergäbe der Clamp `δ=0` → `ApplyResult(status="error", reason="no effective change")`.
  3. Sequence-Längen aus den neuen Source-Längen unter Berücksichtigung von `speed_a` neu berechnen
     (`δ` wirkt im **Source-Raum**; die Sequence-Länge folgt via Speed-Retiming).
  4. Nachfolgende Clips gaplos re-packen (`move`/ripple) + `_normalize_offsets` (First-Clip-0-Invariante).
  *Worked example:* A`[src 100–200, seq 0–100]`, B`[src 200–300, seq 100–200]`, δ=+10 →
  A`[src 100–210, seq 0–110]`, B`[src 210–300, seq 110–200]` (B 10 Frames kürzer, alles gaplos).
- **`transition` (für Jump-Cuts, alle kinds):** setzt `transition_after_kind=transition_style` +
  `transition_after_frames=transition_frames` (TIMELINE-Frames) — auf `sequence_items` (sequence) **oder**
  `timeline_clips` (rough_cut/scene, nach Migration 0023). `transition_style="crossfade"` rendert ein
  **echtes Dissolve** über einen neuen `xfade`-Pfad in
  [`render/mp4.py:_video_transition_chain`](../../../services/local-api/src/laura/render/mp4.py)
  (heute nur `fade`/Schwarz; v3 ergänzt `xfade`). `"fade"` (Abblende) bleibt als Alternativstil.
  **Renderer-Arbeit (v3):** `_video_transition_chain` bekommt für `kind="crossfade"` einen ffmpeg-`xfade`-Zweig;
  der Clip-Render-Pfad (rough_cut/scene) baut die Transition-Kette analog aus `timeline_clips.transition_after_*`.
- **`none`:** no-op, `status="ok"`.
- **Batch „Alle anwenden":** sammelt pro Boundary ein `ApplyResult`, **stoppt nicht** bei einem Fehler,
  liefert Teilergebnis-Liste zurück.
- **Policy 5b:** nichts wird automatisch mutiert; Anwenden ist immer explizit. (Auto-Fix später als
  Opt-in `LAURA_VLM_AUTOFIX`.)

> **Fix-Wahl je Label:** `motion_break`/`hard_jolt` → bevorzugt **resnap** (cut-on-action, saubereres
> Frame-Paar). `jump_cut` (gleiche Quelle, Zeitsprung) → **crossfade** (echtes Dissolve; resnap kann einen
> Same-Source-Sprung nicht verstecken). Der User wählt im Panel den vorgeschlagenen oder einen anderen Fix.

> **Live-Preview-Ehrlichkeit:** Der `SequencePlayer` spielt Clips sequen­ziell als `<video>` ab und stellt
> Transitions **nicht in Echtzeit** dar — der Crossfade ist im **Export/materialisierten Render** korrekt
> sichtbar, in der Live-Vorschau erscheint zunächst der harte Schnitt. Echtzeit-Transition-Vorschau ist ein
> separater, größerer Schritt (Future Work) und wird **nicht** durch einen Fake im Player vorgetäuscht.

## 6. Lauf-Modus & Job

- **On-demand** („Übergänge prüfen"-Button), nicht im Auto-Import. Job-Typ `transition.review`.
- **Fortschritt** über `0005_job_progress`: `{"reviewed": int, "total": int}`, Update nach jeder Boundary.
- **Idempotenz:** vor Inferenz `get_cached_review(identity, model_digest)`; Hit → überspringen. Nach Timeline-Edit
  ruft das UI `review` erneut; semantisch unveränderte Boundaries sind Cache-Hits (§3), nur neue/echte
  Änderungen kosten Inferenz.

## 7. Modellwahl & Eval-Harness („alle 3 nacheinander")

Ziel-Hardware **12 GB VRAM** → Kandidaten (Q4-GGUF): **Qwen3-VL-8B** (~6,1 GB, Default), **Qwen3-VL-4B**
(~3,3 GB), **SmolVLM2-2.2B** (~5,2 GB, CPU-fähig).
`bench/transition_bench.py` (Muster [`cut_bench.py`](../../../services/local-api/src/laura/bench/cut_bench.py)):
fährt die 3 Modelle über einen kleinen **handgelabelten** Satz Schnitt-Grenzen (Jump-Cut/smooth/Jolt), misst
**Label-Übereinstimmung mit Goldstandard** + **Latenz/Schnitt auf 12 GB**, gibt Tabelle aus → Default-Modell.
Default per `LAURA_VLM_MODEL` überschreibbar. Harness ist manueller Bench (nicht CI-Default).

## 8. API & Frontend

**API** (Router [`api/timelines.py`](../../../services/local-api/src/laura/api/timelines.py),
Modelle [`api/models.py`](../../../services/local-api/src/laura/api/models.py)):
- `POST /timelines/{id}/transitions/review` → startet `transition.review`-Job → `{job_id}`.
- `GET /timelines/{id}/transitions/review` → `{ "verdicts": [ {boundary_seq_frame, asset_a, asset_b,
  src_out_a, src_in_b, smoothness, label, reason, suggested_fix, model_id, created_at} ], "job_id"?,
  "status": "running|succeeded|failed", "progress": {"reviewed","total"} }`.
- `POST /timelines/{id}/transitions/apply-fix` Body `{identity, fix}` →
  `{status, reason?, updated_clips?}`. (Identität statt fragiler `boundary_seq_frame` in der URL.)

**Frontend:**
- `api.ts`: Typen `TransitionVerdict`/`SuggestedFix`/`ApplyResult` + `reviewTransitions`,
  `getTransitionReview`, `applyTransitionFix`.
- Hook `useTransitionReview(timelineId)` (Polling bis `status != running`).
- Panel **„Übergänge prüfen"** in `FineCutView` (scene) und `AssembleView` (sequence): Liste der Schnitte
  mit Score-Badge (Farbe nach `smoothness`), Label, Begründung, Fix-Chip + „Anwenden"/„Alle anwenden";
  Fortschritt; einmaliger Modell-Download sichtbar. `transition`-Fix ist auf allen Timeline-Arten aktiv
  (nach 0023); ein Hinweis macht transparent, dass der Crossfade im Export/Render erscheint, nicht in der
  Live-Vorschau.

## 9. Tests & Verifikation (CLAUDE.md „Verifizieren vor fertig")

**pytest, ohne Modell (StubVlmBackend):**
- `frame_strip_plan`-Mathe (inkl. Clip kürzer als k, Off-by-one, end-exclusive).
- `enumerate_boundaries` pro kind (rough_cut/scene/sequence) gegen den Worked-Example.
- `boundary_signature`-Stabilität: gleicher Input → gleiche Signatur; Upstream-Edit ohne semantische
  Änderung → gleiche Identität → **Cache-Hit** (Determinismus-Test).
- `apply_fix` resnap: Worked-Example, Vorzeichen, Clamping, kein Null-/Negativ-Length, First-Clip-0;
  `transition` setzt `transition_after_*` auf `timeline_clips` (rough_cut/scene) **und** `sequence_items`
  (sequence); bei zu wenig Überlapp-Material → `fade`-Fallback (geloggt).
- Renderer (Plan A): `_video_transition_chain` mit `kind="crossfade"` erzeugt einen `xfade`-Filtergraph
  (vs. Goldframe-Vergleich); `hard` bleibt unverändert.
- Idempotenz: zweimal `review` → zweiter Lauf 0 Inferenzen.
**Opt-in-Integrationstest** (nur mit `[vlm]` + Ollama): echter Lauf gegen `feattest_90s`, prüft
JSON-Roundtrip + 3× identischer Score (Sampling-Determinismus).
**Eval-Harness** manuell.

## 10. Risiken & Caveats

- **Fuzzy-Urteil:** „Smoothness" subjektiv; Modell-Updates ändern Verdicts → `model_digest` in der Identität
  + Cache stabilisieren; Harness misst Streuung.
- **Latenz:** 8B über viele Schnitte langsam (mehrere Sek./Schnitt @12 GB) → on-demand + Fortschritt;
  optionaler billiger Vorfilter (Optical-Flow/SSIM) als **Opt-out** (Default: alle Schnitte, gemäß „voll B").
- **Erst-Download:** mehrere GB, einmalig, im UI angekündigt; Fehlerpfad: `ModelUnavailable` + Job-Retry.
- **Crossfade nur im Render sichtbar:** korrekt im Export/materialisierten Render; Live-Vorschau zeigt
  zunächst den harten Schnitt (Echtzeit-Transition-Preview = Future Work, kein Fake im Player).
- **ffmpeg-`xfade`** braucht überlappende Eingaben (Clip A muss `transition_frames` länger laufen) — der
  Render-Pfad muss den Überlapp aus den Source-Reserven ziehen; reicht das Material nicht, fällt der Fix auf
  `fade` zurück (geloggt).

## 11. Scope-Grenzen (YAGNI)

**Drin:** Verdict/Schnitt + semantischer Cache; **resnap** überall; **echter Crossfade (`xfade`) im Renderer**
+ Transition-Felder auf allen Timeline-Arten (0023); on-demand-Job; Eval-Harness (3 Modelle);
„Übergänge prüfen"-Panel; Stub-basierte Tests.
**Draußen (Future Work, je eigenes Stück):** Echtzeit-Transition-Preview im `SequencePlayer`; Auto-Fix im
Pipeline-Lauf; Multi-Lane-/Audio-Cut-Review; Frame-Disk-Cache; Hintergrund-Vorberechnung beim Import;
Multi-Modell-Voting zur Laufzeit; Morph-Cut (optical-flow-Warp) jenseits von `xfade`.

## 12. Implementierungs-Zerlegung (drei geordnete Pläne)

Zu groß für einen Plan → drei abgegrenzte, nacheinander baubare Stücke (jedes für sich testbar & nützlich):

- **Plan A — Transition-Fundament (kein VLM).** Migration 0023 (`timeline_clips.transition_after_*`),
  `xfade`-Zweig in `_video_transition_chain` + Clip-Render-Pfad, Repos/API zum Setzen einer Transition,
  Überlapp-aus-Reserven-Logik + `fade`-Fallback. Verifizierbar: Export mit echtem Crossfade vs. Goldframe.
- **Plan B — Review-Engine (Kern).** `transition_review.py` (Datentypen, `enumerate_boundaries`,
  `frame_strip_plan`, `boundary_signature`), `extract_frames`, `VlmBackend`+`StubVlmBackend`, Tabelle 0024 +
  Repos, `apply_fix` (resnap + transition über Plan A), `transition.review`-Job. Tests komplett gegen Stub.
- **Plan C — Modell + UI + Harness.** `OllamaVlmBackend`, `bench/transition_bench.py` (3 Modelle → Default),
  API-Endpoints, `api.ts`/`useTransitionReview`, „Übergänge prüfen"-Panel. Opt-in-Integrationstest.

Reihenfolge A → B → C; B hängt an A (transition-Fix), C an B (Engine).
