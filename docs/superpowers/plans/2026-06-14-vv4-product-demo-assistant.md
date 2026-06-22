# VV4 Product-Demo Assistant Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a draft-first Product-Demo Assistant that converts a screenrecording asset into editable sequence suggestions.

**Architecture:** Add persistent `demo_drafts`, a pure draft builder, a `demo.analyze` job, API endpoints, and a small Assemble Tools panel. Applying a draft creates normal Laura rough-cut scenes and sequence items.

**Tech Stack:** SQLite migrations, repository helpers, FastAPI/Pydantic, pytest, React/TypeScript strict, Vitest.

---

### Task 1: Backend Draft Model

**Files:**
- Create: `services/local-api/src/laura/db/migrations/0022_demo_drafts.sql`
- Modify: `services/local-api/src/laura/db/repos.py`
- Modify: `services/local-api/src/laura/api/models.py`
- Test: `services/local-api/tests/test_demo_drafts.py`

- [ ] Write failing tests for create/get/update/apply draft storage.
- [ ] Add `demo_drafts` table with `asset_id`, `project_id`, `status`, `items_json`, timestamps.
- [ ] Add repo helpers `create_demo_draft`, `get_demo_draft`, `update_demo_draft`.
- [ ] Add Pydantic models for draft items and responses.
- [ ] Run `uv run pytest tests/test_demo_drafts.py -q`.

### Task 2: Draft Builder And Job

**Files:**
- Create: `services/local-api/src/laura/demo/drafts.py`
- Create: `services/local-api/src/laura/demo/handlers.py`
- Modify: `services/local-api/src/laura/main.py`
- Test: `services/local-api/tests/test_demo_drafts.py`

- [ ] Write failing tests for shot-based and fallback draft items.
- [ ] Implement `build_demo_draft_items(db, asset_id)`.
- [ ] Implement `handle_demo_analyze`.
- [ ] Register `demo.analyze` in the job registry.
- [ ] Run focused pytest.

### Task 3: API And Apply

**Files:**
- Create: `services/local-api/src/laura/api/demo.py`
- Modify: `services/local-api/src/laura/main.py`
- Test: `services/local-api/tests/test_demo_drafts.py`

- [ ] Write failing API tests for POST/GET/PATCH/APPLY.
- [ ] Implement endpoints.
- [ ] Apply creates a new rough-cut timeline, scenes, materialized scene timelines, and sequence items.
- [ ] Run focused pytest, ruff, mypy.

### Task 4: Frontend Client And Panel

**Files:**
- Modify: `apps/desktop/src/api.ts`
- Create: `apps/desktop/src/components/DemoAssistantPanel.tsx`
- Test: `apps/desktop/src/api.test.ts`
- Test: `apps/desktop/src/components/DemoAssistantPanel.test.tsx`

- [ ] Add TypeScript interfaces and client methods.
- [ ] Add component tests for create/edit/apply.
- [ ] Build the panel without `any`.
- [ ] Run focused Vitest and `tsc`.

### Task 5: Assemble Wiring, Docs, Gates

**Files:**
- Modify: `apps/desktop/src/components/AssembleView.tsx`
- Modify: `tasks/todo.md`

- [ ] Wire panel into Tools tab.
- [ ] Mark VV4 completed with verification.
- [ ] Run backend focused/full gates and desktop gates.
