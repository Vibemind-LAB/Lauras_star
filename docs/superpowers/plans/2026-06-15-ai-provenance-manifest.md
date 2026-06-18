# AI Provenance Manifest Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a dep-free second labeling layer for AI outputs: a machine-readable provenance JSON manifest next to every synthetic asset, and visible synthetic metadata in the Desktop asset list.

**Architecture:** Real C2PA/Video Seal remains a future optional integration; this slice writes a deterministic Laura provenance sidecar JSON immediately after synthetic media is validated and before/with asset registration. The API exposes existing `media_assets.synthetic` and `ai_effect` fields, and the renderer surfaces them as compact badges.

**Tech Stack:** Python stdlib JSON/hashlib/pathlib, existing SQLite asset rows, FastAPI models, React/Vitest.

---

## Task 1: Backend RED Tests

**Files:**
- Create: `services/local-api/tests/test_ai_provenance.py`
- Modify: `services/local-api/tests/test_voiceover.py`

- [ ] **Step 1: Write failing tests**

Required behaviors:

```python
def test_write_ai_provenance_manifest_contains_required_fields(tmp_path: Path) -> None:
    manifest = write_ai_provenance_manifest(...)
    assert manifest["schema"] == "laura.ai.provenance.v1"
    assert manifest["ai_effect"] == "lipsync"
    assert manifest["synthetic"] is True
    assert manifest["media_sha256"]
```

```python
def test_voiceover_job_writes_provenance_manifest(client: TestClient, tmp_path: Path) -> None:
    # run existing voiceover path
    # assert Path(asset["source_path"] + ".laura-provenance.json").exists()
    # assert manifest contains asset_id, ai_effect="voiceover", timeline_id and range
```

- [ ] **Step 2: Run RED**

Run:

```powershell
uv run pytest tests/test_ai_provenance.py tests/test_voiceover.py::test_voiceover_job_writes_provenance_manifest -q
```

Expected: FAIL because the provenance module and manifest writes do not exist.

## Task 2: Backend GREEN

**Files:**
- Create: `services/local-api/src/laura/ai/provenance.py`
- Modify: `services/local-api/src/laura/ai/handlers.py`
- Modify: `services/local-api/src/laura/api/models.py`

- [ ] **Step 1: Implement manifest writer**

Create `write_ai_provenance_manifest(media_path, asset_id, project_id, ai_effect, source, synthetic=True) -> Path`.

Manifest keys:

- `schema`
- `asset_id`
- `project_id`
- `synthetic`
- `ai_effect`
- `media_path`
- `media_sha256`
- `created_at`
- `source`

- [ ] **Step 2: Wire into synthetic job handlers**

Call the writer after `repos.create_asset(...)` in `handle_reenact`, `handle_voiceover`, and `handle_lipsync`. Include source range, timeline id, consent id when present, backend name when present, and quality/probe for lipsync.

- [ ] **Step 3: Expose asset flags**

Add `synthetic: bool = False` and `ai_effect: str | None = None` to `AssetOut`.

- [ ] **Step 4: Run GREEN**

Run:

```powershell
uv run pytest tests/test_ai_provenance.py tests/test_voiceover.py::test_voiceover_job_writes_provenance_manifest tests/test_reenact_job.py tests/test_lipsync_job.py -q
uv run ruff check src/laura/ai/provenance.py src/laura/ai/handlers.py src/laura/api/models.py tests/test_ai_provenance.py
uv run mypy src/laura/ai/provenance.py src/laura/ai/handlers.py src/laura/api/models.py
```

## Task 3: Desktop RED/GREEN

**Files:**
- Modify: `apps/desktop/src/api.ts`
- Modify: `apps/desktop/src/components/MediaSidebar.tsx`
- Modify: `apps/desktop/src/components/MediaSidebar.test.tsx`

- [ ] **Step 1: RED test**

Add a synthetic asset fixture and assert the sidebar shows `KI · lipsync`.

- [ ] **Step 2: GREEN**

Add `synthetic`/`ai_effect` fields to `Asset`, render a compact badge next to analyzed status.

- [ ] **Step 3: Verify**

Run:

```powershell
pnpm --dir apps/desktop test -- src/components/MediaSidebar.test.tsx src/api.test.ts
pnpm --dir apps/desktop exec tsc --noEmit
```

## Task 4: Full Verification

- [ ] Run full backend and desktop gates.
- [ ] Update `tasks/todo.md` under the R3/VV follow-ups with “AI Provenance Manifest v1”.
- [ ] Restart the app and check `/healthz` plus renderer.
