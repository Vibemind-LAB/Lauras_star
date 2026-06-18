# VV1 Audio-Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a true sequence-level audio lane for imported music/voiceover assets: store clips in integer frames, show/edit them in Assemble, and render them through the existing ffmpeg MP4 path with gain and simple fades.

**Architecture:** Keep video/storyboard state unchanged. Add a dedicated `timeline_audio_clips` layer tied to timelines, then adapt render/export to merge legacy scene music and the new sequence audio overlays into one structured audio overlay list. The UI exposes the lane in the existing transcript-first Assemble workspace, inside the Tools rail and eventually as an A2 strip beneath the current TimelineBar.

**Tech Stack:** SQLite migrations + repository helpers, FastAPI/Pydantic, pytest, ffmpeg render helper, React/TypeScript strict renderer, Vitest/Testing Library.

---

## File Map

- `services/local-api/src/laura/db/migrations/0018_timeline_audio_clips.sql`: additive schema for sequence-level audio clips.
- `services/local-api/src/laura/db/repos.py`: CRUD helpers for audio clips.
- `services/local-api/src/laura/api/models.py`: `TimelineAudioClip*` request/response models.
- `services/local-api/src/laura/api/audio.py`: new audio-lane endpoints.
- `services/local-api/src/laura/main.py`: register the audio router.
- `services/local-api/src/laura/render/audio.py`: typed audio overlay descriptor and ffmpeg filter helpers.
- `services/local-api/src/laura/render/mp4.py`: accept structured audio overlays and support fades while preserving legacy `music_tracks`.
- `services/local-api/src/laura/render/handlers.py`: include sequence-level audio clips in render jobs.
- `services/local-api/src/laura/sequences/music.py`: keep legacy scene music; optionally convert it to the shared overlay type.
- `apps/desktop/src/api.ts`: typed client methods for audio clips.
- `apps/desktop/src/components/AudioLaneControls.tsx`: audio clip add/list/update/remove controls.
- `apps/desktop/src/components/AssembleView.tsx`: wire Audio-Lane into the Tools tab and refresh sequence/render state.
- `apps/desktop/src/components/TimelineBar.tsx`: later task, draw A2 overlay strip if passed audio clips.

## Data Model

`timeline_audio_clips` stores additive audio-only clips:

- `id TEXT PRIMARY KEY`
- `timeline_id TEXT NOT NULL REFERENCES timelines(id) ON DELETE CASCADE`
- `asset_id TEXT NOT NULL REFERENCES media_assets(id) ON DELETE CASCADE`
- `seq_in_frame INTEGER NOT NULL`
- `seq_out_frame_exclusive INTEGER NOT NULL`
- `asset_in_frame INTEGER NOT NULL DEFAULT 0`
- `gain_percent INTEGER NOT NULL DEFAULT 100`
- `fade_in_frames INTEGER NOT NULL DEFAULT 0`
- `fade_out_frames INTEGER NOT NULL DEFAULT 0`
- `mix_mode TEXT NOT NULL DEFAULT 'mix'`
- `label TEXT`
- `created_at TEXT NOT NULL`

Invariants:

- Sequence ranges are integer frames and end-exclusive.
- `seq_out_frame_exclusive > seq_in_frame`.
- `asset_in_frame >= 0`.
- `gain_percent` is clamped/validated to `0..400`.
- fades are non-negative and together cannot exceed the clip duration.
- v1 render supports `mix`; other modes can be stored only after render semantics are implemented.

## Task 1: Backend Storage And API

- [x] Add failing tests in `services/local-api/tests/test_timeline_audio_clips.py`:
  - migration creates the table and indexes.
  - repo creates/lists/updates/deletes clips in sequence order.
  - invalid frame/fade/gain values are rejected through the API.
  - unknown timeline/asset returns 404.
- [x] Add migration `0018_timeline_audio_clips.sql`.
- [x] Add repo helpers:
  - `add_timeline_audio_clip`
  - `list_timeline_audio_clips`
  - `get_timeline_audio_clip`
  - `update_timeline_audio_clip`
  - `delete_timeline_audio_clip`
- [x] Add Pydantic models:
  - `TimelineAudioClipOut`
  - `TimelineAudioClipCreate`
  - `TimelineAudioClipUpdate`
