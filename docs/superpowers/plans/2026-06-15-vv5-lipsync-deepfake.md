# VV5 Lipsync/Deepfake Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a consent- and license-gated lipsync/deepfake sidecar path that produces a synthetically marked replace-overlay asset with probe and quality evidence.

**Architecture:** Laura stays model-free: it renders the selected sequence range and sends that video plus a chosen audio/voiceover asset to a pluggable lipsync backend. The safe `stub` backend is always available for wiring; the real VibeVideo/MuseTalk/Wav2Lip path is an HTTP sidecar selected with `LAURA_LIPSYNC_URL`, never imported into the core process.

**Tech Stack:** FastAPI, SQLite repos, DB job runner, ffmpeg render helpers, React/Vitest, TypeScript strict.

---

## File Structure

- Create `services/local-api/src/laura/ai/lipsync_backend.py`: backend protocol, safe stub, HTTP sidecar adapter, probe/quality result types.
- Modify `services/local-api/src/laura/ai/handlers.py`: register and implement `ai.lipsync`.
- Modify `services/local-api/src/laura/api/models.py`: add `LipsyncRequest`, `LipsyncAccepted`.
- Create `services/local-api/src/laura/api/lipsync.py`: `POST /timelines/{id}/lipsync`.
- Modify `services/local-api/src/laura/main.py`, `jobs/queues.py`, `jobs/celery_app.py`: router and job routing.
- Create `services/local-api/tests/test_lipsync_job.py`: consent/license/probe/quality/synthetic overlay behavior.
- Create `services/local-api/tests/test_lipsync_api.py`: API validation and enqueue payload.
- Modify `apps/desktop/src/api.ts` and `api.test.ts`: typed `lipsync()` client.
- Create `apps/desktop/src/components/LipsyncPanel.tsx` and `.test.tsx`: UI guardrails.
- Modify `apps/desktop/src/components/AssembleView.tsx`: add the panel to the Tools rail.
- Modify `tasks/todo.md`: mark VV5 only after gates pass.

## Task 1: Backend RED Tests

**Files:**
- Create: `services/local-api/tests/test_lipsync_job.py`
- Create: `services/local-api/tests/test_lipsync_api.py`

- [ ] **Step 1: Write failing job tests**

Cover these behaviors:

```python
def test_lipsync_refuses_missing_license_before_creating_assets(tmp_path: Path) -> None:
    # enqueue ai.lipsync without license_accepted
    # expect failed job, no synthetic lipsync asset, no replace clip
```

```python
def test_lipsync_refuses_revoked_consent_before_sidecar(tmp_path: Path) -> None:
    # create consent, revoke it, enqueue ai.lipsync
    # expect failed job and no output
```

```python
def test_lipsync_stub_e2e_creates_synthetic_replace_asset(tmp_path: Path) -> None:
    # valid consent + license + audio asset + seq range
    # expect succeeded job, synthetic asset ai_effect="lipsync", replace clip on lane >= 1,
    # and result_json containing probe + quality metrics
```

```python
def test_lipsync_probe_failure_creates_no_synthetic_asset(monkeypatch, tmp_path: Path) -> None:
    # monkeypatch backend probe to return face_detected=False
    # expect failed job, no asset, no output file
```

```python
def test_lipsync_quality_gate_failure_creates_no_synthetic_asset(monkeypatch, tmp_path: Path) -> None:
    # monkeypatch backend quality below threshold
    # expect failed job, no asset
```

- [ ] **Step 2: Run RED**

Run:

```powershell
uv run pytest tests/test_lipsync_job.py tests/test_lipsync_api.py -q
```

Expected: FAIL because modules/endpoints are not implemented.

## Task 2: Backend GREEN

**Files:**
- Create: `services/local-api/src/laura/ai/lipsync_backend.py`
- Modify: `services/local-api/src/laura/ai/handlers.py`
- Modify: `services/local-api/src/laura/api/models.py`
- Create: `services/local-api/src/laura/api/lipsync.py`
- Modify: `services/local-api/src/laura/main.py`
- Modify: `services/local-api/src/laura/jobs/queues.py`
- Modify: `services/local-api/src/laura/jobs/celery_app.py`

- [ ] **Step 1: Implement backend protocol**

`lipsync_backend.py` defines:

```python
@dataclass(frozen=True)
class LipsyncProbe:
    face_detected: bool
    mouth_visible: bool
    audio_present: bool
    reason: str | None = None

@dataclass(frozen=True)
class LipsyncQuality:
    sync_score: float
    mouth_score: float
    temporal_score: float
    passed: bool

class LipsyncBackend(Protocol):
    name: str
    def available(self) -> bool: ...
    def probe(self, *, video_path: Path, audio_path: Path) -> LipsyncProbe: ...
    def lipsync(self, *, video_path: Path, audio_path: Path, out_path: Path, fps_num: int, fps_den: int) -> LipsyncQuality: ...
```

