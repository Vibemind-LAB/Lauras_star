# Transition Review — Model + API + UI + Harness (Plan C) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development / executing-plans. Steps use checkbox (`- [ ]`).

**Goal:** Make the Plan B review engine usable end-to-end: a real local VLM backend (Ollama / Qwen3-VL), an eval harness to pick the default model on 12 GB VRAM, the API + api.ts client, and a "Übergänge prüfen" panel.

**Architecture:** `OllamaVlmBackend` implements `VlmBackend` over a local Ollama server (deterministic sampling + forced JSON). `default_backend()` returns it when configured (env `LAURA_VLM_MODEL` + Ollama reachable), else None (Plan B stub path for tests). API endpoints drive the `transition.review` job and serve cached verdicts + apply-fix. Frontend adds a hook + panel. **Full spec:** [`2026-06-19-vlm-transition-smoothness-review-design.md`](../specs/2026-06-19-vlm-transition-smoothness-review-design.md) §4.5/§7/§8.

**Tech Stack:** Python (httpx/urllib to Ollama), FastAPI, React/TS, ffmpeg (via Plan B extract_frames).

## Global Constraints

- **Optional & local-first:** the model is the `[vlm]` extra; backend starts/serves cache + apply-fix without it. GET/apply-fix never need the model; only POST review does.
- **Determinism (#7):** Ollama options `temperature=0, top_k=1, top_p=1.0, seed=fixed`; forced JSON schema; `model_digest` = Ollama model sha. Frame bytes from Plan B's deterministic extractor.
- **No `print`** → logger. Strict mypy, ruff clean. TS `strict`, no `any`.
- **Testability:** the model HTTP call is isolated behind a small seam; the JSON→verdict parse + availability gating are unit-tested without Ollama. API tested with TestClient (cache + apply-fix paths) and the stub for the job path.

---

### Task C1: `OllamaVlmBackend`

**Files:** Create `src/laura/analysis/vlm_ollama.py`; modify `transition_review.py` (`default_backend` resolves it); Test `tests/test_vlm_ollama_parse.py`.

**Interfaces — Produces:**
- `class OllamaVlmBackend(VlmBackend)` with `host`, `model` (default `qwen3-vl:8b` or `LAURA_VLM_MODEL`), `available()` (GET `/api/tags` contains the model), `model_id()` (the model name), `model_digest()` (the model's sha from `/api/tags`, cached), `review(frames, meta)` → POST `/api/chat` with base64 images + the prompt + `options{temperature:0,top_k:1,top_p:1,seed:0}` + `format=<schema>`, then `_parse_verdict(json)`.
- `_parse_verdict(obj: dict) -> TransitionVerdict` — pure; clamps smoothness to [0,1], coerces label/fix-kind to the allowed Literals (unknown → `smooth`/`none`), defaults missing fields.
- `_review_prompt()` / `_VERDICT_SCHEMA` constants.
- `transition_review.default_backend()` returns `OllamaVlmBackend()` when `LAURA_VLM_MODEL` is set (or a config flag) **and** it is `available()`, else None.

- [ ] **Step 1: Failing tests** (no Ollama) — `_parse_verdict` maps a well-formed JSON to a TransitionVerdict; clamps an out-of-range smoothness; coerces an unknown label to `smooth` and an unknown fix kind to `none`; tolerates missing optional fields.
- [ ] **Step 2: Run, FAIL.** **Step 3: Implement** the backend + parse + default_backend wiring (the HTTP methods call urllib; not exercised in unit tests). **Step 4: PASS.**
- [ ] **Step 5: Commit** `feat(review): OllamaVlmBackend (Qwen3-VL via local Ollama, deterministic JSON)`.

---

### Task C2: API — review / get / apply-fix

**Files:** Modify `src/laura/api/timelines.py` + `src/laura/api/models.py`; Test `tests/test_api_transition_review.py`.

**Interfaces — Produces** (spec §8):
- `POST /timelines/{id}/transitions/review` → enqueue `transition.review` job → `{job_id}` (202). 404 unknown timeline.
- `GET /timelines/{id}/transitions/review` → `{verdicts: [...], status}` from `list_transition_reviews` (each verdict includes boundary_seq_frame, asset_a/asset_b, src_out_a/src_in_b, smoothness, label, reason, suggested_fix, model_id, created_at).
- `POST /timelines/{id}/transitions/apply-fix` body `{identity:{asset_a,asset_b,src_out_a,src_in_b}, fix:{kind,resnap_delta_frames?,transition_style?,transition_frames?}}` → calls `transition_review.apply_fix` → `{status, ...}`. `timeline:edit` permission.

- [ ] **Step 1: Failing tests** (TestClient) — POST review on a rough_cut returns a job id (or 202); GET returns the verdicts seeded via `repos.upsert_transition_review`; apply-fix transition sets crossfade on the boundary's clip A; apply-fix on a missing boundary returns the error status; unknown timeline → 404.
- [ ] **Step 2–4: TDD** (Pydantic models: `TransitionReviewOut`, `ApplyFixRequest`/`ApplyFixOut`; reuse SuggestedFix shape).
- [ ] **Step 5: Commit** `feat(api): transition review + apply-fix endpoints`.

---

### Task C3: api.ts client + `useTransitionReview` hook

**Files:** Modify `apps/desktop/src/api.ts`; create `apps/desktop/src/hooks/useTransitionReview.ts`.

**Interfaces — Produces:**
- `TransitionVerdict`/`SuggestedFix`/`TransitionReviewResult` types; `reviewTransitions(timelineId)`, `getTransitionReview(timelineId)`, `applyTransitionFix(timelineId, identity, fix)` methods.
- `useTransitionReview(client, timelineId)` → `{ verdicts, loading, run(), apply(verdict) }`, polling the GET endpoint while a run is in flight.

- [ ] **Step 1–4:** add types/methods; write the hook; `npm --prefix apps/desktop run typecheck` passes (no `any`).
- [ ] **Step 5: Commit** `feat(ui): api.ts transition-review methods + useTransitionReview hook`.

---

### Task C4: "Übergänge prüfen" panel

**Files:** Create `apps/desktop/src/components/TransitionReviewPanel.tsx`; wire into `FineCutView` (scene) and `AssembleView` (sequence).

**Interfaces — Consumes:** `useTransitionReview`. Renders a "Übergänge prüfen" button (runs the review), a per-boundary list (smoothness badge coloured by score, label, reason, suggested-fix chip), an "Anwenden"/"Alle anwenden" action, progress while running, and a one-time "Modell wird geladen…" note. A hint clarifies the crossfade appears in the export/render, not the live preview.

- [ ] **Step 1–4:** build the component (Tailwind, typed props, no `any`); wire it in; `typecheck` passes.
- [ ] **Step 5: Commit** `feat(ui): Übergänge prüfen panel`.

---

### Task C5: Eval harness `transition_bench`

**Files:** Create `src/laura/bench/transition_bench.py` (+ a small labelled fixture under `bench/fixtures/`); Test: a smoke that the harness assembles its scenarios (model runs are manual / gated).

**Interfaces — Produces:** a CLI that runs the configured candidate models (`qwen3-vl:8b`, `qwen3-vl:4b`, `smolvlm2:2.2b`) over a labelled set of boundaries, printing per-model label-agreement vs gold + latency/cut, and the recommended default. Deterministic; model runs gated on Ollama.

- [ ] **Step 1–5:** implement + a non-model smoke test (scenario loading / scoring math) + commit `feat(review): transition_bench eval harness (3 models)`.

---

### Task C6: Plan C verification

- [ ] `uv run pytest` green; `ruff check . && mypy src` clean; `npm --prefix apps/desktop run typecheck` clean.
- [ ] If feasible, a live smoke: launch the app, open a scene with a known jump cut, run "Übergänge prüfen" (requires Ollama + the model pulled) — otherwise document as manual.
- [ ] Final commit. A → B → C complete.

## Self-Review (Spec coverage)

- §4.5 OllamaVlmBackend/determinism/availability → C1. §7 harness → C5. §8 API → C2; api.ts/hook → C3; panel → C4. Determinism/optional invariants → C1 + tests. ✓