- [x] Add `api/audio.py` endpoints:
  - `GET /timelines/{timeline_id}/audio-clips`
  - `POST /timelines/{timeline_id}/audio-clips`
  - `PATCH /timelines/{timeline_id}/audio-clips/{clip_id}`
  - `DELETE /timelines/{timeline_id}/audio-clips/{clip_id}`
- [x] Register the router in `main.py`.
- [x] Run:
  - `uv run pytest services/local-api/tests/test_timeline_audio_clips.py`

## Task 2: Render Mix And Fades

- [x] Add failing tests in `services/local-api/tests/test_render_audio_overlays.py`:
  - structured overlays produce an `afade` + `adelay` + `amix` filter graph.
  - legacy `music_tracks` still renders with the existing behavior.
  - fade durations are projected from frames to seconds with project rate.
- [x] Introduce `render/audio.py` with `AudioOverlay`.
- [x] Refactor `render/mp4.py` to accept `audio_overlays` while keeping `music_tracks`.
- [x] Include clip `asset_in_frame` by trimming from the audio asset source offset.
- [x] Ensure no-overlay render remains video-only and backward compatible.
- [x] Run:
  - `uv run pytest services/local-api/tests/test_render_audio_overlays.py services/local-api/tests/test_render_music.py`

## Task 3: Render Job Integration

- [x] Add failing tests in `services/local-api/tests/test_render_handler_options.py`:
  - sequence render includes timeline audio clips.
  - legacy scene music still contributes overlays.
  - render fails clearly when an audio asset file is missing.
- [x] Add resolver function for timeline audio overlays.
- [x] Update `render/handlers.py` to pass both scene music and timeline audio clips.
- [x] Run:
  - `uv run pytest services/local-api/tests/test_render_handler_options.py services/local-api/tests/test_render_audio_overlays.py services/local-api/tests/test_render_music.py`

## Task 4: Frontend Client And Controls

- [x] Add failing tests for `apps/desktop/src/api.ts` client methods if existing client tests cover API calls.
- [x] Add strict TS interfaces:
  - `TimelineAudioClip`
  - `TimelineAudioClipCreate`
  - `TimelineAudioClipUpdate`
- [x] Add `LauraClient` methods:
  - `listTimelineAudioClips`
  - `createTimelineAudioClip`
  - `updateTimelineAudioClip`
  - `deleteTimelineAudioClip`
- [x] Create `AudioLaneControls.tsx`:
  - filter project assets to audio-capable assets (`type === "audio"` or `codec_audio !== null`).
  - choose asset, sequence in/out, gain, fade in/out.
  - list existing clips with remove.
  - call `onChange` after mutations.
- [x] Wire controls into `AssembleView` Tools tab.
- [x] Run:
  - `pnpm --dir apps/desktop test -- AudioLaneControls`
  - `pnpm --dir apps/desktop exec tsc --noEmit`

## Task 5: Visual A2 Strip

- [x] Add a prop to `TimelineBar` for `audioClips`.
- [x] Draw a stable A2 lane under existing A1 with clip labels and fade/gain hints.
- [x] Keep dimensions fixed so hover/labels cannot resize the timeline.
- [x] Add component tests for render without overlap/regression.
- [x] Run:
  - `pnpm --dir apps/desktop test -- TimelineBar`
  - `pnpm --dir apps/desktop exec tsc --noEmit`

## Task 6: Docs, Tasks, And Gates

- [x] Update `tasks/todo.md` from VV1 planned to implemented details.
- [x] Run scoped backend tests from tasks 1-3.
- [x] Run full gates if scoped tests pass:
  - `uv run pytest` — green, 691 passed / 10 skipped.
  - `uv run ruff check` — still red on pre-existing test lint debt outside VV1; scoped VV1 files are green.
  - `uv run mypy src/laura` — green.
  - `pnpm --dir apps/desktop test` — green, 125 passed.
  - `pnpm --dir apps/desktop exec tsc --noEmit` — green.
  - `pnpm --dir apps/desktop run build:renderer` — green.
- [ ] Commit with a conventional commit:
  - `feat: add sequence audio lane`

## Self-Review

- [x] Search for placeholders:
  - `rg -n "TODO|TBD|placeholder|any\\b|console\\.log" services/local-api/src apps/desktop/src`
- [x] Confirm all new frame ranges are integer/end-exclusive.
- [x] Confirm no heavy audio/model dependency is introduced.
- [x] Confirm no unrelated dirty worktree files are staged.
