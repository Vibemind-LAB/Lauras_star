# Spec — Smarter Rough Cut + Timeline UX (video-driven)

_Date: 2026-06-03 · Branch: `sw-iso` · Status: approved design_

## Context

Laura's "Rough Cut aus Szenen bauen" currently splits the **whole** video into one clip per
detected shot — it segments but never actually cuts anything out, and the timeline is hard to
refine. The user wants both **(a)** a smarter, **video-driven** auto-cut (their "Mathe RGBs"
intuition — colour/image math) that drops weak material out of the box, and **(b)** an
excellent timeline editing UX. Agreed approach: **combined + staged** — ship a deterministic,
GPU-free quality pass and the inspector first, then direct-manipulation (drag), then optional
ML refinement.

This evolves the in-progress Scene Workbench milestone (P1 from-shots already shipped on
`sw-iso`; P2 4-zone layout shipped). It refines `from-shots` into a *filtering* build and adds
a backend quality pass + timeline quality UX.

## Goals

- Auto-cut produces a **shorter, cleaner** rough cut: drop black/leader frames, frozen/static
  shots, near-duplicate shots; merge sub-threshold micro-shots.
- Better shot **boundaries** via a stronger detector (AdaptiveDetector) — TransNetV2 optional.
- The user can **see why** a scene was dropped and **re-include** it in one click.
- Frame-accurate refinement (SceneInspector) now; **drag** trim/reorder next.

## Non-goals (this spec)

- Speech/transcript-driven cutting (user chose video-driven; revisit later).
- Multi-lane timeline, assembled-sequence "replay" (still deferred).
- TransNetV2 is **optional/last** (heavy extra, GPU); not required for value.

## Decisions

- **Quality metrics live on the `shots` row** (1:1, no join), not a separate table.
- **Default detector → `AdaptiveDetector`** (`content`/`histogram` selectable).
- **Stage order:** filter + badges + inspector → drag (trim/reorder) → TransNetV2.
- All Stage 1/2 math is **deterministic, CPU, ffmpeg/numpy** — no GPU/heavy-model dependency.

## Backend design

### Shot detection (`analysis/shots.py`)
`detect_shots(video, *, detector="adaptive", threshold=...)` selecting PySceneDetect
`AdaptiveDetector` (default) / `ContentDetector` / `HistogramDetector`. Output stays
end-exclusive source-frame ranges (invariant).

### Quality pass (`analysis/quality.py`, new)
Runs after detection, in the analysis job. For each shot, sample K frames and compute,
deterministically:
- `black_ratio` — fraction of sampled frames whose mean luma < ε (leader/black). ffmpeg
  `blackdetect` or mean-luma of sampled frames.
- `static_score` — inter-frame mean-abs-difference variance (≈0 → frozen/static). ffmpeg
  `freezedetect` or numpy frame diffs.
- `phash` — 64-bit perceptual hash (dHash) of a representative mid-shot frame; duplicates =
  Hamming distance ≤ d.
- `blur_score` — variance of the Laplacian (focus measure); very low → blurry. _(optional)_
A pure-Python `decide_keep(metrics, thresholds)` returns `(keep: bool, drop_reason: str|None)`
where reason ∈ {black, static, duplicate, blur, micro}. Near-duplicate is resolved across the
shot list (keep the first of a hash cluster).

### Storage (migration)
Add nullable columns to `shots`: `black_ratio REAL`, `static_score REAL`, `phash TEXT`,
`blur_score REAL`, `keep INTEGER`, `drop_reason TEXT`. Portable statement-wise migration.
Metrics are part of the analysis run ⇒ **bump `PIPELINE_VERSION`** (idempotency by
`(input, pipeline_version)` stays exact).

