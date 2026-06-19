# Transition Review Engine (Plan B) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** The local review core that enumerates cut boundaries, asks a `VlmBackend` how smooth each transition is, caches verdicts by semantic identity, and applies the chosen fix (resnap via a roll edit, or a transition via Plan A) — all testable without a model.

**Architecture:** New `analysis/transition_review.py` holds pure datatypes + boundary/frame/signature logic and the `VlmBackend` protocol with a deterministic `StubVlmBackend`. A new `transition_reviews` table (migration 0024) caches verdicts keyed by `(timeline_id, asset_a, asset_b, src_out_a, src_in_b, model_digest)`. `apply_fix` mutates the timeline through a new pure roll op + Plan A's `set_clip_transition`. A `transition.review` job ties it together. **Full spec:** [`2026-06-19-vlm-transition-smoothness-review-design.md`](../specs/2026-06-19-vlm-transition-smoothness-review-design.md) §3–§9.

**Tech Stack:** Python 3.11 (uv/pytest/ruff/mypy), SQLite migration, ffmpeg frame extraction.

## Global Constraints

- **Integer frames, end-exclusive** (#1/#2). **Idempotency (#7):** cache key is the *semantic* boundary identity (NOT the drifting seq position) + `model_digest`; `boundary_signature` is a stable hash. `temperature=0` etc. are Plan C (OllamaBackend) — Plan B's backend is the deterministic stub.
- **Optional & local-first:** `vlm_available()` guards; the stub needs no model. Tests run with the stub only (no model, no GPU).
- **No `print`** → logger. Strict typing (mypy), ruff clean.
- **Migration `0024_transition_reviews.sql`** (0023 is Plan A's clip transitions).

---

### Task B1: Module + datatypes + `boundary_signature` + `frame_strip_plan` (pure)

**Files:** Create `src/laura/analysis/transition_review.py`; Test `tests/test_transition_review_core.py`.

**Interfaces — Produces** (spec §4.1, §4.3):
- `@dataclass(frozen=True) Boundary` with `timeline_id, kind, asset_a, asset_b, src_in_a, src_out_a, src_in_b, src_out_b, seq_in_a, seq_out_a, removed_gap_frames, same_source`.
- `@dataclass(frozen=True) SuggestedFix(kind, resnap_delta_frames=0, transition_style="crossfade", transition_frames=0)`.
- `@dataclass(frozen=True) TransitionVerdict(smoothness, label, reason, suggested_fix)`.
- `boundary_signature(b: Boundary, k: int, proxy_version: str) -> str` — `sha256` of `timeline_id|asset_a|asset_b|src_out_a|src_in_b|removed_gap_frames|int(same_source)|k|proxy_version`.
- `frame_strip_plan(b: Boundary, k: int) -> list[tuple[str,int]]` — A-side `[max(src_in_a, src_out_a-k) .. src_out_a-1]` then B-side `[src_in_b .. min(src_in_b+k, src_out_b)-1]`, each as `(asset_id, frame)`; ≤k per side (short-clip safe).

- [ ] **Step 1: Failing tests** — signature stable + sensitive to src change; frame_strip_plan exact indices incl. short clip (A length 3, k=6 → 3 frames) and end-exclusive (last A frame == src_out_a-1).
- [ ] **Step 2: Run, FAIL** (`pytest tests/test_transition_review_core.py -v`).
- [ ] **Step 3: Implement** the dataclasses + the two pure functions.
- [ ] **Step 4: Run, PASS.**
- [ ] **Step 5: Commit** `feat(review): transition_review datatypes + signature + frame_strip_plan`.

---

### Task B2: `enumerate_boundaries` (kind-aware)

**Files:** Modify `transition_review.py`; Test `tests/test_enumerate_boundaries.py`.

**Interfaces — Produces:** `enumerate_boundaries(db, timeline_id) -> list[Boundary]` (spec §4.2).
- rough_cut/scene: lane-0 `list_timeline_clips` ordered by `seq_in_frame`; a Boundary between each adjacent pair. `same_source = (asset_a==asset_b and src_in_b==src_out_a)`; `removed_gap_frames = max(0, src_in_b - src_out_a)` when same asset else 0.
- sequence: walk `sequence_items`; for each adjacent scene pair resolve last clip of A / first clip of B via the scene timelines; boundary from those clip rows.

- [ ] **Step 1: Failing tests** — 3-clip rough_cut → 2 boundaries with right asset/src/seq fields; a contiguous same-source pair sets `same_source=True, removed_gap_frames=0`; a gap pair (src_in_b>src_out_a) sets `same_source=False, removed_gap_frames=gap`. (Sequence path: 2-scene seq → 1 boundary; can be a focused test if scene-seed helper exists, else mark a TODO test for B-followup.)
- [ ] **Step 2: Run, FAIL.** **Step 3: Implement.** **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(review): kind-aware enumerate_boundaries`.

---

### Task B3: `VlmBackend` protocol + `StubVlmBackend` + `vlm_available`

**Files:** Modify `transition_review.py`; Test `tests/test_stub_vlm_backend.py`.

**Interfaces — Produces:** `VlmBackend` Protocol (`available()`, `model_id()`, `model_digest()`, `review(frames: list[bytes], meta: dict) -> TransitionVerdict`); `StubVlmBackend` (deterministic: `meta["same_source"] and meta["removed_gap_frames"]==0` → `label="jump_cut", suggested_fix=SuggestedFix("transition", transition_style="crossfade", transition_frames=meta.get("k",6))`; else `label="smooth", suggested_fix=SuggestedFix("none")`); `vlm_available() -> bool` (Plan B: returns False unless an Ollama backend is registered — Plan C wires the real one).

- [ ] **Step 1: Failing tests** — stub returns jump_cut+transition for a same-source/zero-gap meta; smooth+none otherwise; `model_digest()` stable string.
- [ ] **Step 2–4: TDD.**
- [ ] **Step 5: Commit** `feat(review): VlmBackend protocol + deterministic StubVlmBackend`.

---

### Task B4: Migration 0024 + repos

**Files:** Create `src/laura/db/migrations/0024_transition_reviews.sql`; Modify `db/repos.py`; Test `tests/test_transition_reviews_repo.py`.

**Schema** (spec §4.6): table `transition_reviews` with the columns + `UNIQUE(timeline_id, asset_a, asset_b, src_out_a, src_in_b, model_digest)` + `ON DELETE CASCADE` to `timelines(id)` + index on `timeline_id`.

**Interfaces — Produces:** `upsert_transition_review(db, *, timeline_id, boundary, verdict, model_id, model_digest, boundary_signature, boundary_seq_frame) -> None`; `list_transition_reviews(db, timeline_id) -> list[dict]` (latest per identity); `get_cached_review(db, *, timeline_id, asset_a, asset_b, src_out_a, src_in_b, model_digest) -> dict | None`.

- [ ] **Step 1: Failing tests** — migration adds the table; upsert→get_cached round-trips; cache key is the semantic identity (changing `boundary_seq_frame` only → same row updated, still a cache hit); FK cascade: deleting the timeline removes its reviews (no orphan, cf. [[verify-against-fresh-build]]).
- [ ] **Step 2–4: TDD.**
- [ ] **Step 5: Commit** `feat(review): transition_reviews table (0024) + repos`.

---

### Task B5: `extract_frames` (IO)

**Files:** Modify `transition_review.py` (or `analysis/frames.py`); Test `tests/test_extract_frames.py` (real-ffmpeg gated).

**Interfaces — Produces:** `extract_frames(proxy_path, frame_refs, rate_num, rate_den) -> list[bytes]` — `t = frame*rate_den/rate_num`; `ffmpeg -ss {t} -i {proxy} -frames:v 1 -q:v 2 -f image2pipe -vcodec mjpeg -` per frame (JPEG bytes); out-of-bounds clamps; resolves proxy path via the asset file list (caller passes the resolved on-disk path).

- [ ] **Step 1: Failing test** (gated on ffmpeg): extract 4 frames from a generated testsrc → 4 non-empty JPEG byte blobs.
- [ ] **Step 2–4: TDD.**
- [ ] **Step 5: Commit** `feat(review): deterministic frame extraction for boundaries`.

---

### Task B6: `roll_clip_boundary` op + `apply_fix`

**Files:** Modify `editing/operations.py` (pure roll op); Modify `transition_review.py` (`apply_fix`); Test `tests/test_apply_transition_fix.py`.

**Interfaces — Produces:**
- `roll_clip_boundary(clips: list[EditClip], boundary_seq_frame: int, delta_frames: int) -> list[EditClip]` (pure): clip A (`seq_out==boundary`) gets `src_out += delta`; clip B (`seq_in==boundary`) gets `src_in += delta`; re-pack gaplessly via `ordered`/`_normalize_offsets`. Validates neither clip ≤0 length; raises `ValueError` on out-of-bounds (caller clamps).
- `apply_fix(db, *, timeline_id, identity, fix) -> dict` with `status in {"ok","not_supported","error"}`:
  - `resnap` → clamp `delta` to `[-(La-1), Lb-1]` ∩ `[-W, W]` (W=12); if 0 → `error`; else `roll_clip_boundary` + `replace_timeline_clips`. Works on rough_cut/scene/sequence (lane-0 clips).
  - `transition` → `repos.set_clip_transition` (Plan A) on clip A (or sequence-item for kind=sequence); `transition_style` → `transition_after_kind`.
  - `none` → `ok` no-op.

- [ ] **Step 1: Failing tests** — `roll_clip_boundary` worked example: A`[src100-200 seq0-100]`,B`[src200-300 seq100-200]`, δ=+10 → A`[100-210 seq0-110]`,B`[210-300 seq110-200]` (downstream end 200 unchanged); clamp prevents zero-length; `apply_fix` transition sets crossfade on clip A; `apply_fix` resnap δ=0 → `error`.
- [ ] **Step 2–4: TDD.**
- [ ] **Step 5: Commit** `feat(review): roll-boundary op + apply_fix (resnap + transition)`.

---

### Task B7: `transition.review` job handler

**Files:** Create `src/laura/analysis/transition_review_job.py` (or extend `analysis/handlers.py`); register in the job dispatch; Test `tests/test_transition_review_job.py` (stub backend, no ffmpeg — inject a fake `extract_frames`).

**Interfaces — Produces:** `handle_transition_review(ctx)` — enumerate boundaries → for each: cache lookup by identity+digest (hit → skip); else `extract_frames` → `backend.review(frames, meta)` → `upsert_transition_review`; progress `{"reviewed","total"}`.

- [ ] **Step 1: Failing tests** — with the stub backend + injected frame extractor: a 3-clip rough_cut with one same-source/zero-gap boundary → one `jump_cut` verdict persisted; a second run does **0** inferences (all cached — assert the backend call count).
- [ ] **Step 2–4: TDD.**
- [ ] **Step 5: Commit** `feat(review): transition.review job (enumerate→cache→review→persist)`.

---

### Task B8: Plan B verification

- [ ] `uv run pytest` green; `uv run ruff check . && uv run mypy src` clean.
- [ ] Commit any cleanup. Plan B complete → Plan C (model + UI + harness).

## Self-Review (Spec coverage)

- §3 idempotency/signature → B1 + B4. §4.1 datatypes → B1. §4.2 enumerate → B2. §4.3 frame_strip → B1; extract → B5. §4.5 backend/stub → B3. §4.6 persistence → B4. §5 apply_fix → B6. §6 job/cache → B7. §9 tests → every task (stub-only). ✓