Stub behavior: always available, validates files are non-empty, renders a visibly labeled MP4 from the driving range, returns scores above threshold.

- [ ] **Step 2: Implement handler gates in order**

`handle_lipsync(ctx)` must:

1. Require `license_accepted is True`; fail before file IO otherwise.
2. Require existing, non-revoked `consent_id`; fail before file IO otherwise.
3. Resolve timeline/project/audio asset/range.
4. Render base video range to temp MP4.
5. Resolve backend and run `probe`; fail before output asset if face/mouth/audio unsuitable.
6. Run backend to temp synthetic MP4.
7. Run `assert_or_fix_media_sync(..., require_video=True, fix=True)`.
8. Reject quality below `quality_threshold`.
9. Create `synthetic=True`, `ai_effect="lipsync"` video asset.
10. Place replace-overlay clip covering `[seq_in_frame, seq_out_frame_exclusive)`.
11. Return `asset_id`, `probe`, `quality`, source frames, consent id.

- [ ] **Step 3: Implement API endpoint**

`POST /timelines/{timeline_id}/lipsync` accepts:

```python
class LipsyncRequest(BaseModel):
    seq_in_frame: int = Field(ge=0)
    seq_out_frame_exclusive: int = Field(gt=0)
    audio_asset_id: str
    consent_id: str
    license_accepted: bool
    backend: str | None = None
    quality_threshold: float = Field(default=0.6, ge=0.0, le=1.0)
```

Validator requires out > in and `license_accepted is True`.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
uv run pytest tests/test_lipsync_job.py tests/test_lipsync_api.py -q
uv run ruff check src/laura/ai/lipsync_backend.py src/laura/ai/handlers.py src/laura/api/lipsync.py src/laura/api/models.py tests/test_lipsync_job.py tests/test_lipsync_api.py
uv run mypy src/laura/ai/lipsync_backend.py src/laura/ai/handlers.py src/laura/api/lipsync.py src/laura/api/models.py
```

Expected: PASS.

## Task 3: Desktop RED Tests

**Files:**
- Modify: `apps/desktop/src/api.test.ts`
- Create: `apps/desktop/src/components/LipsyncPanel.test.tsx`

- [ ] **Step 1: Write failing API test**

Assert `LauraClient.lipsync("tl", {...})` POSTs `/timelines/tl/lipsync` with snake_case fields and `license_accepted`.

- [ ] **Step 2: Write failing panel tests**

Cover:

```tsx
it("keeps lipsync disabled until consent and license are confirmed", async () => {})
it("submits lipsync with audio asset, consent id, frame range and backend", async () => {})
```

- [ ] **Step 3: Run RED**

Run:

```powershell
pnpm --dir apps/desktop test -- src/api.test.ts src/components/LipsyncPanel.test.tsx
```

Expected: FAIL because client/panel are not implemented.

## Task 4: Desktop GREEN

**Files:**
- Modify: `apps/desktop/src/api.ts`
- Create: `apps/desktop/src/components/LipsyncPanel.tsx`
- Modify: `apps/desktop/src/components/AssembleView.tsx`

- [ ] **Step 1: Implement client**

Add:

```ts
export interface LipsyncOptions {
  seqIn: number;
  seqOut: number;
  audioAssetId: string;
  consentId: string;
  licenseAccepted: boolean;
  backend?: string;
  qualityThreshold?: number;
}
```

`lipsync(timelineId, opts)` POSTs snake_case payload and returns `{ job_id: string }`.

- [ ] **Step 2: Implement panel**

Panel behavior:

- Reuses `createConsent`.
- Filters audio assets by `asset.type === "audio" || asset.codec_audio !== null`.
- Requires explicit checkbox `Lizenz/Consent-Nutzung bestätigt`.
- Requires integer frames and out > in.
- Shows backend `stub` / `VibeVideo Sidecar`.
- Calls `client.lipsync(...)` and `onChange()` after job enqueue.

- [ ] **Step 3: Wire into Assemble Tools**

Place `LipsyncPanel` near Reenact, pass `client`, `projectId`, `timelineId`, full `assets`, and reload callbacks.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
pnpm --dir apps/desktop test -- src/api.test.ts src/components/LipsyncPanel.test.tsx src/components/AssembleView.test.tsx
pnpm --dir apps/desktop exec tsc --noEmit
```

Expected: PASS.

## Task 5: Verification + Todo

**Files:**
- Modify: `tasks/todo.md`

- [ ] **Step 1: Full gates**

Run:

```powershell
uv run pytest -q
uv run ruff check .
uv run mypy src
pnpm --dir apps/desktop test
pnpm --dir apps/desktop exec tsc --noEmit
pnpm --dir apps/desktop run build:renderer
```

- [ ] **Step 2: Mark VV5**

Only after gates pass, mark VV5 `[x]` with the exact verification list and honest follow-ups: real sidecar install/model weights, C2PA/Video Seal, stronger mouth/identity metrics.