### `from-shots` becomes filtering (`api/timelines.py`)
`FromShotsRequest` gains a master `quality: bool` (default true) that enables the standard
drops + micro-merge, with per-filter overrides (`drop_black`, `drop_static`,
`drop_duplicates: bool`, `merge_min_frames: int`). The endpoint:
- skips shots whose `drop_reason` matches an enabled filter,
- merges runs shorter than `merge_min_frames` into the previous kept clip,
- builds the contiguous, end-exclusive, speed-1/1 cut (as today),
- returns `TimelineOut` **plus `dropped: [{shot_id, drop_reason}]`** so the UI can show
  "kept N of M" + re-include. Non-destructive timeline handling unchanged.
The `shots` read endpoint already returns rows; extend `ShotOut` with the metric fields so the
UI can render badges.

## Frontend design (staged)

### Stage 1 — quality-aware timeline + inspector
- Scene blocks: taller, thumbnail + duration + a **quality badge** (drop reason) for dropped
  scenes shown in a muted "Verworfen"-tray; click re-includes (append at the right seq spot).
- **SceneInspector** (the Scene Workbench P5): IN/OUT filmstrips via `assetFrameUrl`, audio
  window (waveform sliced to the clip's source range — 16 kHz mapping), frame-accurate nudge →
  `trim` op. Seeks the player to the edited cut.

### Stage 2 — direct manipulation
- Drag clip **edges** to trim and drag clip **body** to reorder. Needs a new backend
  `move_clip(clips, at_seq_frame, to_seq_frame)` op (atomic delete+insert with ripple) +
  `op:"move"` in the operations endpoint. Magnetic snapping to neighbour cuts + ripple.

### Stage 3 — optional ML
- TransNetV2 boundary refinement as an optional extra (`[scene-ml]`), GPU-accelerated; a
  second pass that refines/snaps boundaries. Graceful-skip when absent.

## Data flow

Analyse → shots + metrics persisted → `GET /assets/{id}/shots` returns metrics → user clicks
"Rough Cut aus Szenen bauen" → `from-shots(quality)` filters/merges, returns cut + dropped
list → UI renders kept blocks + dropped tray → user re-includes / trims (inspector) / drags
(Stage 2).

## Testing

- **Backend (pytest, deterministic):** synthetic fixtures via ffmpeg — `color=black` clip →
  high `black_ratio`/`keep=0/black`; a frozen clip (single frame looped) → high `static_score`;
  two identical clips → equal `phash` (duplicate). `decide_keep` unit table. `from-shots`
  filtering: dropped reasons skipped, micro-shots merged, kept cut contiguous/end-exclusive.
  `move_clip` (Stage 2) ripple/ordering tests mirroring `test_editing_*`.
- **Frontend (vitest):** badge + re-include + trim wiring (mirror `TimelineBar.test`). UI paths
  marked "manuell zu prüfen".
- Time invariants (integer frames, end-exclusive, audio in samples) preserved throughout.

## Risks / edge cases

Over-aggressive drops (thresholds conservative + everything re-includable); a whole video of
near-duplicates (don't drop the last survivor); micro-merge across asset/lane boundaries;
`PIPELINE_VERSION` bump re-runs analysis (expected); phash false-positives on similar framing
(tune Hamming distance); ffmpeg `blackdetect`/`freezedetect` availability (fallback to numpy).

## Sequence (portions, commit+push each on `sw-iso`)

1. Backend: detector option (AdaptiveDetector default) + tests.
2. Backend: quality pass + metrics migration + `PIPELINE_VERSION` bump + tests.
3. Backend: `from-shots` filtering + dropped-list + `ShotOut` metrics + tests.
4. Frontend: scene blocks + quality badges + dropped tray (re-include).
5. Frontend: SceneInspector (filmstrips + audio + nudge→trim) — also the Scene Workbench P5.
6. Backend+Frontend (Stage 2): `move_clip` op + drag trim/reorder + snapping.
7. (Optional, Stage 3) TransNetV2 refinement extra.

Re-transcription (Scene Workbench P6/P7) remains a separate, parallel track.
