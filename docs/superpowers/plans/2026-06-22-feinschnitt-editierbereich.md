# Feinschnitt-Editierbereich (Transkript als Steuerflaeche) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Den Feinschnitt zu einem transkript-getriebenen Ein-Bildschirm-Editor machen: im durchgehenden Transkript markieren=loeschen, klicken=Schnitt->neue Szene, Text ersetzen=automatisch neue Stimme+Lippensync+Glaettung — frame-genau auf dem Rough-Cut, in der Vorschau hoer- und sichtbar, mit immer-an-Compliance.

**Architecture:** Editiert direkt die durchgehende Rough-Cut-Timeline (Szenen = Marker), nutzt vorhandene frame-genaue Ops (delete_words, split_clip/split_scene, map_asset_range_to_seq) + VO/Lipsync/Transition-Engines. Neu: ripple-bewusster Szenen-Abgleich, Komposit-Schnitt-Endpoint, Web-Audio-Vorschau-Mix, VO->Lipsync-Hook, Compliance-Defaults.

**Tech Stack:** Python 3.11 (uv, FastAPI, SQLite, pytest), TypeScript/React/Electron (Tailwind, vitest), ffmpeg, Web Audio API, Windows SAPI TTS (Default), optionale Sidecars (Lipsync/VLM).

**Spec:** docs/superpowers/specs/2026-06-22-feinschnitt-editierbereich-design.md (e2d93de)

## Global Constraints

- EIN Bildschirm, simpelst, novizen-/agenten-bedienbar; keine Tool-Auswahl ausser der Stimme.
- Laura-Invarianten: Ganzzahl-Frames relativ zur Sequence (nie Float-Sekunden als Zustand); Ranges end-exclusive (`out_frame_exclusive`); Audio/Alignment in SAMPLES (Frames = UI-Projektion); OTIO = source of truth; Idempotenz ueber semantische Identitaet; schwere Modelle (neural TTS / Lipsync-Sidecar / VLM) bleiben OPTIONALE Extras, Default-Pfad laeuft ohne (SAPI-TTS, Stub-Lipsync, Heuristik-Uebergaenge).
- Implementer committen NUR ihre Task-Dateien via EXPLIZITEM `git add <paths>` (NIE `-A` / `-am`).
- codex-AI-runtime-Subtree NICHT anfassen: `services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py`.
- Tests: Backend/Zeitkern = reine Funktionen via pytest (`uv run --no-sync pytest`); Frontend = vitest; reine UI-Pfade als "manuell zu pruefen (live CDP 9222)" markieren.
- Jede Phase endet mit einem gruenen Verifikations-Task.

## Datei-Struktur & geteilter Interface-Kontrakt

**Backend (Python, services/local-api/src/laura):**
- `scenes/reconcile.py` (NEU): `reconcile_after_delete(bounds, del_seq_in, del_seq_out_excl) -> bounds` (rein, ripple-bewusst).
- `POST /timelines/{id}/cut-at-frame` (`CutAtFrameRequest{at_seq_frame}`) -> `{clips, scenes}` (split_clip + split_scene). [Phase A]
- `delete_words`-Pfad ruft `reconcile_after_delete` + gibt aktualisierte Szenen zurueck. [Phase A]
- `handle_voiceover`-Erfolg: bei Face-Probe-Treffer + gueltigem Consent `ai.lipsync` enqueuen (Audio=VO-Asset, Span=VO-Span), sonst still skippen. [Phase C]
- `audit.record(...)` bei Erfolg von voiceover/lipsync/reenact + Export-Render. [Phase D]
- Reel-Render: Disclosure-Praesenz erzwungen (leer -> Default "KI · synthetisch"). [Phase D]

**Frontend (TS, apps/desktop/src):**
- `api.ts`: `cutAtFrame(timelineId, atSeqFrame): Promise<{clips, scenes}>`; Reuse createVoiceover/deleteWords/listTimelineAudioClips/listVoiceoverVoices/lipsync.
- `hooks/useRoughCutTranscript.ts` (NEU): `{ words, scenes, selection, deleteRange, cutAt, replaceSpanText }`.
- `components/ContinuousTranscript.tsx` (NEU): Szenen-gruppierte Woerter + Schnitt-Marker + Drag/Shift-Auswahl + Caret-Schnitt.
- `components/EditorialToolsBar.tsx` (NEU): Streifen unter dem Player — Stimmenwahl, Glaetten, Reenact (manuell), immer-an Synthetik-Hinweis.
- `SequencePlayer` NEUER Prop `audioClips: TimelineAudioClip[]` + interner AudioMixer (Web Audio, playhead-synchron, Ducking). [Phase B]

---


---

## Phase A — Durchgehendes Transkript-Editing auf dem Rough-Cut

Phase A makes the Feinschnitt edit the **continuous rough-cut timeline** directly. Backend gets the pure `reconcile_after_delete`, a composite `cut-at-frame` endpoint, and a `delete_words` path that reconciles+returns scenes. Frontend gets a scene-grouping helper, the `useRoughCutTranscript` hook, and the `ContinuousTranscript` component, then rewires `FineCutView`. Every implementer commits **only their own task files** via explicit `git add <paths>` (never `-A`); the codex AI-runtime subtree (`services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py`) is never touched.

---

### Task A1: Pure `reconcile_after_delete` in `scenes/reconcile.py`

**Files:**
- Create `services/local-api/src/laura/scenes/reconcile.py`
- Create `services/local-api/tests/test_scene_reconcile.py`

**Interfaces:**
- Produces: `reconcile_after_delete(bounds: list[tuple[int, int]], del_seq_in: int, del_seq_out_excl: int) -> list[tuple[int, int]]` — pure; shifts every bound past the ripple-deleted span `[del_seq_in, del_seq_out_excl)` left by the deleted length, clamps bounds that fall **inside** the span to `del_seq_in`, drops resulting zero-length scenes, preserves order and contiguity.
- Consumes: nothing (no DB, no imports beyond stdlib).

Mirrors the same end-exclusive ripple math `delete_range` ([operations.py:201](services/local-api/src/laura/editing/operations.py)) applies to clips, so scene markers track clip geometry exactly.

- [ ] **Step 1: Write the failing test.** Create `services/local-api/tests/test_scene_reconcile.py`:
  ```python
  """reconcile_after_delete: ripple-track scene bounds after a delete_range (invariant #2)."""
  from __future__ import annotations

  from laura.scenes.reconcile import reconcile_after_delete


  def test_delete_before_boundary_shifts_later_bounds_left() -> None:
      # Scenes [0,100) [100,250); delete [10,40) (len 30) entirely inside scene 1.
      out = reconcile_after_delete([(0, 100), (100, 250)], 10, 40)
      assert out == [(0, 70), (70, 220)]


  def test_delete_spanning_a_boundary_keeps_the_boundary() -> None:
      # Delete [80,140) straddles the 100 boundary; boundary survives, collapsed to del_seq_in.
      out = reconcile_after_delete([(0, 100), (100, 250)], 80, 140)
      assert out == [(0, 80), (80, 190)]


  def test_scene_fully_inside_deleted_span_is_dropped() -> None:
      # Scene 2 = [100,140) lies wholly inside delete [90,200) -> zero-length -> dropped.
      out = reconcile_after_delete([(0, 100), (100, 140), (140, 300)], 90, 200)
      assert out == [(0, 90), (90, 190)]


  def test_delete_at_tail_shrinks_last_scene() -> None:
      out = reconcile_after_delete([(0, 100), (100, 250)], 240, 250)
      assert out == [(0, 100), (100, 240)]


  def test_empty_bounds_returns_empty() -> None:
      assert reconcile_after_delete([], 0, 10) == []


  def test_output_is_contiguous_and_ordered() -> None:
      out = reconcile_after_delete([(0, 50), (50, 60), (60, 200)], 45, 120)
      # span [45,120) len 75; scene2 [50,60) inside -> drop; bounds clamp+shift.
      assert out == [(0, 45), (45, 125)]
      for (a_in, a_out), (b_in, b_out) in zip(out, out[1:], strict=True):
          assert a_out == b_in  # contiguous
          assert a_in < a_out   # no zero-length survives
  ```
- [ ] **Step 2: Run it — expect FAIL** (module does not exist):
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_scene_reconcile.py -q
  ```
  Expected: `ModuleNotFoundError: No module named 'laura.scenes.reconcile'`.
- [ ] **Step 3: Minimal implementation.** Create `services/local-api/src/laura/scenes/reconcile.py`:
  ```python
  """Ripple-track scene markers after a delete_range on the rough-cut timeline.

  A scene is a marker pair ``(seq_in, seq_out_exclusive)`` over the continuous rough-cut.
  When a sequence span ``[del_seq_in, del_seq_out_excl)`` is ripple-deleted (the SAME
  geometry ``editing.operations.delete_range`` applies to clips), every marker frame moves
  by the deleted length if it lay past the span, or collapses to ``del_seq_in`` if it lay
  inside it. Pure: integer frames, end-exclusive (invariants #1/#2), no DB.
  """
  from __future__ import annotations


  def _shift_frame(f: int, del_in: int, del_out: int) -> int:
      """Project one sequence frame through a ripple delete of ``[del_in, del_out)``."""
      length = del_out - del_in
      if f <= del_in:
          return f
      if f >= del_out:
          return f - length
      return del_in  # inside the deleted span -> collapses to the cut point


  def reconcile_after_delete(
      bounds: list[tuple[int, int]], del_seq_in: int, del_seq_out_excl: int
  ) -> list[tuple[int, int]]:
      """Return new scene bounds after ripple-deleting ``[del_seq_in, del_seq_out_excl)``.

      Shifts bounds past the span left by its length, collapses bounds inside the span,
      drops scenes that become zero-length, and preserves input order/contiguity.
      """
      if del_seq_out_excl < del_seq_in:
          raise ValueError("del_seq_out_excl must be >= del_seq_in")
      out: list[tuple[int, int]] = []
      for s_in, s_out in bounds:
          n_in = _shift_frame(s_in, del_seq_in, del_seq_out_excl)
          n_out = _shift_frame(s_out, del_seq_in, del_seq_out_excl)
          if n_out > n_in:  # drop zero-length scenes (fully inside the deleted span)
              out.append((n_in, n_out))
      return out
  ```
- [ ] **Step 4: Run it — expect PASS:**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_scene_reconcile.py -q
  ```
  Expected: `6 passed`.
- [ ] **Step 5: Commit.**
  ```
  git add services/local-api/src/laura/scenes/reconcile.py services/local-api/tests/test_scene_reconcile.py
  git commit -m "feat(scenes): pure reconcile_after_delete for ripple-tracking scene markers"
  ```

---

### Task A2: `delete_words` op reconciles + returns scenes

**Files:**
- Modify `services/local-api/src/laura/api/timelines.py` — `delete_words` branch in `_apply` (lines 907–924) and `apply_operation` (lines 929–946)
- Modify `services/local-api/src/laura/api/models.py` — add `scenes` to a new response model (or extend the dispatch to return scenes)
- Create `services/local-api/tests/test_delete_words_reconcile.py`

**Interfaces:**
- Consumes: `reconcile_after_delete` (Task A1); `repos.list_scenes`, `repos.replace_scenes` ([repos.py:1322,1331](services/local-api/src/laura/db/repos.py)); `map_asset_range_to_seq`, `delete_range`.
- Produces: the `delete_words` op path persists reconciled scene bounds and its response includes the updated scenes. New response model `TimelineWithScenesOut{...TimelineOut fields, scenes: list[SceneOut]}`; `apply_operation` returns it for `op == "delete_words"` (other ops keep returning `TimelineOut` shape, which is a structural subset).

Fixes the documented staleness: today `delete_words` ([timelines.py:907](services/local-api/src/laura/api/timelines.py)) ripple-deletes clips but leaves scene rows untouched.

- [ ] **Step 1: Write the failing test.** Create `services/local-api/tests/test_delete_words_reconcile.py`. Use the existing scenes-API fixture pattern (build a project + asset + analysis run + rough-cut with scenes, exactly as `tests/test_scenes_api.py` does — reuse its helper if exported, else inline the same setup), then:
  ```python
  def test_delete_words_reconciles_scene_bounds_and_returns_scenes(client_with_scenes) -> None:
      api, timeline_id, words = client_with_scenes  # words: ordered list of {id,start_frame,...}
      before = api.get(f"/timelines/{timeline_id}/scenes").json()
      assert len(before) >= 2
      # Delete a contiguous early word span fully inside scene 1.
      w0, w1 = words[1]["id"], words[2]["id"]
      resp = api.post(
          f"/timelines/{timeline_id}/operations",
          json={"op": "delete_words", "word_start_id": w0, "word_end_id": w1},
      )
      assert resp.status_code == 200
      body = resp.json()
      assert "scenes" in body  # delete_words now returns updated scenes
      after = body["scenes"]
      # Same scene count (a within-scene delete keeps all boundaries), later bounds shifted left.
      assert len(after) == len(before)
      total_before = before[-1]["seq_out_frame_exclusive"]
      total_after = after[-1]["seq_out_frame_exclusive"]
      assert total_after < total_before  # ripple shortened the sequence
      # Persisted: a re-GET matches the reconciled bounds.
      reget = api.get(f"/timelines/{timeline_id}/scenes").json()
      assert [(s["seq_in_frame"], s["seq_out_frame_exclusive"]) for s in reget] == \
             [(s["seq_in_frame"], s["seq_out_frame_exclusive"]) for s in after]
  ```
- [ ] **Step 2: Run it — expect FAIL:**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_delete_words_reconcile.py -q
  ```
  Expected: `KeyError: 'scenes'` / assertion failure (response has no `scenes`; bounds unchanged on re-GET).
- [ ] **Step 3: Implement.** In `services/local-api/src/laura/api/models.py`, add after `TimelineOut` (line 297):
  ```python
  class TimelineWithScenesOut(TimelineOut):
      """A timeline response that also carries the scene markers reconciled by the same edit
      (used by delete_words / cut-at-frame so the UI updates clips and markers in one round-trip)."""
      scenes: list[SceneOut] = Field(default_factory=list)
  ```
  In `services/local-api/src/laura/api/timelines.py`, add to imports (line 56 block): `TimelineWithScenesOut`, `SceneOut`; and `from ..scenes.reconcile import reconcile_after_delete`. Replace the `delete_words` branch (lines 907–924) so it returns the span alongside the clips — change `_apply` to return `tuple[list[EditClip], tuple[int, int] | None]` is invasive; instead keep `_apply` returning clips and reconcile in `apply_operation`. Concretely, capture the deleted span by computing it again in `apply_operation` only for `delete_words`. Add a tiny helper next to `_apply`:
  ```python
  def _delete_words_span(db: Database, current: list[EditClip], body: OperationRequest) -> tuple[int, int]:
      w0 = repos.get_word(db, _require(body.word_start_id, "word_start_id required"))
      w1 = repos.get_word(db, _require(body.word_end_id, "word_end_id required"))
      if w0 is None or w1 is None:
          raise HTTPException(status.HTTP_404_NOT_FOUND, "word not found")
      if w0["asset_id"] != w1["asset_id"]:
          raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "words must be from the same asset")
      lo, hi = min(w0["start_frame"], w1["start_frame"]), max(w0["end_frame"], w1["end_frame"])
      span = map_asset_range_to_seq(current, asset_id=w0["asset_id"], src_lo=lo, src_hi=hi)
      if span is None:
          raise HTTPException(
              status.HTTP_422_UNPROCESSABLE_CONTENT, "selected words are not present in this timeline"
          )
      return span
  ```
  Refactor the `delete_words` branch in `_apply` to call it:
  ```python
      if op == "delete_words":
          span = _delete_words_span(db, current, body)
          return delete_range(current, span[0], span[1])
  ```
  Then in `apply_operation` (after `repos.replace_timeline_clips(...)` at line 939, before returning), branch on the op:
  ```python
      if body.op == "delete_words":
          span = _delete_words_span(db, current, body)
          old = [(s["seq_in_frame"], s["seq_out_frame_exclusive"]) for s in repos.list_scenes(db, timeline_id)]
          new_bounds = reconcile_after_delete(old, span[0], span[1])
          repos.replace_scenes(db, row["project_id"], timeline_id, new_bounds)
          repos.update_timeline_otio(db, timeline_id, serialize_timeline_otio(db, fresh))
          base = _timeline_out(db, fresh)
          return TimelineWithScenesOut(
              **base.model_dump(),
              scenes=[SceneOut(**s) for s in repos.list_scenes(db, timeline_id)],
          )
  ```
  (Keep the existing `update_timeline_otio` + `return _timeline_out(...)` as the fall-through for all other ops; set the function return annotation to `TimelineOut` since `TimelineWithScenesOut` is a subclass.) Computing the span twice is cheap and avoids threading a tuple through `_apply`'s broad dispatch.
- [ ] **Step 4: Run it — expect PASS:**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_delete_words_reconcile.py -q
  ```
  Expected: `1 passed`.
- [ ] **Step 5: Commit.**
  ```
  git add services/local-api/src/laura/api/timelines.py services/local-api/src/laura/api/models.py services/local-api/tests/test_delete_words_reconcile.py
  git commit -m "feat(timelines): delete_words reconciles + returns scene markers (ripple)"
  ```

---

### Task A3: Composite `POST /timelines/{id}/cut-at-frame` endpoint

**Files:**
- Modify `services/local-api/src/laura/api/scenes.py` — add the `cut_at_frame` route + import
- Modify `services/local-api/src/laura/api/models.py` — add `CutAtFrameRequest`
- Create `services/local-api/tests/test_cut_at_frame_api.py`

**Interfaces:**
- Consumes: `split_clip` ([operations.py:250](services/local-api/src/laura/editing/operations.py)), `EditClip`, `repos.list_timeline_clips`, `repos.replace_timeline_clips`, `repos.list_scenes`, `repos.replace_scenes`, the existing `split_scene` logic ([scenes.py:85](services/local-api/src/laura/api/scenes.py)).
- Produces: `POST /timelines/{timeline_id}/cut-at-frame`, body `CutAtFrameRequest{at_seq_frame: int}` → `{clips: list[ClipOut], scenes: list[SceneOut]}`. Splits the clip at the frame **only when the frame is mid-clip** (already a clip boundary = skip the clip split), then splits the scene that strictly contains the frame at that boundary. Idempotent: cutting at an existing clip+scene boundary is a no-op.

- [ ] **Step 1: Write the failing test.** Create `services/local-api/tests/test_cut_at_frame_api.py` (reuse the scenes-API fixture as in Task A2):
  ```python
  def test_cut_at_frame_splits_clip_then_scene(client_with_scenes) -> None:
      api, timeline_id, _words = client_with_scenes
      scenes = api.get(f"/timelines/{timeline_id}/scenes").json()
      clips = api.get(f"/timelines/{timeline_id}").json()["clips"]
      # Pick a frame strictly inside the first scene AND strictly inside some clip.
      s0 = scenes[0]
      mid = (s0["seq_in_frame"] + s0["seq_out_frame_exclusive"]) // 2
      resp = api.post(f"/timelines/{timeline_id}/cut-at-frame", json={"at_seq_frame": mid})
      assert resp.status_code == 200
      body = resp.json()
      assert len(body["clips"]) == len(clips) + 1            # clip got split
      assert any(c["seq_in_frame"] == mid for c in body["clips"])  # boundary exists at the frame
      assert len(body["scenes"]) == len(scenes) + 1          # scene got split there
      assert any(s["seq_in_frame"] == mid for s in body["scenes"])


  def test_cut_at_existing_clip_boundary_skips_clip_split(client_with_scenes) -> None:
      api, timeline_id, _words = client_with_scenes
      clips = api.get(f"/timelines/{timeline_id}").json()["clips"]
      # A frame that is already a clip boundary but mid-scene (use clip[1]'s seq_in if it is mid-scene).
      boundary = clips[1]["seq_in_frame"]
      scenes = api.get(f"/timelines/{timeline_id}/scenes").json()
      resp = api.post(f"/timelines/{timeline_id}/cut-at-frame", json={"at_seq_frame": boundary})
      assert resp.status_code == 200
      body = resp.json()
      assert len(body["clips"]) == len(clips)   # no clip split (already a boundary)
      assert any(s["seq_in_frame"] == boundary for s in body["scenes"])


  def test_cut_at_frame_at_sequence_edge_is_rejected(client_with_scenes) -> None:
      api, timeline_id, _words = client_with_scenes
      resp = api.post(f"/timelines/{timeline_id}/cut-at-frame", json={"at_seq_frame": 0})
      assert resp.status_code == 422
  ```
- [ ] **Step 2: Run it — expect FAIL:**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_cut_at_frame_api.py -q
  ```
  Expected: `404 Not Found` (route absent) → assertion failures.
- [ ] **Step 3: Implement.** In `services/local-api/src/laura/api/models.py`, add near `SplitSceneRequest` (line 689):
  ```python
  class CutAtFrameRequest(BaseModel):
      at_seq_frame: int = Field(ge=0)
  ```
  In `services/local-api/src/laura/api/scenes.py`, extend imports: `from ..editing.operations import EditClip, ordered, split_clip`, `from ..editing.otio_sync import serialize_timeline_otio`, `from ..scenes.reconcile import reconcile_after_delete` is **not** needed here; add `ClipOut`, `CutAtFrameRequest` to the `.models` import. Add the route:
  ```python
  @router.post("/timelines/{timeline_id}/cut-at-frame")
  def cut_at_frame(
      timeline_id: str, body: CutAtFrameRequest, request: Request
  ) -> dict[str, Any]:
      """Composite cut: split the clip at ``at_seq_frame`` if mid-clip, then split the scene
      containing that frame at the resulting boundary. Guarantees a valid clip boundary so the
      scene split never lands off a cut. Idempotent at an existing clip+scene boundary."""
      db = _db(request)
      tl = repos.get_timeline(db, timeline_id)
      if tl is None:
          raise HTTPException(status.HTTP_404_NOT_FOUND, "timeline not found")
      at = body.at_seq_frame
      clips = [EditClip.from_row(c) for c in repos.list_timeline_clips(db, timeline_id)]
      boundaries = {c.seq_in_frame for c in clips} | {c.seq_out_frame_exclusive for c in clips}
      if at not in boundaries:
          # Mid-clip: split so the cut becomes a real clip boundary first (invariant: integer frames).
          try:
              clips = split_clip(clips, at)
          except ValueError as exc:
              raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
          repos.replace_timeline_clips(db, timeline_id, [c.to_row() for c in ordered(clips)])
          fresh = repos.get_timeline(db, timeline_id)
          assert fresh is not None
          repos.update_timeline_otio(db, timeline_id, serialize_timeline_otio(db, fresh))
      # Split the scene that strictly contains `at` (end-exclusive); a frame on a scene edge is a no-op.
      scene = next(
          (s for s in repos.list_scenes(db, timeline_id)
           if s["seq_in_frame"] < at < s["seq_out_frame_exclusive"]),
          None,
      )
      if scene is None:
          raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "cut point is not inside a scene")
      ranges: list[tuple[int, int]] = []
      for s in repos.list_scenes(db, timeline_id):
          if s["id"] == scene["id"]:
              ranges.append((s["seq_in_frame"], at))
              ranges.append((at, s["seq_out_frame_exclusive"]))
          else:
              ranges.append((s["seq_in_frame"], s["seq_out_frame_exclusive"]))
      repos.replace_scenes(db, tl["project_id"], timeline_id, ranges)
      return {
          "clips": [ClipOut(**c).model_dump() for c in repos.list_timeline_clips(db, timeline_id)],
          "scenes": [SceneOut(**s).model_dump() for s in repos.list_scenes(db, timeline_id)],
      }
  ```
- [ ] **Step 4: Run it — expect PASS:**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_cut_at_frame_api.py -q
  ```
  Expected: `3 passed`.
- [ ] **Step 5: Commit.**
  ```
  git add services/local-api/src/laura/api/scenes.py services/local-api/src/laura/api/models.py services/local-api/tests/test_cut_at_frame_api.py
  git commit -m "feat(scenes): composite cut-at-frame endpoint (split_clip + split_scene)"
  ```

---

### Task A4: `api.ts` — `cutAtFrame` client method

**Files:**
- Modify `apps/desktop/src/api.ts` — add `cutAtFrame` near `splitScene` (line 1101) and the `deleteWords` method (line 1137)
- Modify `apps/desktop/src/api.test.ts`

**Interfaces:**
- Produces: `cutAtFrame(timelineId: string, atSeqFrame: number): Promise<{clips: TimelineClip[]; scenes: Scene[]}>`.
- Consumes: existing `request<T>`, `TimelineClip`, `Scene`. Note: `deleteWords` already returns `Promise<Timeline>`; Phase A leaves its signature unchanged — the hook re-reads scenes after a delete via `listScenes`, since `deleteWords`'s server response now carries `scenes` but the client type stays `Timeline` (structural superset; the hook reads `.clips` and reloads scenes).

- [ ] **Step 1: Write the failing test.** In `apps/desktop/src/api.test.ts`, add:
  ```ts
  it("cutAtFrame posts at_seq_frame and returns clips + scenes", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => ({ clips: [], scenes: [] }),
    });
    vi.stubGlobal("fetch", fetchMock);
    const client = new LauraClient("http://x", "tok");
    const out = await client.cutAtFrame("tl1", 42);
    expect(out).toEqual({ clips: [], scenes: [] });
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("http://x/timelines/tl1/cut-at-frame");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ at_seq_frame: 42 });
  });
  ```
  (Match the existing mocking style in `api.test.ts`; if it uses a shared `makeClient`/`mockFetch` helper, reuse that instead of `stubGlobal`.)
- [ ] **Step 2: Run it — expect FAIL:**
  ```
  cd apps/desktop && npx vitest run src/api.test.ts -t cutAtFrame
  ```
  Expected: `TypeError: client.cutAtFrame is not a function`.
- [ ] **Step 3: Implement.** In `apps/desktop/src/api.ts`, add after `splitScene` (line 1106):
  ```ts
  /** Composite cut on the rough-cut: split the clip at the frame (if mid-clip) then the scene
   *  there. Returns the updated clips and scene markers in one round-trip. */
  cutAtFrame(
    timelineId: string,
    atSeqFrame: number,
  ): Promise<{ clips: TimelineClip[]; scenes: Scene[] }> {
    return this.request<{ clips: TimelineClip[]; scenes: Scene[] }>(
      `/timelines/${timelineId}/cut-at-frame`,
      { method: "POST", body: JSON.stringify({ at_seq_frame: atSeqFrame }) },
    );
  }
  ```
- [ ] **Step 4: Run it — expect PASS:**
  ```
  cd apps/desktop && npx vitest run src/api.test.ts -t cutAtFrame
  ```
  Expected: `1 passed`.
- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/api.ts apps/desktop/src/api.test.ts
  git commit -m "feat(api): cutAtFrame client method for composite rough-cut cut"
  ```

---

### Task A5: Scene-grouping helper `groupCutWordsByScene` (pure TS)

**Files:**
- Create `apps/desktop/src/shared/sceneTranscript.ts`
- Create `apps/desktop/src/shared/sceneTranscript.test.ts`

**Interfaces:**
- Consumes: `CutWord` ([transcriptProjection.ts:11](apps/desktop/src/shared/transcriptProjection.ts)), `Scene` ([api.ts:123](apps/desktop/src/api.ts)).
- Produces:
  ```ts
  export interface SceneGroup { scene: Scene; words: CutWord[] }
  export function groupCutWordsByScene(words: CutWord[], scenes: Scene[]): SceneGroup[]
  ```
  Assigns each projected word to the scene whose `[seq_in_frame, seq_out_frame_exclusive)` contains its `seqStart` (end-exclusive, invariant #2). Words past the last scene attach to the last scene; scenes keep input order; a word matches at most one scene.

This is the §4.1 "group by `[seq_in, seq_out_exclusive)` and render with scene label + cut marker" pure step, isolated for unit testing.

- [ ] **Step 1: Write the failing test.** Create `apps/desktop/src/shared/sceneTranscript.test.ts`:
  ```ts
  import { describe, expect, it } from "vitest";

  import { groupCutWordsByScene } from "./sceneTranscript";
  import { type CutWord } from "./transcriptProjection";
  import { type Scene } from "../api";

  function scene(id: string, inF: number, outF: number, order: number): Scene {
    return {
      id, project_id: "p", source_timeline_id: "t", name: id,
      order_index: order, seq_in_frame: inF, seq_out_frame_exclusive: outF,
    };
  }
  function word(id: string, seqStart: number): CutWord {
    return { id, text: id, srcFrame: seqStart, srcEndFrame: seqStart + 1, seqStart, seqEnd: seqStart + 1 };
  }

  describe("groupCutWordsByScene", () => {
    it("assigns words to the scene whose end-exclusive range contains seqStart", () => {
      const scenes = [scene("s1", 0, 100, 0), scene("s2", 100, 200, 1)];
      const words = [word("a", 0), word("b", 99), word("c", 100), word("d", 150)];
      const groups = groupCutWordsByScene(words, scenes);
      expect(groups.map((g) => g.scene.id)).toEqual(["s1", "s2"]);
      expect(groups[0].words.map((w) => w.id)).toEqual(["a", "b"]); // 99 < 100 stays in s1
      expect(groups[1].words.map((w) => w.id)).toEqual(["c", "d"]); // 100 is s2's in-frame
    });

    it("attaches words past the last scene to the last scene", () => {
      const scenes = [scene("s1", 0, 100, 0)];
      const groups = groupCutWordsByScene([word("a", 250)], scenes);
      expect(groups[0].words.map((w) => w.id)).toEqual(["a"]);
    });

    it("returns one empty group per scene when there are no words", () => {
      const scenes = [scene("s1", 0, 100, 0), scene("s2", 100, 200, 1)];
      const groups = groupCutWordsByScene([], scenes);
      expect(groups.map((g) => g.words.length)).toEqual([0, 0]);
    });

    it("returns no groups when there are no scenes", () => {
      expect(groupCutWordsByScene([word("a", 0)], [])).toEqual([]);
    });
  });
  ```
- [ ] **Step 2: Run it — expect FAIL:**
  ```
  cd apps/desktop && npx vitest run src/shared/sceneTranscript.test.ts
  ```
  Expected: `Failed to resolve import "./sceneTranscript"`.
- [ ] **Step 3: Implement.** Create `apps/desktop/src/shared/sceneTranscript.ts`:
  ```ts
  /**
   * Pure helper: group projected cut-words into the continuous transcript's scene sections.
   *
   * Each word is assigned to the scene whose [seq_in_frame, seq_out_frame_exclusive) contains its
   * seqStart (end-exclusive, invariant #2). Words past the last scene attach to the last scene so
   * nothing is dropped. Scenes keep their input order; every scene yields a group (possibly empty)
   * so the UI can render its label + cut marker even before the first word lands.
   */
  import { type Scene } from "../api";
  import { type CutWord } from "./transcriptProjection";

  export interface SceneGroup {
    scene: Scene;
    words: CutWord[];
  }

  export function groupCutWordsByScene(words: CutWord[], scenes: Scene[]): SceneGroup[] {
    if (scenes.length === 0) return [];
    const groups: SceneGroup[] = scenes.map((scene) => ({ scene, words: [] }));
    for (const w of words) {
      let idx = groups.findIndex(
        (g) => w.seqStart >= g.scene.seq_in_frame && w.seqStart < g.scene.seq_out_frame_exclusive,
      );
      if (idx === -1) idx = groups.length - 1; // past the last scene -> attach to it
      groups[idx].words.push(w);
    }
    return groups;
  }
  ```
- [ ] **Step 4: Run it — expect PASS:**
  ```
  cd apps/desktop && npx vitest run src/shared/sceneTranscript.test.ts
  ```
  Expected: `4 passed`.
- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/shared/sceneTranscript.ts apps/desktop/src/shared/sceneTranscript.test.ts
  git commit -m "feat(transcript): pure groupCutWordsByScene for continuous scene sections"
  ```

---

### Task A6: `useRoughCutTranscript` hook

**Files:**
- Create `apps/desktop/src/hooks/useRoughCutTranscript.ts`
- Create `apps/desktop/src/hooks/useRoughCutTranscript.test.ts`

**Interfaces:**
- Consumes: `LauraClient` (`getTimeline`, `listScenes`, `deleteWords`, `cutAtFrame`, and — placeholder for Phase C — `createVoiceover`), `projectCutWords`, `Segment`, `Scene`, `Word`.
- Produces:
  ```ts
  export interface RoughCutTranscriptController {
    words: CutWord[];
    scenes: Scene[];
    clips: TimelineClip[];
    selection: { startWordId: string; endWordId: string } | null;
    setSelection: (sel: { startWordId: string; endWordId: string } | null) => void;
    deleteRange: (startWordId: string, endWordId: string) => Promise<void>;
    cutAt: (seqFrame: number) => Promise<void>;
    replaceSpanText: (
      startWordId: string, endWordId: string, newText: string, voiceId: string,
    ) => Promise<void>;
    error: string | null;
    reload: () => Promise<void>;
  }
  export function useRoughCutTranscript(
    client: LauraClient | null, roughCutId: string | null, segments: Segment[],
  ): RoughCutTranscriptController
  ```
  Loads the rough-cut timeline (`getTimeline`) + its scenes (`listScenes`), projects words via `projectCutWords(segments, clips)`. `deleteRange` calls `client.deleteWords` then reloads clips+scenes (Phase A backend already reconciled them server-side). `cutAt` calls `client.cutAtFrame` and applies the returned `{clips, scenes}` directly. `replaceSpanText` is a **Phase A stub** that records intent and reloads — the real auto-VO pipeline lands in Phase C; here it must compile, be typed, and is verified by a "no-op resolves" test only.

- [ ] **Step 1: Write the failing test.** Create `apps/desktop/src/hooks/useRoughCutTranscript.test.ts` (mirror `useSceneTimeline.test.ts` / `useScenes.test.ts` style with `@testing-library/react` `renderHook` + `act`):
  ```ts
  import { act, renderHook, waitFor } from "@testing-library/react";
  import { describe, expect, it, vi } from "vitest";

  import { useRoughCutTranscript } from "./useRoughCutTranscript";
  import { type LauraClient } from "../api";

  const clip = {
    id: "c1", asset_id: "a1", src_in_frame: 0, src_out_frame_exclusive: 100,
    seq_in_frame: 0, seq_out_frame_exclusive: 100, lane: 0,
    speaker_id: null, origin_word_start_id: null, origin_word_end_id: null,
  };
  const scenes = [
    { id: "s1", project_id: "p", source_timeline_id: "t", name: "s1",
      order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 100 },
  ];
  const segments = [
    { id: "seg1", asset_id: "a1", speaker_id: null, start_frame: 0, end_frame: 100,
      text: "hi there", words: [
        { id: "w1", idx: 0, start_frame: 0, end_frame: 10, text: "hi", is_punctuation: false },
        { id: "w2", idx: 1, start_frame: 20, end_frame: 30, text: "there", is_punctuation: false },
      ] },
  ];

  function makeClient(overrides: Partial<LauraClient> = {}): LauraClient {
    return {
      getTimeline: vi.fn().mockResolvedValue({ id: "t", clips: [clip] }),
      listScenes: vi.fn().mockResolvedValue(scenes),
      deleteWords: vi.fn().mockResolvedValue({ id: "t", clips: [clip] }),
      cutAtFrame: vi.fn().mockResolvedValue({ clips: [clip], scenes }),
      ...overrides,
    } as unknown as LauraClient;
  }

  describe("useRoughCutTranscript", () => {
    it("loads rough-cut clips, scenes, and projects words", async () => {
      const client = makeClient();
      const { result } = renderHook(() =>
        useRoughCutTranscript(client, "t", segments as never));
      await waitFor(() => expect(result.current.words.length).toBe(2));
      expect(result.current.scenes).toHaveLength(1);
      expect(result.current.words.map((w) => w.id)).toEqual(["w1", "w2"]);
    });

    it("deleteRange calls deleteWords then reloads scenes", async () => {
      const client = makeClient();
      const { result } = renderHook(() =>
        useRoughCutTranscript(client, "t", segments as never));
      await waitFor(() => expect(result.current.words.length).toBe(2));
      await act(async () => { await result.current.deleteRange("w1", "w1"); });
      expect(client.deleteWords).toHaveBeenCalledWith("t", "w1", "w1");
      expect(client.listScenes).toHaveBeenCalled();
    });

    it("cutAt applies the returned clips and scenes", async () => {
      const client = makeClient();
      const { result } = renderHook(() =>
        useRoughCutTranscript(client, "t", segments as never));
      await waitFor(() => expect(result.current.scenes.length).toBe(1));
      await act(async () => { await result.current.cutAt(50); });
      expect(client.cutAtFrame).toHaveBeenCalledWith("t", 50);
    });
  });
  ```
- [ ] **Step 2: Run it — expect FAIL:**
  ```
  cd apps/desktop && npx vitest run src/hooks/useRoughCutTranscript.test.ts
  ```
  Expected: `Failed to resolve import "./useRoughCutTranscript"`.
- [ ] **Step 3: Implement.** Create `apps/desktop/src/hooks/useRoughCutTranscript.ts`:
  ```ts
  import { useCallback, useEffect, useMemo, useState } from "react";

  import { type LauraClient, type Scene, type Segment, type TimelineClip } from "../api";
  import { type CutWord, projectCutWords } from "../shared/transcriptProjection";

  export interface RoughCutTranscriptController {
    words: CutWord[];
    scenes: Scene[];
    clips: TimelineClip[];
    selection: { startWordId: string; endWordId: string } | null;
    setSelection: (sel: { startWordId: string; endWordId: string } | null) => void;
    deleteRange: (startWordId: string, endWordId: string) => Promise<void>;
    cutAt: (seqFrame: number) => Promise<void>;
    replaceSpanText: (
      startWordId: string,
      endWordId: string,
      newText: string,
      voiceId: string,
    ) => Promise<void>;
    error: string | null;
    reload: () => Promise<void>;
  }

  /**
   * Drive the Feinschnitt against the CONTINUOUS rough-cut timeline (not isolated scene copies).
   * Loads the rough-cut clips + its scene markers and projects every surviving transcript word onto
   * continuous sequence frames. Edits run on the rough-cut directly: deleteRange ripple-deletes words
   * (the backend reconciles scene markers, §4.4), cutAt is the composite split_clip+split_scene.
   *
   * replaceSpanText is a Phase A seam: it records intent and reloads. The auto-VO/lipsync pipeline
   * (§5) is wired in Phase C — voiceId is plumbed through now so the signature is stable.
   */
  export function useRoughCutTranscript(
    client: LauraClient | null,
    roughCutId: string | null,
    segments: Segment[],
  ): RoughCutTranscriptController {
    const [clips, setClips] = useState<TimelineClip[]>([]);
    const [scenes, setScenes] = useState<Scene[]>([]);
    const [selection, setSelection] = useState<
      { startWordId: string; endWordId: string } | null
    >(null);
    const [error, setError] = useState<string | null>(null);

    const reload = useCallback(async () => {
      if (!client || !roughCutId) {
        setClips([]);
        setScenes([]);
        return;
      }
      try {
        setError(null);
        const [tl, sc] = await Promise.all([
          client.getTimeline(roughCutId),
          client.listScenes(roughCutId),
        ]);
        setClips(tl.clips);
        setScenes(sc);
      } catch (e) {
        setError(String(e));
      }
    }, [client, roughCutId]);

    useEffect(() => {
      void reload();
    }, [reload]);

    const words = useMemo(() => projectCutWords(segments, clips), [segments, clips]);

    const deleteRange = useCallback(
      async (startWordId: string, endWordId: string) => {
        if (!client || !roughCutId) return;
        try {
          await client.deleteWords(roughCutId, startWordId, endWordId);
          setSelection(null);
          await reload(); // backend already reconciled scene markers; re-read clips + scenes
        } catch (e) {
          setError(String(e));
        }
      },
      [client, roughCutId, reload],
    );

    const cutAt = useCallback(
      async (seqFrame: number) => {
        if (!client || !roughCutId) return;
        try {
          const out = await client.cutAtFrame(roughCutId, seqFrame);
          setClips(out.clips);
          setScenes(out.scenes);
        } catch (e) {
          setError(String(e));
        }
      },
      [client, roughCutId],
    );

    const replaceSpanText = useCallback(
      async (
        _startWordId: string,
        _endWordId: string,
        _newText: string,
        _voiceId: string,
      ) => {
        // Phase A seam: the auto-VO/lipsync pipeline (§5) lands in Phase C. Reload to keep the
        // projection fresh; arguments are plumbed so the contract is stable across phases.
        await reload();
      },
      [reload],
    );

    return {
      words,
      scenes,
      clips,
      selection,
      setSelection,
      deleteRange,
      cutAt,
      replaceSpanText,
      error,
      reload,
    };
  }
  ```
- [ ] **Step 4: Run it — expect PASS:**
  ```
  cd apps/desktop && npx vitest run src/hooks/useRoughCutTranscript.test.ts
  ```
  Expected: `3 passed`.
- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/hooks/useRoughCutTranscript.ts apps/desktop/src/hooks/useRoughCutTranscript.test.ts
  git commit -m "feat(hooks): useRoughCutTranscript drives continuous rough-cut editing"
  ```

---

### Task A7: `ContinuousTranscript` component (scene sections + cut markers + selection)

**Files:**
- Create `apps/desktop/src/components/ContinuousTranscript.tsx`
- Create `apps/desktop/src/components/ContinuousTranscript.test.tsx`

**Interfaces:**
- Consumes: `groupCutWordsByScene` (Task A5), `CutWord`, `Scene`.
- Produces:
  ```tsx
  export function ContinuousTranscript({
    words, scenes, selection, onSelectionChange, onDeleteSelection, onCutAt, onSeek,
  }: {
    words: CutWord[];
    scenes: Scene[];
    selection: { startWordId: string; endWordId: string } | null;
    onSelectionChange: (sel: { startWordId: string; endWordId: string } | null) => void;
    onDeleteSelection: (startWordId: string, endWordId: string) => void;
    onCutAt: (seqFrame: number) => void;
    onSeek: (seqFrame: number) => void;
  }): ReactElement
  ```
  Renders scene-labelled sections (with a cut marker `✂` at each scene boundary), words as clickable spans (`onClick` → `onSeek(word.seqStart)`), drag/shift multi-word selection (`onSelectionChange`), a delete affordance for the active selection (`onDeleteSelection`), and a caret between adjacent words whose click calls `onCutAt(rightWord.seqStart)`. Pure presentation — no client/network. Selection logic (drag start→end ordering by `seqStart`) lives here and is unit-tested.

- [ ] **Step 1: Write the failing test.** Create `apps/desktop/src/components/ContinuousTranscript.test.tsx` (mirror `TranscriptBar.test.tsx` with `@testing-library/react` + `userEvent`):
  ```tsx
  import { fireEvent, render, screen } from "@testing-library/react";
  import { describe, expect, it, vi } from "vitest";

  import { ContinuousTranscript } from "./ContinuousTranscript";
  import { type CutWord } from "../shared/transcriptProjection";
  import { type Scene } from "../api";

  const scenes: Scene[] = [
    { id: "s1", project_id: "p", source_timeline_id: "t", name: "Szene 1",
      order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 100 },
    { id: "s2", project_id: "p", source_timeline_id: "t", name: "Szene 2",
      order_index: 1, seq_in_frame: 100, seq_out_frame_exclusive: 200 },
  ];
  const words: CutWord[] = [
    { id: "w1", text: "hallo", srcFrame: 0, srcEndFrame: 10, seqStart: 0, seqEnd: 10 },
    { id: "w2", text: "welt", srcFrame: 20, srcEndFrame: 30, seqStart: 20, seqEnd: 30 },
    { id: "w3", text: "zwei", srcFrame: 100, srcEndFrame: 110, seqStart: 100, seqEnd: 110 },
  ];

  describe("ContinuousTranscript", () => {
    it("renders every scene label and every word", () => {
      render(
        <ContinuousTranscript words={words} scenes={scenes} selection={null}
          onSelectionChange={vi.fn()} onDeleteSelection={vi.fn()}
          onCutAt={vi.fn()} onSeek={vi.fn()} />,
      );
      expect(screen.getByText("Szene 1")).toBeInTheDocument();
      expect(screen.getByText("Szene 2")).toBeInTheDocument();
      expect(screen.getByText("hallo")).toBeInTheDocument();
      expect(screen.getByText("zwei")).toBeInTheDocument();
    });

    it("clicking a word seeks to its seqStart", () => {
      const onSeek = vi.fn();
      render(
        <ContinuousTranscript words={words} scenes={scenes} selection={null}
          onSelectionChange={vi.fn()} onDeleteSelection={vi.fn()}
          onCutAt={vi.fn()} onSeek={onSeek} />,
      );
      fireEvent.click(screen.getByText("welt"));
      expect(onSeek).toHaveBeenCalledWith(20);
    });

    it("drag from one word to another reports an ordered selection", () => {
      const onSelectionChange = vi.fn();
      render(
        <ContinuousTranscript words={words} scenes={scenes} selection={null}
          onSelectionChange={onSelectionChange} onDeleteSelection={vi.fn()}
          onCutAt={vi.fn()} onSeek={vi.fn()} />,
      );
      fireEvent.mouseDown(screen.getByText("welt"));   // start at w2 (seqStart 20)
      fireEvent.mouseEnter(screen.getByText("hallo")); // drag back over w1 (seqStart 0)
      fireEvent.mouseUp(screen.getByText("hallo"));
      // ordered by seqStart: start=w1, end=w2
      expect(onSelectionChange).toHaveBeenLastCalledWith({ startWordId: "w1", endWordId: "w2" });
    });

    it("clicking the caret between two words cuts at the right word's seqStart", () => {
      const onCutAt = vi.fn();
      render(
        <ContinuousTranscript words={words} scenes={scenes} selection={null}
          onSelectionChange={vi.fn()} onDeleteSelection={vi.fn()}
          onCutAt={onCutAt} onSeek={vi.fn()} />,
      );
      // caret before "welt" cuts at 20
      fireEvent.click(screen.getByTestId("caret-w2"));
      expect(onCutAt).toHaveBeenCalledWith(20);
    });

    it("delete button on an active selection calls onDeleteSelection", () => {
      const onDeleteSelection = vi.fn();
      render(
        <ContinuousTranscript words={words} scenes={scenes}
          selection={{ startWordId: "w1", endWordId: "w2" }}
          onSelectionChange={vi.fn()} onDeleteSelection={onDeleteSelection}
          onCutAt={vi.fn()} onSeek={vi.fn()} />,
      );
      fireEvent.click(screen.getByRole("button", { name: /löschen/i }));
      expect(onDeleteSelection).toHaveBeenCalledWith("w1", "w2");
    });
  });
  ```
- [ ] **Step 2: Run it — expect FAIL:**
  ```
  cd apps/desktop && npx vitest run src/components/ContinuousTranscript.test.tsx
  ```
  Expected: `Failed to resolve import "./ContinuousTranscript"`.
- [ ] **Step 3: Implement.** Create `apps/desktop/src/components/ContinuousTranscript.tsx`:
  ```tsx
  import { type ReactElement, useState } from "react";

  import { type Scene } from "../api";
  import { type CutWord } from "../shared/transcriptProjection";
  import { groupCutWordsByScene } from "../shared/sceneTranscript";

  interface Selection {
    startWordId: string;
    endWordId: string;
  }

  /**
   * The continuous rough-cut transcript as the editing surface (spec §3, §4.1).
   *
   * Words are grouped into scene sections (label + ✂ cut marker at each boundary). Three gestures,
   * no tool picker: click a word -> seek; drag / shift over words -> selection (ordered by seqStart)
   * which the parent ripple-deletes; click the caret between two words -> cut at the right word's
   * sequence frame (a new scene starts there). Pure presentation — all effects flow through props.
   */
  export function ContinuousTranscript({
    words,
    scenes,
    selection,
    onSelectionChange,
    onDeleteSelection,
    onCutAt,
    onSeek,
  }: {
    words: CutWord[];
    scenes: Scene[];
    selection: Selection | null;
    onSelectionChange: (sel: Selection | null) => void;
    onDeleteSelection: (startWordId: string, endWordId: string) => void;
    onCutAt: (seqFrame: number) => void;
    onSeek: (seqFrame: number) => void;
  }): ReactElement {
    const groups = groupCutWordsByScene(words, scenes);
    const ordered = words; // already sorted by seqStart from projectCutWords
    const [anchor, setAnchor] = useState<CutWord | null>(null);

    function selectTo(from: CutWord, to: CutWord): void {
      const [a, b] = from.seqStart <= to.seqStart ? [from, to] : [to, from];
      onSelectionChange({ startWordId: a.id, endWordId: b.id });
    }

    const inSelection = (w: CutWord): boolean => {
      if (!selection) return false;
      const s = ordered.find((x) => x.id === selection.startWordId);
      const e = ordered.find((x) => x.id === selection.endWordId);
      if (!s || !e) return false;
      return w.seqStart >= s.seqStart && w.seqStart <= e.seqStart;
    };

    return (
      <div className="flex flex-col gap-2 overflow-auto p-2 text-sm" data-testid="continuous-transcript">
        {selection && (
          <div className="flex items-center gap-2 text-xs text-content-muted">
            <button
              type="button"
              className="rounded bg-status-err/20 px-2 py-0.5 text-status-err hover:bg-status-err/30"
              onClick={() => onDeleteSelection(selection.startWordId, selection.endWordId)}
            >
              Auswahl löschen
            </button>
          </div>
        )}
        {groups.map((g) => (
          <section key={g.scene.id} data-scene-id={g.scene.id}>
            <div className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-content-faint">
              <span aria-hidden>✂</span>
              <span>{g.scene.name}</span>
            </div>
            <p className="leading-7">
              {g.words.map((w) => (
                <span key={w.id} className="whitespace-nowrap">
                  <button
                    type="button"
                    aria-label={`Schnitt vor ${w.text}`}
                    data-testid={`caret-${w.id}`}
                    className="mx-0.5 inline-block w-1 align-middle text-content-faint hover:text-sky-400"
                    onClick={() => onCutAt(w.seqStart)}
                  >
                    |
                  </button>
                  <span
                    role="button"
                    tabIndex={0}
                    className={`cursor-text rounded px-0.5 ${
                      inSelection(w) ? "bg-sky-700 text-white" : "hover:bg-surface-2"
                    }`}
                    onClick={() => onSeek(w.seqStart)}
                    onMouseDown={() => setAnchor(w)}
                    onMouseEnter={() => {
                      if (anchor) selectTo(anchor, w);
                    }}
                    onMouseUp={() => {
                      if (anchor) selectTo(anchor, w);
                      setAnchor(null);
                    }}
                  >
                    {w.text}
                  </span>
                </span>
              ))}
            </p>
          </section>
        ))}
      </div>
    );
  }
  ```
- [ ] **Step 4: Run it — expect PASS:**
  ```
  cd apps/desktop && npx vitest run src/components/ContinuousTranscript.test.tsx
  ```
  Expected: `5 passed`.
- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/ContinuousTranscript.tsx apps/desktop/src/components/ContinuousTranscript.test.tsx
  git commit -m "feat(ui): ContinuousTranscript with scene sections, cut markers, drag-select"
  ```

---

### Task A8: Rewire `FineCutView` onto the rough-cut + scene jump nav

**Files:**
- Modify `apps/desktop/src/components/FineCutView.tsx` (replace the per-scene `useSceneTimeline`/`openScene` editing path with `useRoughCutTranscript` + `ContinuousTranscript`; left list becomes scene **jump** navigation)
- Modify `apps/desktop/src/components/FineCutView.test.tsx`

**Interfaces:**
- Consumes: `useRoughCutTranscript` (A6), `ContinuousTranscript` (A7), `useScenes` (existing, for the jump list — read-only here), `SequencePlayer`, `TimelineBar`, the `roughCutId` prop FineCutView already receives.
- Produces: a FineCutView whose center column edits the continuous rough-cut. The left scene list **navigates** (clicking a scene seeks `onSeek(scene.seq_in_frame)` and scrolls), it no longer isolates/materializes. `SequencePlayer` is fed the rough-cut `roughCutId` + its `clips` directly (no `seqToSrc`/`srcToSeq` round-trip — the transcript is already continuous-seq).

The §4.1 wiring: "no `openScene` materialization in the Feinschnitt edit path." Removes the `seqToSrc`/`srcToSeq` machinery (FineCutView.tsx:84–139) now that words/seek are all continuous sequence frames.

- [ ] **Step 1: Write the failing test.** Update `apps/desktop/src/components/FineCutView.test.tsx`. Keep existing passing assertions where still valid; add:
  ```tsx
  it("loads the rough-cut timeline directly (no openScene) and renders scene jump buttons", async () => {
    const getTimeline = vi.fn().mockResolvedValue({ id: "rc1", clips: [baseClip] });
    const listScenes = vi.fn().mockResolvedValue([sceneA, sceneB]);
    const openScene = vi.fn(); // must NOT be called in the edit path
    const client = makeClient({ getTimeline, listScenes, openScene });
    render(
      <FineCutView client={client} asset={asset} roughCutId="rc1" segments={segments}
        currentFrame={0} seek={null} onSeek={vi.fn()} onFrame={vi.fn()} />,
    );
    await screen.findByText("Szene 1");
    expect(getTimeline).toHaveBeenCalledWith("rc1");
    expect(openScene).not.toHaveBeenCalled();
  });

  it("clicking a scene in the jump list seeks to its seq_in_frame", async () => {
    const onSeek = vi.fn();
    const client = makeClient({
      getTimeline: vi.fn().mockResolvedValue({ id: "rc1", clips: [baseClip] }),
      listScenes: vi.fn().mockResolvedValue([sceneA, sceneB]),
    });
    render(
      <FineCutView client={client} asset={asset} roughCutId="rc1" segments={segments}
        currentFrame={0} seek={null} onSeek={onSeek} onFrame={vi.fn()} />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "Szene 2" }));
    expect(onSeek).toHaveBeenCalledWith(sceneB.seq_in_frame);
  });
  ```
  (Define `baseClip`, `sceneA`, `sceneB`, `asset`, `segments`, and a `makeClient` that stubs `getTimeline`/`listScenes`/`deleteWords`/`cutAtFrame` at the top of the file, matching A6's shapes.)
- [ ] **Step 2: Run it — expect FAIL:**
  ```
  cd apps/desktop && npx vitest run src/components/FineCutView.test.tsx
  ```
  Expected: failures — `openScene` is still called / scene buttons don't seek (current FineCutView uses `useSceneTimeline`).
- [ ] **Step 3: Implement.** Rewrite `apps/desktop/src/components/FineCutView.tsx`. Replace the `useSceneTimeline` + `seqToSrc`/`srcToSeq`/`seekToSeq`/`handleSeqFrame`/`projectCutWords` block (lines 42–142, 209–221) so the center column drives the rough-cut directly:
  ```tsx
  import { type ReactElement, useEffect, useMemo } from "react";

  import { type Asset, type LauraClient, type Segment } from "../api";
  import { useScenes } from "../hooks/useScenes";
  import { useRoughCutTranscript } from "../hooks/useRoughCutTranscript";
  import { ContinuousTranscript } from "./ContinuousTranscript";
  import { SequencePlayer } from "./SequencePlayer";
  import { TimelineBar } from "./TimelineBar";

  /**
   * Feinschnitt: edit the CONTINUOUS rough-cut directly (spec §3/§4.1). Scenes are jump markers,
   * not isolated copies — clicking one navigates (seeks) the single continuous transcript+timeline.
   * Three transcript gestures (delete-selection / caret-cut / [Phase C] text-replace) are the only
   * editing affordances; there is no scene materialization (openScene) on this path.
   */
  export function FineCutView({
    client,
    asset,
    roughCutId,
    segments,
    currentFrame,
    onSeek,
    onFrame,
  }: {
    client: LauraClient;
    asset: Asset | null;
    roughCutId: string | null;
    segments: Segment[];
    currentFrame: number;
    seek: { frame: number } | null;
    onSeek: (f: number) => void;
    onFrame: (f: number) => void;
  }): ReactElement {
    const rc = useRoughCutTranscript(client, roughCutId, segments);
    const { scenes } = useScenes(client, roughCutId); // jump list (read-only nav)
    const clips = useMemo(() => rc.clips, [rc.clips]);

    // Keep the player's reload key tied to the rough-cut id so an edit re-materializes once.
    useEffect(() => {
      if (roughCutId) onFrame(currentFrame); // no-op guard keeps onFrame referenced; player drives ticks
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [roughCutId]);

    if (!roughCutId) {
      return (
        <div className="flex flex-1 items-center justify-center text-sm text-content-faint">
          Noch keine Szenen — erst Rough Cut ausführen.
        </div>
      );
    }

    return (
      <div className="grid min-h-0 flex-1 grid-cols-[200px_1fr] gap-px bg-bezel">
        <aside className="flex flex-col gap-1 overflow-auto bg-surface-0 p-2">
          {(rc.scenes.length ? rc.scenes : scenes).map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => onSeek(s.seq_in_frame)}
              className="truncate rounded px-2 py-1 text-left text-xs text-content-muted hover:bg-surface-2"
            >
              {s.name}
            </button>
          ))}
        </aside>

        <section className="flex min-h-0 flex-col">
          <div className="flex min-h-0 flex-1 items-center justify-center bg-black/40 p-4">
            <SequencePlayer
              client={client}
              projectId={asset?.project_id ?? null}
              sequenceId={roughCutId}
              reloadKey={`${roughCutId}:${clips.length}`}
              clipsOverride={clips}
              seekTo={null}
              onFrame={onFrame}
            />
          </div>

          <TimelineBar
            client={client}
            timeline={{ id: roughCutId, clips } as never}
            onChange={() => void rc.reload()}
            onScrub={(_assetId, frame) => onSeek(frame)}
            onSelect={() => undefined}
            segments={segments}
            currentFrame={currentFrame}
          />

          <ContinuousTranscript
            words={rc.words}
            scenes={rc.scenes}
            selection={rc.selection}
            onSelectionChange={rc.setSelection}
            onDeleteSelection={(a, b) => void rc.deleteRange(a, b)}
            onCutAt={(f) => void rc.cutAt(f)}
            onSeek={onSeek}
          />

          {rc.error && <div className="px-3 py-1 text-xs text-status-err">{rc.error}</div>}
        </section>
      </div>
    );
  }
  ```
  Notes: `TimelineBar`'s `timeline` prop expects a `Timeline`; passing the rough-cut id + continuous clips is the minimal shape it reads (`id`, `clips`) — if `TimelineBar` needs more fields, fetch the full timeline via `rc`/`getTimeline` and store it in the hook's state instead of the cast. (The hook already calls `getTimeline`; expose the raw `Timeline` from `useRoughCutTranscript` as `timeline` if the cast trips `tsc` in Step 4, and pass that.) The right-hand `SceneInspector` column and `seek`-prop round-trip are dropped from the default screen (spec §10: inspector is contextual, deferred); `seek` stays in the prop type for App.tsx compatibility but is unused here.
- [ ] **Step 4: Run it — expect PASS:**
  ```
  cd apps/desktop && npx vitest run src/components/FineCutView.test.tsx
  ```
  Expected: all FineCutView tests pass (old per-scene assertions that asserted `openScene` were removed/updated in Step 1).
- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/FineCutView.tsx apps/desktop/src/components/FineCutView.test.tsx
  git commit -m "feat(ui): FineCutView edits the continuous rough-cut with scene jump nav"
  ```

---

### Task A9: Phase A green verification

**Files:** none created — runs the gate over every file Phase A touched.

**Interfaces:** Consumes all Phase A artifacts. Produces a green ruff+mypy+pytest (backend) and tsc+vitest (frontend) run.

- [ ] **Step 1: Backend lint + format check** (touched files only):
  ```
  cd services/local-api && uv run --no-sync ruff check src/laura/scenes/reconcile.py src/laura/api/scenes.py src/laura/api/timelines.py src/laura/api/models.py && uv run --no-sync ruff format --check src/laura/scenes/reconcile.py src/laura/api/scenes.py src/laura/api/timelines.py src/laura/api/models.py
  ```
  Expected: `All checks passed!` and no reformat diffs.
- [ ] **Step 2: Backend types:**
  ```
  cd services/local-api && uv run --no-sync mypy src/laura/scenes/reconcile.py src/laura/api/scenes.py src/laura/api/timelines.py src/laura/api/models.py
  ```
  Expected: `Success: no issues found`.
- [ ] **Step 3: Backend tests** (the three new suites + the scene/timeline suites that could be affected):
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_scene_reconcile.py tests/test_delete_words_reconcile.py tests/test_cut_at_frame_api.py tests/test_scenes_api.py tests/test_scene_grouping.py -q
  ```
  Expected: all pass (no failures, no errors).
- [ ] **Step 4: Frontend types** (whole project tsc — `strict`, no `any`):
  ```
  cd apps/desktop && npx tsc --noEmit
  ```
  Expected: no output (exit 0).
- [ ] **Step 5: Frontend tests** (new + touched suites):
  ```
  cd apps/desktop && npx vitest run src/shared/sceneTranscript.test.ts src/hooks/useRoughCutTranscript.test.ts src/components/ContinuousTranscript.test.tsx src/components/FineCutView.test.tsx src/api.test.ts
  ```
  Expected: all files pass.
- [ ] **Step 6: Mark the manual-only paths.** Confirm these are recorded as "manuell zu prüfen (live CDP 9222)": drag-select feel across scene boundaries; click-caret → new-scene marker appears; scene-jump scroll-to position; the delete ripple visibly shortening the timeline. (Headless vitest covers the logic; the pointer feel and visual marker placement are live-only.)
- [ ] **Step 7: Final commit** (only if Steps 1–2 produced auto-fixes worth keeping; otherwise no-op):
  ```
  git add services/local-api/src/laura/scenes/reconcile.py services/local-api/src/laura/api/scenes.py services/local-api/src/laura/api/timelines.py services/local-api/src/laura/api/models.py apps/desktop/src/shared/sceneTranscript.ts apps/desktop/src/hooks/useRoughCutTranscript.ts apps/desktop/src/components/ContinuousTranscript.tsx apps/desktop/src/components/FineCutView.tsx
  git commit -m "chore(feinschnitt): Phase A green — ruff+mypy+pytest+tsc+vitest pass"
  ```

---

## Phase B — Vorschau-Audio-Mix (Web Audio)

Diese Phase macht den In-App-Player **hörbar**: `SequencePlayer` bekommt eine neue Prop `audioClips: TimelineAudioClip[]` und einen internen, frame-synchronen **AudioMixer** (Web Audio / `<audio>`), der VO + Szenen-Musik exakt zum Video-`currentFrame` abspielt und dabei Gain / Fades / Ducking anwendet — gespiegelt aus der Export-Mix-Semantik (`mp4.py`: `volume`/`atrim`/`afade`/`adelay`, Ducking via `volume=enable='between(t,…)'`, `replace_original`/`mute_original` ⇒ Ducking 0). `AssembleView` + `FineCutView` werden so verdrahtet, dass sie `audioClips` an den Player durchreichen. Es wird **kein** Backend angefasst; die A2-Clips kommen über das bereits existierende `listTimelineAudioClips`.

Reines, testbares Frame→Zeit- und Mix-Mapping wird in eine separate Modul-Datei `shared/audioMix.ts` ausgelagert (vitest-prüfbar ohne React/AudioContext). Die `<audio>`-Verdrahtung selbst ist nur live (CDP 9222) prüfbar und entsprechend markiert.

---

### Task B1: Reine Mix-Mapping-Helfer (`shared/audioMix.ts`)

Reine Funktionen, die die Export-Semantik aus `mp4.py` spiegeln: pro A2-Clip die Quell-Zeit (aus `asset_in_frame`), den Soll-Gain (inkl. linearer Fade-In/Fade-Out-Hüllkurve in Sekunden) und den Ducking-Faktor der Videospur an einem gegebenen Sequence-Frame. Alles end-exclusive, Frames als ganzzahlige Sequence-Frames, Sekunden = `frames * rate_den / rate_num` (identisch zu `_seconds(frame * rate_den / rate_num)` in `mp4.py`).

**Files:**
- Create `apps/desktop/src/shared/audioMix.ts`
- Create `apps/desktop/src/shared/audioMix.test.ts`

**Interfaces:**
- Consumes: `TimelineAudioClip`, `AudioMixMode` (from `../api`).
- Produces:
  - `seqFrameToSeconds(frame: number, rateNum: number, rateDen: number): number`
  - `clipActiveAt(clip: TimelineAudioClip, seqFrame: number): boolean` — true wenn `seq_in_frame <= seqFrame < seq_out_frame_exclusive`.
  - `clipSourceTimeSeconds(clip: TimelineAudioClip, seqFrame: number, rateNum: number, rateDen: number): number` — `(asset_in_frame + (seqFrame - seq_in_frame)) * rateDen / rateNum`.
  - `clipGainAt(clip: TimelineAudioClip, seqFrame: number, rateNum: number, rateDen: number): number` — `gain_percent/100`, multipliziert mit linearer Fade-Hüllkurve (Fade-In über `fade_in_frames` ab `seq_in_frame`, Fade-Out über `fade_out_frames` bis `seq_out_frame_exclusive`); 0 außerhalb der Spanne.
  - `videoDuckGainAt(clips: TimelineAudioClip[], seqFrame: number): number` — min. Ducking-Faktor aller aktiven Clips (`replace_original`/`mute_original` ⇒ 0; `mix` ⇒ `ducking_percent/100`; 1 wenn keiner aktiv).

- [ ] **Step 1: Failing-Test schreiben.** Lege `apps/desktop/src/shared/audioMix.test.ts` an:
  ```ts
  import { describe, expect, it } from "vitest";
  import type { TimelineAudioClip } from "../api";
  import {
    clipActiveAt,
    clipGainAt,
    clipSourceTimeSeconds,
    seqFrameToSeconds,
    videoDuckGainAt,
  } from "./audioMix";

  function clip(over: Partial<TimelineAudioClip> = {}): TimelineAudioClip {
    return {
      id: "a1",
      timeline_id: "t1",
      asset_id: "vo1",
      seq_in_frame: 30,
      seq_out_frame_exclusive: 90,
      asset_in_frame: 0,
      gain_percent: 100,
      fade_in_frames: 0,
      fade_out_frames: 0,
      mix_mode: "mix",
      ducking_percent: 100,
      label: null,
      created_at: "",
      ...over,
    };
  }

  describe("audioMix mapping (mirrors mp4.py export semantics)", () => {
    it("converts seq frames to seconds at 30/1", () => {
      expect(seqFrameToSeconds(30, 30, 1)).toBeCloseTo(1, 6);
      expect(seqFrameToSeconds(0, 30, 1)).toBe(0);
    });

    it("clipActiveAt is end-exclusive", () => {
      expect(clipActiveAt(clip(), 29)).toBe(false);
      expect(clipActiveAt(clip(), 30)).toBe(true);
      expect(clipActiveAt(clip(), 89)).toBe(true);
      expect(clipActiveAt(clip(), 90)).toBe(false);
    });

    it("source time accounts for asset_in_frame and intra-clip offset", () => {
      // asset_in 15, seq_in 30, query frame 45 -> src frame 30 -> 1.0s @30fps
      expect(clipSourceTimeSeconds(clip({ asset_in_frame: 15 }), 45, 30, 1)).toBeCloseTo(1, 6);
    });

    it("gain applies gain_percent and linear fades", () => {
      expect(clipGainAt(clip({ gain_percent: 50 }), 60, 30, 1)).toBeCloseTo(0.5, 6);
      // fade_in 30 frames from seq_in 30: at frame 45 (halfway) -> 0.5 * gain(1.0)
      expect(clipGainAt(clip({ fade_in_frames: 30 }), 45, 30, 1)).toBeCloseTo(0.5, 6);
      // fade_out 30 frames ending at 90: at frame 75 (halfway through fade) -> 0.5
      expect(clipGainAt(clip({ fade_out_frames: 30 }), 75, 30, 1)).toBeCloseTo(0.5, 6);
      expect(clipGainAt(clip(), 10, 30, 1)).toBe(0); // outside span
    });

    it("video duck gain mirrors export ducking (replace/mute -> 0)", () => {
      expect(videoDuckGainAt([clip({ mix_mode: "mix", ducking_percent: 30 })], 60)).toBeCloseTo(0.3, 6);
      expect(videoDuckGainAt([clip({ mix_mode: "replace_original" })], 60)).toBe(0);
      expect(videoDuckGainAt([clip({ mix_mode: "mute_original" })], 60)).toBe(0);
      expect(videoDuckGainAt([clip()], 200)).toBe(1); // none active
      expect(videoDuckGainAt([], 60)).toBe(1);
    });
  });
  ```

- [ ] **Step 2: Test laufen lassen — FAIL erwartet.**
  ```
  npx vitest run src/shared/audioMix.test.ts
  ```
  Erwartung: FAIL — `Failed to resolve import "./audioMix"` (Modul existiert noch nicht).

- [ ] **Step 3: Minimal-Implementierung.** Lege `apps/desktop/src/shared/audioMix.ts` an:
  ```ts
  import type { TimelineAudioClip } from "../api";

  /** Sequence frame -> seconds, identical to mp4.py `_seconds(frame * rate_den / rate_num)`. */
  export function seqFrameToSeconds(frame: number, rateNum: number, rateDen: number): number {
    return (frame * rateDen) / rateNum;
  }

  /** End-exclusive activity test: seq_in_frame <= seqFrame < seq_out_frame_exclusive. */
  export function clipActiveAt(clip: TimelineAudioClip, seqFrame: number): boolean {
    return seqFrame >= clip.seq_in_frame && seqFrame < clip.seq_out_frame_exclusive;
  }

  /**
   * Source-file time (seconds) for this clip at `seqFrame`. Mirrors the export `atrim`
   * start: asset_in_frame + intra-clip offset, in seconds.
   */
  export function clipSourceTimeSeconds(
    clip: TimelineAudioClip,
    seqFrame: number,
    rateNum: number,
    rateDen: number,
  ): number {
    const srcFrame = clip.asset_in_frame + (seqFrame - clip.seq_in_frame);
    return seqFrameToSeconds(Math.max(0, srcFrame), rateNum, rateDen);
  }

  /**
   * Effective gain (0..n) at `seqFrame`: gain_percent/100 * linear fade envelope.
   * Mirrors the export `volume` + `afade=t=in/out`. Zero outside the span.
   */
  export function clipGainAt(
    clip: TimelineAudioClip,
    seqFrame: number,
    rateNum: number,
    rateDen: number,
  ): number {
    if (!clipActiveAt(clip, seqFrame)) return 0;
    const base = clip.gain_percent / 100;
    let env = 1;
    if (clip.fade_in_frames > 0) {
      const into = seqFrame - clip.seq_in_frame;
      if (into < clip.fade_in_frames) env = Math.min(env, into / clip.fade_in_frames);
    }
    if (clip.fade_out_frames > 0) {
      const untilEnd = clip.seq_out_frame_exclusive - seqFrame;
      if (untilEnd < clip.fade_out_frames) env = Math.min(env, untilEnd / clip.fade_out_frames);
    }
    // rateNum/rateDen reserved for sub-frame envelope precision; frame-granular here.
    void rateNum;
    void rateDen;
    return base * Math.max(0, env);
  }

  /**
   * Video-track duck factor (0..1) at `seqFrame`, mirroring mp4.py:
   * replace_original/mute_original -> 0; mix -> ducking_percent/100; 1 when no clip active.
   * Multiple overlapping clips take the strongest duck (lowest factor).
   */
  export function videoDuckGainAt(clips: TimelineAudioClip[], seqFrame: number): number {
    let factor = 1;
    for (const c of clips) {
      if (!clipActiveAt(c, seqFrame)) continue;
      const f =
        c.mix_mode === "replace_original" || c.mix_mode === "mute_original"
          ? 0
          : c.ducking_percent / 100;
      if (f < factor) factor = f;
    }
    return factor;
  }
  ```

- [ ] **Step 4: Test laufen lassen — PASS erwartet.**
  ```
  npx vitest run src/shared/audioMix.test.ts
  ```
  Erwartung: PASS — 5 Tests grün.

- [ ] **Step 5: Commit (nur diese zwei Dateien, expliziter `git add`).**
  ```
  git add apps/desktop/src/shared/audioMix.ts apps/desktop/src/shared/audioMix.test.ts
  git commit -m "feat(preview): pure frame->time/gain/duck mappers mirroring export mix"
  ```

---

### Task B2: `AudioMixer` (Web-Audio-Klasse, `shared/AudioMixer.ts`)

Eine framework-freie Klasse, die pro A2-Clip ein `HTMLAudioElement` (Quelle `laura-media://media/<asset_id>/proxy`) hält und an einen `GainNode` hängt; `currentFrame`-getrieben startet/stoppt/sync't sie die Clips und setzt Gains gemäß B1. Diese Klasse kapselt die Web-Audio-Verdrahtung, damit `SequencePlayer` schlank bleibt. Konstruktor nimmt eine injizierbare `AudioElementFactory`, sodass die Sync-Logik in vitest mit Fakes prüfbar ist (kein echtes `AudioContext`/DOM nötig).

**Files:**
- Create `apps/desktop/src/shared/AudioMixer.ts`
- Create `apps/desktop/src/shared/AudioMixer.test.ts`

**Interfaces:**
- Consumes: `TimelineAudioClip`; `clipActiveAt`, `clipGainAt`, `clipSourceTimeSeconds` (from `./audioMix`).
- Produces:
  - `interface MixerAudioEl { currentTime: number; volume: number; paused: boolean; src: string; play(): Promise<void>; pause(): void; load(): void; }`
  - `type AudioElementFactory = (src: string) => MixerAudioEl;`
  - `class AudioMixer { constructor(opts: { rateNum: number; rateDen: number; makeEl?: AudioElementFactory }); setClips(clips: TimelineAudioClip[]): void; syncTo(seqFrame: number, playing: boolean): void; pauseAll(): void; dispose(): void; }`
  - Static helper `static SYNC_DRIFT_SECONDS = 0.08;` (re-seek threshold; mirrors §14 „Web-Audio-Drift → an currentFrame koppeln, bei Seek/Pause neu sync’en").

- [ ] **Step 1: Failing-Test schreiben.** Lege `apps/desktop/src/shared/AudioMixer.test.ts` an:
  ```ts
  import { describe, expect, it } from "vitest";
  import type { TimelineAudioClip } from "../api";
  import { AudioMixer, type MixerAudioEl } from "./AudioMixer";

  class FakeEl implements MixerAudioEl {
    currentTime = 0;
    volume = 1;
    paused = true;
    src: string;
    playCount = 0;
    pauseCount = 0;
    loadCount = 0;
    constructor(src: string) {
      this.src = src;
    }
    play(): Promise<void> {
      this.paused = false;
      this.playCount += 1;
      return Promise.resolve();
    }
    pause(): void {
      this.paused = true;
      this.pauseCount += 1;
    }
    load(): void {
      this.loadCount += 1;
    }
  }

  function clip(over: Partial<TimelineAudioClip> = {}): TimelineAudioClip {
    return {
      id: "a1",
      timeline_id: "t1",
      asset_id: "vo1",
      seq_in_frame: 30,
      seq_out_frame_exclusive: 90,
      asset_in_frame: 0,
      gain_percent: 100,
      fade_in_frames: 0,
      fade_out_frames: 0,
      mix_mode: "mix",
      ducking_percent: 30,
      label: null,
      created_at: "",
      ...over,
    };
  }

  describe("AudioMixer sync", () => {
    function makeMixer(): { mixer: AudioMixer; els: Map<string, FakeEl> } {
      const els = new Map<string, FakeEl>();
      const mixer = new AudioMixer({
        rateNum: 30,
        rateDen: 1,
        makeEl: (src) => {
          const el = new FakeEl(src);
          els.set(src, el);
          return el;
        },
      });
      return { mixer, els };
    }

    it("creates one audio element per clip pointing at the asset proxy", () => {
      const { mixer, els } = makeMixer();
      mixer.setClips([clip({ id: "a1", asset_id: "vo1" })]);
      expect(els.has("laura-media://media/vo1/proxy")).toBe(true);
    });

    it("plays a clip only while inside its span and seeks to source time", () => {
      const { mixer, els } = makeMixer();
      mixer.setClips([clip()]);
      const el = els.get("laura-media://media/vo1/proxy")!;

      mixer.syncTo(10, true); // before span
      expect(el.paused).toBe(true);

      mixer.syncTo(60, true); // mid span (frame 60 -> src 1.0s)
      expect(el.paused).toBe(false);
      expect(el.currentTime).toBeCloseTo(1, 2);
      expect(el.volume).toBeCloseTo(1, 6);

      mixer.syncTo(95, true); // past span
      expect(el.paused).toBe(true);
    });

    it("re-seeks only when drift exceeds the threshold (avoids stutter)", () => {
      const { mixer, els } = makeMixer();
      mixer.setClips([clip()]);
      const el = els.get("laura-media://media/vo1/proxy")!;
      mixer.syncTo(60, true);
      el.currentTime = 1.02; // tiny drift, within threshold
      mixer.syncTo(60, true);
      expect(el.currentTime).toBeCloseTo(1.02, 3); // not re-seeked
      el.currentTime = 5; // large drift
      mixer.syncTo(60, true);
      expect(el.currentTime).toBeCloseTo(1, 2); // re-seeked back
    });

    it("pauseAll pauses every element", () => {
      const { mixer, els } = makeMixer();
      mixer.setClips([clip()]);
      mixer.syncTo(60, true);
      mixer.pauseAll();
      expect(els.get("laura-media://media/vo1/proxy")!.paused).toBe(true);
    });
  });
  ```

- [ ] **Step 2: Test laufen lassen — FAIL erwartet.**
  ```
  npx vitest run src/shared/AudioMixer.test.ts
  ```
  Erwartung: FAIL — `Failed to resolve import "./AudioMixer"`.

- [ ] **Step 3: Minimal-Implementierung.** Lege `apps/desktop/src/shared/AudioMixer.ts` an:
  ```ts
  import type { TimelineAudioClip } from "../api";
  import { clipActiveAt, clipGainAt, clipSourceTimeSeconds } from "./audioMix";

  /** The slice of HTMLAudioElement the mixer needs; injectable so sync logic is testable. */
  export interface MixerAudioEl {
    currentTime: number;
    volume: number;
    paused: boolean;
    src: string;
    play(): Promise<void>;
    pause(): void;
    load(): void;
  }

  export type AudioElementFactory = (src: string) => MixerAudioEl;

  function mediaUrl(assetId: string): string {
    // Audio assets are streamed via the same laura-media scheme as video proxies
    // (main.ts resolveMediaPath resolves any asset file by kind).
    return `laura-media://media/${assetId}/proxy`;
  }

  function defaultFactory(src: string): MixerAudioEl {
    const el = new Audio();
    el.src = src;
    el.preload = "auto";
    el.crossOrigin = "anonymous";
    return el as unknown as MixerAudioEl;
  }

  /**
   * Plays the timeline A2 clips (VO + music) synced to the video currentFrame, applying
   * gain + fades per clip (clipGainAt) — mirrors the export mix. Ducking of the *video*
   * track is computed by the caller (videoDuckGainAt) and applied to the <video>.volume.
   */
  export class AudioMixer {
    static readonly SYNC_DRIFT_SECONDS = 0.08;

    private readonly rateNum: number;
    private readonly rateDen: number;
    private readonly makeEl: AudioElementFactory;
    private clips: TimelineAudioClip[] = [];
    private els = new Map<string, MixerAudioEl>(); // clip.id -> element

    constructor(opts: { rateNum: number; rateDen: number; makeEl?: AudioElementFactory }) {
      this.rateNum = opts.rateNum;
      this.rateDen = opts.rateDen;
      this.makeEl = opts.makeEl ?? defaultFactory;
    }

    setClips(clips: TimelineAudioClip[]): void {
      // Drop elements for clips that are gone.
      const keep = new Set(clips.map((c) => c.id));
      for (const [id, el] of this.els) {
        if (!keep.has(id)) {
          el.pause();
          this.els.delete(id);
        }
      }
      // Create elements for new clips.
      for (const c of clips) {
        if (!this.els.has(c.id)) {
          this.els.set(c.id, this.makeEl(mediaUrl(c.asset_id)));
        }
      }
      this.clips = clips;
    }

    syncTo(seqFrame: number, playing: boolean): void {
      for (const c of this.clips) {
        const el = this.els.get(c.id);
        if (!el) continue;
        const active = clipActiveAt(c, seqFrame);
        if (!active || !playing) {
          if (!el.paused) el.pause();
          continue;
        }
        el.volume = Math.min(1, clipGainAt(c, seqFrame, this.rateNum, this.rateDen));
        const target = clipSourceTimeSeconds(c, seqFrame, this.rateNum, this.rateDen);
        if (Math.abs(el.currentTime - target) > AudioMixer.SYNC_DRIFT_SECONDS) {
          el.currentTime = target;
        }
        if (el.paused) void el.play();
      }
    }

    pauseAll(): void {
      for (const el of this.els.values()) if (!el.paused) el.pause();
    }

    dispose(): void {
      this.pauseAll();
      this.els.clear();
      this.clips = [];
    }
  }
  ```

- [ ] **Step 4: Test laufen lassen — PASS erwartet.**
  ```
  npx vitest run src/shared/AudioMixer.test.ts
  ```
  Erwartung: PASS — 4 Tests grün.

- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/shared/AudioMixer.ts apps/desktop/src/shared/AudioMixer.test.ts
  git commit -m "feat(preview): AudioMixer web-audio class (per-clip <audio>, drift-gated sync)"
  ```

---

### Task B3: `SequencePlayer` bekommt `audioClips`-Prop + Mixer-Verdrahtung

Neue optionale Prop `audioClips?: TimelineAudioClip[]` und `rateNum?`/`rateDen?` (für Frame→Sekunde; Default 30/1 wie AssembleView). Der Player hält eine `AudioMixer`-Instanz in einem Ref, ruft `mixer.setClips(...)` bei Prop-Änderung auf, und treibt `mixer.syncTo(seqFrame, playing)` bei jedem Frame-Tick/Seek/Pause. Die Videospur wird per `video.volume = videoDuckGainAt(audioClips, seqFrame)` geduckt. Bei `pause`/Sequence-Wechsel/Unmount wird der Mixer pausiert/entsorgt. Default ohne `audioClips` = unverändert (kein Mixer, kein Ducking).

**Files:**
- Modify `apps/desktop/src/components/SequencePlayer.tsx` (Props-Interface ~54-75; Component-Signatur 77-85; `handleTimeUpdate` 257-291; `seekToSeqFrame` 193-218; `toggle`/`<video>` `onPause`/`onPlay` 171-184, 321-324; neuer Effekt-Block)
- Modify `apps/desktop/src/components/SequencePlayer.test.tsx` *(falls vorhanden — sonst Create)*

**Interfaces:**
- Consumes: `TimelineAudioClip` (from `../api`); `AudioMixer` (from `../shared/AudioMixer`); `videoDuckGainAt` (from `../shared/audioMix`).
- Produces: erweiterte `SequencePlayerProps` mit `audioClips?: TimelineAudioClip[]; rateNum?: number; rateDen?: number;`.

- [ ] **Step 1: Failing-Test schreiben.** Erstelle/erweitere `apps/desktop/src/components/SequencePlayer.test.tsx` um einen reinen Logik-Test der bereits exportierten Helfer **plus** die neue Re-Export-Erwartung, dass `SequencePlayer` `videoDuckGainAt` für die Lautstärke nutzt. Da der `<audio>`-Pfad nur live prüfbar ist, testen wir hier die **Prop-Akzeptanz + Duck-Berechnung** über einen kleinen reinen Adapter, den der Player exportiert:
  ```tsx
  import { describe, expect, it } from "vitest";
  import type { TimelineAudioClip } from "../api";
  import { videoVolumeForFrame } from "./SequencePlayer";

  function clip(over: Partial<TimelineAudioClip> = {}): TimelineAudioClip {
    return {
      id: "a1",
      timeline_id: "t1",
      asset_id: "vo1",
      seq_in_frame: 30,
      seq_out_frame_exclusive: 90,
      asset_in_frame: 0,
      gain_percent: 100,
      fade_in_frames: 0,
      fade_out_frames: 0,
      mix_mode: "mix",
      ducking_percent: 30,
      label: null,
      created_at: "",
      ...over,
    };
  }

  describe("SequencePlayer video ducking adapter", () => {
    it("ducks the video to ducking_percent under an active VO span", () => {
      expect(videoVolumeForFrame([clip()], 60)).toBeCloseTo(0.3, 6);
    });
    it("is full volume outside any VO span", () => {
      expect(videoVolumeForFrame([clip()], 200)).toBe(1);
      expect(videoVolumeForFrame(undefined, 60)).toBe(1);
    });
    it("replace_original mutes the video under the span", () => {
      expect(videoVolumeForFrame([clip({ mix_mode: "replace_original" })], 60)).toBe(0);
    });
  });
  ```

- [ ] **Step 2: Test laufen lassen — FAIL erwartet.**
  ```
  npx vitest run src/components/SequencePlayer.test.tsx
  ```
  Erwartung: FAIL — `videoVolumeForFrame` ist (noch) kein Export von `SequencePlayer`.

- [ ] **Step 3: Implementierung in `SequencePlayer.tsx`.**
  - Imports ergänzen (oben):
    ```ts
    import { type Asset, type TimelineAudioClip, type TimelineClip, hasFile } from "../api";
    import type { LauraClient } from "../api";
    import { AudioMixer } from "../shared/AudioMixer";
    import { videoDuckGainAt } from "../shared/audioMix";
    ```
  - Reinen, exportierten Adapter direkt neben `clipIndexAtSeqFrame` einfügen:
    ```ts
    /**
     * Video-track volume (0..1) at `frame` given the A2 overlay clips. Pure wrapper over
     * videoDuckGainAt so the ducking rule is unit-testable without mounting the player.
     */
    export function videoVolumeForFrame(
      audioClips: TimelineAudioClip[] | undefined,
      frame: number,
    ): number {
      return videoDuckGainAt(audioClips ?? [], frame);
    }
    ```
  - Props-Interface erweitern (im `SequencePlayerProps`-Block):
    ```ts
      /**
       * VO + music timeline audio clips to play synced to the video playhead (Phase B).
       * When omitted the player is video-only (unchanged). Mirrors the export mix:
       * gain/fades per clip, the video track ducks under overlapping VO spans.
       */
      audioClips?: TimelineAudioClip[];
      /** Sequence frame rate for frame->time mapping (defaults 30/1, matching AssembleView). */
      rateNum?: number;
      rateDen?: number;
    ```
  - Signatur destrukturieren:
    ```ts
    export function SequencePlayer({
      client,
      projectId,
      sequenceId,
      reloadKey,
      seekTo,
      onFrame,
      clipsOverride,
      audioClips,
      rateNum = 30,
      rateDen = 1,
    }: SequencePlayerProps): ReactElement {
    ```
  - Mixer-Ref + Lifecycle-Effekte direkt unter den bestehenden Refs (nach Zeile ~103):
    ```ts
      const mixerRef = useRef<AudioMixer | null>(null);
      if (mixerRef.current === null) {
        mixerRef.current = new AudioMixer({ rateNum, rateDen });
      }
      // Keep the mixer's clip set in sync with the prop.
      useEffect(() => {
        mixerRef.current?.setClips(audioClips ?? []);
      }, [audioClips]);
      // Pause + tear down on unmount.
      useEffect(() => {
        return () => {
          mixerRef.current?.dispose();
          mixerRef.current = null;
        };
      }, []);
    ```
  - Hilfsfunktion `applyAudio(frame, playing)` einfügen und aus den Stellen aufrufen, die `setSeqFrame`/`onFrame` setzen:
    ```ts
      function applyAudio(frame: number, isPlaying: boolean): void {
        const v = videoRef.current;
        if (v) v.volume = videoVolumeForFrame(audioClips, frame);
        mixerRef.current?.syncTo(frame, isPlaying);
      }
    ```
    - In `handleTimeUpdate`, nach `setSeqFrame(sf); onFrame?.(sf);` → `applyAudio(sf, !v.paused);`
    - Im End-of-sequence-Zweig (nach `setPlaying(false)`) → `mixerRef.current?.pauseAll();`
    - In `seekToSeqFrame`, nach beiden `setSeqFrame(target);` → `applyAudio(target, false);`
    - `<video onPause>` → `onPause={() => { setPlaying(false); mixerRef.current?.pauseAll(); }}`
    - `<video onPlay>` → `onPlay={() => { setPlaying(true); applyAudio(seqFrame, true); }}`

- [ ] **Step 4: Test laufen lassen — PASS erwartet.**
  ```
  npx vitest run src/components/SequencePlayer.test.tsx
  ```
  Erwartung: PASS — 3 Tests grün.

- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/SequencePlayer.tsx apps/desktop/src/components/SequencePlayer.test.tsx
  git commit -m "feat(preview): SequencePlayer audioClips prop + AudioMixer wiring with video ducking"
  ```

---

### Task B4: `AssembleView` reicht `audioClips` an `SequencePlayer` durch

`AssembleView` lädt die A2-Clips bereits (`reloadAudioClips` → `setAudioClips`) und gibt sie an `TimelineBar`, **nicht** aber an den Player. Diese Task leitet `audioClips` + die vorhandenen `rateNum`/`rateDen`-Props an `SequencePlayer` weiter, sodass VO/Musik im Zusammenfügen-Tab hörbar werden.

**Files:**
- Modify `apps/desktop/src/components/AssembleView.tsx` (`<SequencePlayer …>` Aufruf, Zeilen 744-750)

**Interfaces:**
- Consumes: bestehender `audioClips`-State (Zeile 455), `rateNum`/`rateDen` (Props, 447-448).
- Produces: keine neuen Exporte — reine Verdrahtung.

- [ ] **Step 1: Failing-Test schreiben.** Erweitere `apps/desktop/src/components/AssembleView.test.tsx` um eine Assertion, dass der gerenderte `SequencePlayer` `audioClips` erhält. Da `SequencePlayer` in den AssembleView-Tests bereits gemockt wird, prüfe die an den Mock übergebenen Props. Füge im bestehenden `vi.mock("./SequencePlayer", …)`-Block eine Prop-Erfassung hinzu (oder ergänze einen neuen Test, der den zuletzt empfangenen Prop-Satz inspiziert):
  ```tsx
  // In the SequencePlayer mock: record the props it was called with.
  // (top of file)
  export const seqPlayerProps: { audioClips?: unknown; rateNum?: number } = {};
  vi.mock("./SequencePlayer", () => ({
    SequencePlayer: (props: { audioClips?: unknown; rateNum?: number }) => {
      seqPlayerProps.audioClips = props.audioClips;
      seqPlayerProps.rateNum = props.rateNum;
      return null;
    },
  }));

  it("passes loaded audio clips and frame rate to the SequencePlayer", async () => {
    // render AssembleView with a client whose listTimelineAudioClips resolves a clip
    // (reuse the existing render harness in this file), then:
    await waitFor(() => expect(Array.isArray(seqPlayerProps.audioClips)).toBe(true));
    expect(seqPlayerProps.rateNum).toBe(30);
  });
  ```
  *(Wenn die bestehende Datei `SequencePlayer` bereits anders mockt: den vorhandenen Mock um die `audioClips`/`rateNum`-Erfassung erweitern, statt ihn zu duplizieren.)*

- [ ] **Step 2: Test laufen lassen — FAIL erwartet.**
  ```
  npx vitest run src/components/AssembleView.test.tsx
  ```
  Erwartung: FAIL — `seqPlayerProps.audioClips` ist `undefined` (Prop wird noch nicht durchgereicht).

- [ ] **Step 3: Verdrahtung in `AssembleView.tsx`.** Den Player-Aufruf erweitern:
  ```tsx
          <SequencePlayer
            client={client}
            projectId={projectId}
            sequenceId={sequence?.timeline_id ?? null}
            reloadKey={reloadKey}
            onFrame={setSeqFrame}
            audioClips={audioClips}
            rateNum={rateNum}
            rateDen={rateDen}
          />
  ```

- [ ] **Step 4: Test laufen lassen — PASS erwartet.**
  ```
  npx vitest run src/components/AssembleView.test.tsx
  ```
  Erwartung: PASS.

- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/AssembleView.tsx apps/desktop/src/components/AssembleView.test.tsx
  git commit -m "feat(preview): AssembleView passes audioClips + rate to SequencePlayer"
  ```

---

### Task B5: `FineCutView` lädt A2-Clips und reicht sie an `SequencePlayer` durch

`FineCutView` nutzt `SequencePlayer` mit `clipsOverride`, lädt aber keine `audioClips`. Da der Feinschnitt-Editierpfad (Phase A/C) auf der Rough-Cut-Timeline arbeitet, soll die platzierte/erzeugte VO auch hier hörbar sein. Diese Task lädt `listTimelineAudioClips(scene.timeline.id)` und reicht das Ergebnis + Rate an den Player.

**Files:**
- Modify `apps/desktop/src/components/FineCutView.tsx` (Imports; neuer `audioClips`-State + Lade-Effekt; `<SequencePlayer …>` 183-191)

**Interfaces:**
- Consumes: `client.listTimelineAudioClips(timelineId)` (bereits in `api.ts`); `TimelineAudioClip` (from `../api`).
- Produces: keine neuen Exporte — reine Verdrahtung. (Rate: Feinschnitt-Timelines laufen über `clipsOverride`; mangels expliziter Rate-Prop wird der AssembleView-konsistente Default 30/1 verwendet — als „manuell zu prüfen" markiert, falls projektweite Rate ≠ 30.)

- [ ] **Step 1: Failing-Test schreiben.** Erweitere `apps/desktop/src/components/FineCutView.test.tsx`: mock `SequencePlayer` und erfasse die `audioClips`-Prop; gib im Test-Client `listTimelineAudioClips` zurück, das ein Clip-Array auflöst, und assertiere, dass es am Player ankommt:
  ```tsx
  export const fcSeqPlayerProps: { audioClips?: unknown } = {};
  vi.mock("./SequencePlayer", () => ({
    SequencePlayer: (props: { audioClips?: unknown }) => {
      fcSeqPlayerProps.audioClips = props.audioClips;
      return null;
    },
  }));

  it("loads timeline audio clips and forwards them to the player", async () => {
    // render FineCutView with a selected scene whose timeline.id is "t1",
    // and a client where listTimelineAudioClips("t1") resolves [oneClip].
    await waitFor(() => expect(Array.isArray(fcSeqPlayerProps.audioClips)).toBe(true));
    expect((fcSeqPlayerProps.audioClips as unknown[]).length).toBe(1);
  });
  ```
  *(Vorhandenen `SequencePlayer`-Mock in der Datei wiederverwenden/erweitern statt duplizieren.)*

- [ ] **Step 2: Test laufen lassen — FAIL erwartet.**
  ```
  npx vitest run src/components/FineCutView.test.tsx
  ```
  Erwartung: FAIL — `fcSeqPlayerProps.audioClips` ist `undefined`.

- [ ] **Step 3: Implementierung in `FineCutView.tsx`.**
  - Import ergänzen:
    ```ts
    import { type TimelineAudioClip } from "../api";
    ```
  - State + Lade-Effekt (bei den übrigen `useState`/`useEffect`-Blöcken):
    ```ts
      const [audioClips, setAudioClips] = useState<TimelineAudioClip[]>([]);
      useEffect(() => {
        const timelineId = scene.timeline?.id ?? null;
        if (!timelineId) {
          setAudioClips([]);
          return;
        }
        let cancelled = false;
        client
          .listTimelineAudioClips(timelineId)
          .then((cs) => {
            if (!cancelled) setAudioClips(cs);
          })
          .catch(() => {
            if (!cancelled) setAudioClips([]);
          });
        return () => {
          cancelled = true;
        };
      }, [client, scene.timeline?.id]);
    ```
  - Player-Aufruf erweitern:
    ```tsx
            <SequencePlayer
              client={client}
              projectId={asset?.project_id ?? null}
              sequenceId={scene.timeline.id}
              reloadKey={scene.timeline.id}
              clipsOverride={clips}
              seekTo={seekToSeq}
              onFrame={handleSeqFrame}
              audioClips={audioClips}
            />
    ```

- [ ] **Step 4: Test laufen lassen — PASS erwartet.**
  ```
  npx vitest run src/components/FineCutView.test.tsx
  ```
  Erwartung: PASS.

- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/FineCutView.tsx apps/desktop/src/components/FineCutView.test.tsx
  git commit -m "feat(preview): FineCutView loads A2 clips + forwards audioClips to player"
  ```

---

### Task B6: Phase-B-Verifikation (tsc + vitest für die berührten Frontend-Dateien) + manuelle Live-Checks

Grüne Schlusskontrolle aller berührten Frontend-Dateien (kein Backend in dieser Phase). Plus dokumentierte Liste der nur live (CDP 9222) prüfbaren UI-Pfade.

**Files:** keine Änderung — nur Verifikation.

**Interfaces:** keine.

- [ ] **Step 1: Typecheck (gesamtes Renderer-Projekt, strict, kein `any`).**
  ```
  cd apps/desktop && npm run typecheck
  ```
  Erwartung: `tsc --noEmit` ohne Fehler (keine Ausgabe, Exit 0). Insbesondere: `audioClips?: TimelineAudioClip[]` typt sauber, keine `any` in `AudioMixer.ts`/`audioMix.ts`.

- [ ] **Step 2: Lint der berührten Dateien.**
  ```
  cd apps/desktop && npx eslint src/shared/audioMix.ts src/shared/AudioMixer.ts src/components/SequencePlayer.tsx src/components/AssembleView.tsx src/components/FineCutView.tsx
  ```
  Erwartung: keine Fehler (0 Probleme).

- [ ] **Step 3: Vitest für alle Phase-B-Suites.**
  ```
  cd apps/desktop && npx vitest run src/shared/audioMix.test.ts src/shared/AudioMixer.test.ts src/components/SequencePlayer.test.tsx src/components/AssembleView.test.tsx src/components/FineCutView.test.tsx
  ```
  Erwartung: alle Suites PASS (audioMix 5, AudioMixer 4, SequencePlayer 3, AssembleView + FineCutView je grün; keine Regressionen).

- [ ] **Step 4: Manuelle Live-Checks dokumentieren (headless nicht prüfbar — live auf CDP 9222).** In der PR-Beschreibung / `tasks/todo.md` festhalten:
  - VO-Hörbarkeit: A2-Clip im Zusammenfügen platzieren → Play → neue Stimme ist hörbar, Originalton duckt unter der Spanne (`mix`/`ducking_percent`) bzw. ist still (`replace_original`/`mute_original`).
  - Sync-Gefühl beim Scrubben/Seek: Audio springt mit dem Playhead, kein hörbares Stottern (Drift-Schwelle 0.08 s greift).
  - Pause/Sequenzwechsel: Audio stoppt, kein „Geister-Audio" nach Wechsel der Szene/Sequenz.
  - Feinschnitt: in einer Szene mit platzierter VO ist die Stimme im Feinschnitt-Player ebenfalls hörbar.

- [ ] **Step 5: Commit (nur falls in diesem Schritt Doku-Dateien wie `tasks/todo.md` berührt wurden).**
  ```
  git add tasks/todo.md
  git commit -m "docs(preview): Phase B verification notes + manual live-check checklist"
  ```

---

## Phase C — Auto-Pipeline (Transkript ersetzen -> VO + Lipsync + glaetten)

This phase wires the three consequences of a transcript text-edit. The user only ever picks a **voice**; everything else (VO render, conditional lipsync, jump-cut smoothing offer, preview refresh) is automatic. Default path runs without neural TTS / lipsync sidecar / VLM (SAPI/stub/heuristic). All ranges end-exclusive, frames are integer sequence frames, idempotency via semantic identity.

Key design decision (verified against `handlers.py`/`runner.py`): the VO→lipsync coupling lives **inside `handle_voiceover`'s success path** (it has `ctx.db` and can `enqueue`), but the *decision* is extracted into a **pure planner** `plan_lipsync_after_voiceover(...)` so it is unit-testable without ffmpeg. The probe is done with the resolved lipsync backend (`StubLipsyncBackend` by default → face/mouth True when video+audio readable). Consent is resolved by the most-recent non-revoked `consent_records` row for the project (single-subject MVP, matching §5 "consent once per subject").

---

### Task C1: Pure lipsync-after-VO planner (`scenes`-free, ffmpeg-free)

**Files:**
- Create `services/local-api/src/laura/ai/auto_pipeline.py`
- Create `services/local-api/tests/test_auto_pipeline_plan.py`

**Interfaces:**
- Produces:
  - `@dataclass(frozen=True) class LipsyncPlan: should_enqueue: bool; reason: str; audio_asset_id: str | None; consent_id: str | None; seq_in_frame: int; seq_out_frame_exclusive: int`
  - `plan_lipsync_after_voiceover(*, probe_face_detected: bool, probe_mouth_visible: bool, consent_id: str | None, audio_asset_id: str, seq_in_frame: int, seq_out_frame_exclusive: int) -> LipsyncPlan` — pure; `should_enqueue=True` iff face AND mouth AND a non-revoked consent_id is present. Reasons: `"ok"`, `"no_face"`, `"no_consent"`.
- Consumes: nothing (pure inputs).

Steps:

- [ ] **Step 1: Write failing test for the planner truth table.**

```python
# services/local-api/tests/test_auto_pipeline_plan.py
"""Pure decision logic for the VO->lipsync auto-coupling (no DB, no ffmpeg)."""

from __future__ import annotations

from laura.ai.auto_pipeline import LipsyncPlan, plan_lipsync_after_voiceover


def _call(**kw: object) -> LipsyncPlan:
    base: dict[str, object] = dict(
        probe_face_detected=True,
        probe_mouth_visible=True,
        consent_id="c1",
        audio_asset_id="a1",
        seq_in_frame=10,
        seq_out_frame_exclusive=40,
    )
    base.update(kw)
    return plan_lipsync_after_voiceover(**base)  # type: ignore[arg-type]


def test_face_and_consent_enqueues_with_bound_span_and_audio() -> None:
    plan = _call()
    assert plan.should_enqueue is True
    assert plan.reason == "ok"
    assert plan.audio_asset_id == "a1"
    assert plan.consent_id == "c1"
    assert plan.seq_in_frame == 10
    assert plan.seq_out_frame_exclusive == 40


def test_no_face_skips_silently() -> None:
    plan = _call(probe_face_detected=False)
    assert plan.should_enqueue is False
    assert plan.reason == "no_face"


def test_no_mouth_skips_silently() -> None:
    plan = _call(probe_mouth_visible=False)
    assert plan.should_enqueue is False
    assert plan.reason == "no_face"


def test_face_but_no_consent_holds_back() -> None:
    plan = _call(consent_id=None)
    assert plan.should_enqueue is False
    assert plan.reason == "no_consent"
```

- [ ] **Step 2: Run it — expect FAIL (module missing).**
  `cd services/local-api && uv run --no-sync pytest tests/test_auto_pipeline_plan.py -q`
  Expected: `ModuleNotFoundError: No module named 'laura.ai.auto_pipeline'`.

- [ ] **Step 3: Minimal implementation.**

```python
# services/local-api/src/laura/ai/auto_pipeline.py
"""Pure decision logic for the transcript-edit auto-pipeline (spec §5).

Keeps the VO->lipsync coupling decision free of DB/ffmpeg so it is fully unit-testable;
the handler does I/O (probe, consent lookup, enqueue) and feeds the booleans in here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LipsyncPlan:
    should_enqueue: bool
    reason: str  # "ok" | "no_face" | "no_consent"
    audio_asset_id: str | None
    consent_id: str | None
    seq_in_frame: int
    seq_out_frame_exclusive: int


def plan_lipsync_after_voiceover(
    *,
    probe_face_detected: bool,
    probe_mouth_visible: bool,
    consent_id: str | None,
    audio_asset_id: str,
    seq_in_frame: int,
    seq_out_frame_exclusive: int,
) -> LipsyncPlan:
    """Decide whether a successful VO should auto-enqueue lipsync.

    Enqueue iff a face+mouth are present in the span AND a valid (non-revoked) consent
    id was resolved. Missing face -> skip silently (only VO stays). Face but no consent
    -> hold back (caller surfaces a one-time hint). The span/audio binding is passed
    through verbatim so the lipsync idempotency key derives from it.
    """
    if not (probe_face_detected and probe_mouth_visible):
        return LipsyncPlan(False, "no_face", None, None, seq_in_frame, seq_out_frame_exclusive)
    if not consent_id:
        return LipsyncPlan(
            False, "no_consent", audio_asset_id, None, seq_in_frame, seq_out_frame_exclusive
        )
    return LipsyncPlan(
        True, "ok", audio_asset_id, consent_id, seq_in_frame, seq_out_frame_exclusive
    )
```

- [ ] **Step 4: Run it — expect PASS (4 passed).**
  `cd services/local-api && uv run --no-sync pytest tests/test_auto_pipeline_plan.py -q`

- [ ] **Step 5: Commit.**
  `git add services/local-api/src/laura/ai/auto_pipeline.py services/local-api/tests/test_auto_pipeline_plan.py`
  `git commit -m "feat(ai): pure planner for VO->lipsync auto-coupling decision"`

---

### Task C2: Consent resolver — most-recent valid consent for a project

**Files:**
- Modify `services/local-api/src/laura/db/repos.py` (add after `list_consent_records`, ~line 1878)
- Create `services/local-api/tests/test_consent_resolve.py`

**Interfaces:**
- Produces: `get_active_consent_id(db: Database, project_id: str) -> str | None` — newest `consent_records` row for the project whose `revoked_at IS NULL`, else `None`.
- Consumes: existing `create_consent_record`, `revoke_consent_record`.

Steps:

- [ ] **Step 1: Write failing test.**

```python
# services/local-api/tests/test_consent_resolve.py
"""get_active_consent_id picks the newest non-revoked consent for the project."""

from __future__ import annotations

from pathlib import Path

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path, start_runner=False).db_path)
    db.migrate()
    return db


def _project(db: SqliteDatabase, tmp_path: Path) -> str:
    p = repos.create_project(db, name="P", workspace_root=str(tmp_path / "ws"))
    return str(p["id"])


def test_none_when_no_consent(tmp_path: Path) -> None:
    db = _db(tmp_path)
    assert repos.get_active_consent_id(db, _project(db, tmp_path)) is None


def test_returns_newest_non_revoked(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db, tmp_path)
    c1 = repos.create_consent_record(db, project_id=pid, subject_label="Me")
    c2 = repos.create_consent_record(db, project_id=pid, subject_label="Me again")
    assert repos.get_active_consent_id(db, pid) == c2["id"]
    repos.revoke_consent_record(db, c2["id"])
    assert repos.get_active_consent_id(db, pid) == c1["id"]


def test_all_revoked_is_none(tmp_path: Path) -> None:
    db = _db(tmp_path)
    pid = _project(db, tmp_path)
    c1 = repos.create_consent_record(db, project_id=pid, subject_label="Me")
    repos.revoke_consent_record(db, c1["id"])
    assert repos.get_active_consent_id(db, pid) is None
```

- [ ] **Step 2: Run it — expect FAIL.**
  `cd services/local-api && uv run --no-sync pytest tests/test_consent_resolve.py -q`
  Expected: `AttributeError: module 'laura.db.repos' has no attribute 'get_active_consent_id'`.

- [ ] **Step 3: Minimal implementation** (insert immediately after `list_consent_records`):

```python
def get_active_consent_id(db: Database, project_id: str) -> str | None:
    """Newest non-revoked consent id for a project, or None.

    Single-subject MVP for the auto-pipeline (spec §5 "consent once per subject"):
    the most recent confirmed-and-not-revoked record is reused by auto-lipsync jobs.
    """
    with db.connection() as conn:
        row = conn.execute(
            "SELECT id FROM consent_records WHERE project_id=? AND revoked_at IS NULL "
            "ORDER BY confirmed_at DESC LIMIT 1",
            (project_id,),
        ).fetchone()
        return str(row["id"]) if row is not None else None
```

- [ ] **Step 4: Run it — expect PASS (3 passed).**
  `cd services/local-api && uv run --no-sync pytest tests/test_consent_resolve.py -q`

- [ ] **Step 5: Commit.**
  `git add services/local-api/src/laura/db/repos.py services/local-api/tests/test_consent_resolve.py`
  `git commit -m "feat(db): get_active_consent_id resolver for auto-lipsync reuse"`

---

### Task C3: Wire the completion hook into `handle_voiceover`

**Files:**
- Modify `services/local-api/src/laura/ai/handlers.py` (imports ~line 21–24; `handle_voiceover` return path ~line 436–442)
- Create `services/local-api/tests/test_voiceover_lipsync_hook.py`

**Interfaces:**
- Consumes: `plan_lipsync_after_voiceover` (C1), `repos.get_active_consent_id` (C2), `resolve_lipsync_backend(...).probe(...)`, `render_clips_mp4`, `flatten_sequence`/`repos.list_timeline_clips`, `idempotency_key_for("ai.lipsync", ...)`, `enqueue`, `queue_for`.
- Produces: `handle_voiceover` result gains keys `lipsync_job_id: str | None` and `lipsync_skip_reason: str | None`; on plan `should_enqueue` it enqueues `ai.lipsync` with `audio_asset_id` = the new VO asset, the VO span, resolved `consent_id`, `license_accepted=True`, and the standard idempotency key.

Steps:

- [ ] **Step 1: Write failing integration test** (mirrors `test_lipsync_job.py` fixture style; uses stub backends, real ffmpeg for the base clip).

```python
# services/local-api/tests/test_voiceover_lipsync_hook.py
"""handle_voiceover success path conditionally enqueues ai.lipsync (spec §5)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ingest.ffmpeg import run_ffmpeg
from laura.jobs import JobContext, enqueue
from laura.jobs.queues import queue_for

_FFMPEG = os.environ.get("LAURA_FFMPEG", "ffmpeg")
pytestmark = pytest.mark.skipif(shutil.which(_FFMPEG) is None, reason="ffmpeg not on PATH")


def _setup(tmp_path: Path) -> tuple[SqliteDatabase, dict[str, Any], dict[str, Any]]:
    ws = tmp_path / "ws"
    (ws / "project").mkdir(parents=True, exist_ok=True)
    video = tmp_path / "base.mp4"
    run_ffmpeg([
        "-f", "lavfi", "-i", "color=c=green:s=320x240:r=30:d=2",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:d=2",
        "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(video),
    ])
    db = SqliteDatabase(Settings(workspace_root=ws, start_runner=False).db_path)
    db.migrate()
    project = repos.create_project(db, name="P", workspace_root=str(ws / "project"))
    asset = repos.create_asset(db, project_id=project["id"], type="video",
                               display_name="base", source_path=str(video))
    repos.update_asset_probe(db, asset["id"], type="video", duration_frames=60,
                             rate_num=30, rate_den=1, audio_sample_rate=48000,
                             start_timecode=None, width=320, height=240,
                             codec_video="h264", codec_audio="aac", is_vfr=False, sha256=None)
    tl = repos.create_timeline(db, project_id=project["id"], kind="rough_cut", name="RC")
    repos.add_timeline_clip(db, timeline_id=tl["id"], asset_id=asset["id"],
                            src_in_frame=0, src_out_frame_exclusive=60,
                            seq_in_frame=0, seq_out_frame_exclusive=60, lane=0)
    return db, project, tl


def _ctx(db: SqliteDatabase, tl_id: str, project_id: str) -> JobContext:
    job_id = enqueue(db, queue=queue_for("ai.voiceover"), kind="ai.voiceover", payload={
        "timeline_id": tl_id, "text": "hallo welt",
        "seq_in_frame": 10, "seq_out_frame_exclusive": 40,
        "mix_mode": "replace_original", "ducking_percent": 0, "backend": "stub",
    })
    return JobContext(job_id=job_id, kind="ai.voiceover",
                      queue=queue_for("ai.voiceover"),
                      payload={"timeline_id": tl_id, "text": "hallo welt",
                               "seq_in_frame": 10, "seq_out_frame_exclusive": 40,
                               "mix_mode": "replace_original", "ducking_percent": 0,
                               "backend": "stub"}, db=db)


def test_face_plus_consent_enqueues_lipsync(tmp_path: Path) -> None:
    from laura.ai.handlers import handle_voiceover

    db, project, tl = _setup(tmp_path)
    repos.create_consent_record(db, project_id=project["id"], subject_label="Me")
    result = handle_voiceover(_ctx(db, tl["id"], project["id"]))
    assert result["lipsync_job_id"] is not None
    queued = repos.get_job(db, str(result["lipsync_job_id"]))
    assert queued is not None and queued["kind"] == "ai.lipsync"


def test_face_but_no_consent_skips_lipsync(tmp_path: Path) -> None:
    from laura.ai.handlers import handle_voiceover

    db, project, tl = _setup(tmp_path)
    result = handle_voiceover(_ctx(db, tl["id"], project["id"]))
    assert result["lipsync_job_id"] is None
    assert result["lipsync_skip_reason"] == "no_consent"
```

- [ ] **Step 2: Run it — expect FAIL.**
  `cd services/local-api && uv run --no-sync pytest tests/test_voiceover_lipsync_hook.py -q`
  Expected: `KeyError: 'lipsync_job_id'`.

- [ ] **Step 3: Implement the hook.** Add imports near the top of `handlers.py` (after line 24):

```python
from ..jobs.keys import idempotency_key_for
from ..jobs.queues import queue_for
from ..jobs.runner import enqueue
from .auto_pipeline import plan_lipsync_after_voiceover
```

Add a private helper above `handle_voiceover` (after line 298) that does the I/O and returns `(job_id, skip_reason)`:

```python
def _maybe_enqueue_lipsync_after_vo(
    ctx: JobContext,
    *,
    timeline: dict[str, Any],
    project: dict[str, Any],
    vo_asset_id: str,
    seq_in: int,
    seq_out: int,
) -> tuple[str | None, str | None]:
    """Probe the VO span for a face and, if consent exists, enqueue ai.lipsync (spec §5).

    No face / sidecar absent / no consent -> (None, reason); never raises into the VO
    success path (a lipsync hiccup must not fail the VO that already rendered).
    """
    try:
        backend = resolve_lipsync_backend(None)
        if not backend.available():
            return None, "no_backend"
        rate_num = int(project["sequence_rate_num"])
        rate_den = int(project["sequence_rate_den"])
        if timeline.get("kind") == "sequence":
            base_rows = flatten_sequence(ctx.db, timeline["id"])
        else:
            base_rows = [
                r for r in repos.list_timeline_clips(ctx.db, timeline["id"])
                if r.get("role", "base") != "replace" and int(r.get("lane") or 0) == 0
            ]
        clips: list[tuple[Path, int, int]] = []
        for row in base_rows:
            r_in, r_out = int(row["seq_in_frame"]), int(row["seq_out_frame_exclusive"])
            o_in, o_out = max(r_in, seq_in), min(r_out, seq_out)
            if o_in >= o_out:
                continue
            asset = repos.get_asset(ctx.db, row["asset_id"])
            if asset is None:
                continue
            base = int(row["src_in_frame"])
            clips.append((Path(asset["source_path"]), base + (o_in - r_in), base + (o_out - r_in)))
        if not clips:
            return None, "no_face"
        tmp_dir = Path(project["workspace_root"]) / "tmp"
        tmp_dir.mkdir(parents=True, exist_ok=True)
        driving = tmp_dir / f"{new_id()}.vo-probe.mp4"
        vo_asset = repos.get_asset(ctx.db, vo_asset_id)
        audio_path = Path(vo_asset["source_path"]) if vo_asset else None
        try:
            render_clips_mp4(clips, driving, rate_num=rate_num, rate_den=rate_den)
            probe = backend.probe(video_path=driving, audio_path=audio_path or driving)
        finally:
            driving.unlink(missing_ok=True)
        consent_id = repos.get_active_consent_id(ctx.db, project["id"])
        plan = plan_lipsync_after_voiceover(
            probe_face_detected=bool(getattr(probe, "face_detected")),
            probe_mouth_visible=bool(getattr(probe, "mouth_visible")),
            consent_id=consent_id,
            audio_asset_id=vo_asset_id,
            seq_in_frame=seq_in,
            seq_out_frame_exclusive=seq_out,
        )
        if not plan.should_enqueue:
            return None, plan.reason
        payload: dict[str, Any] = {
            "timeline_id": timeline["id"],
            "seq_in_frame": plan.seq_in_frame,
            "seq_out_frame_exclusive": plan.seq_out_frame_exclusive,
            "audio_asset_id": plan.audio_asset_id,
            "consent_id": plan.consent_id,
            "license_accepted": True,
        }
        job_id = enqueue(
            ctx.db,
            queue=queue_for("ai.lipsync", default="ai"),
            kind="ai.lipsync",
            payload=payload,
            max_attempts=2,
            idempotency_key=idempotency_key_for("ai.lipsync", payload),
            caused_by_job_id=ctx.job_id,
        )
        return job_id, "ok"
    except Exception:  # noqa: BLE001 - VO already succeeded; lipsync is best-effort
        return None, "probe_error"
```

Then change the `handle_voiceover` return (lines 436–442) to call it and extend the dict:

```python
    lipsync_job_id, lipsync_skip_reason = _maybe_enqueue_lipsync_after_vo(
        ctx,
        timeline=timeline,
        project=project,
        vo_asset_id=asset["id"],
        seq_in=seq_in,
        seq_out=seq_out,
    )
    return {
        "asset_id": asset["id"],
        "audio_clip_id": clip["id"],
        "out_path": str(out_path),
        "seq_in_frame": seq_in,
        "seq_out_frame_exclusive": seq_out,
        "lipsync_job_id": lipsync_job_id,
        "lipsync_skip_reason": lipsync_skip_reason,
    }
```

- [ ] **Step 4: Run it — expect PASS (2 passed).**
  `cd services/local-api && uv run --no-sync pytest tests/test_voiceover_lipsync_hook.py tests/test_voiceover.py -q`
  (Re-run `test_voiceover.py` to confirm the extended result dict didn't break existing assertions.)

- [ ] **Step 5: Commit.**
  `git add services/local-api/src/laura/ai/handlers.py services/local-api/tests/test_voiceover_lipsync_hook.py`
  `git commit -m "feat(ai): handle_voiceover success enqueues probe+consent-gated lipsync"`

---

### Task C4: (entfaellt — `cutAtFrame`-Client ist bereits Task A4)

> **Uebersprungen / merged.** Der `cutAtFrame`-API-Client wird in **Task A4** erstellt; die Transkript-Span-Ersatz-Commit-Logik liegt in **Task C5** (`replaceSpanText`). Dieser Task hat keinen eigenen Inhalt — im Ledger als "merged into A4 + C5" abhaken, nicht erneut implementieren.

---

### Task C5: `replaceSpanText` commit logic in `useRoughCutTranscript` (debounced auto-VO)

**Files:**
- Modify `apps/desktop/src/hooks/useRoughCutTranscript.ts` (NEW in Phase A; this task adds the `replaceSpanText` method body + a pure helper)
- Create `apps/desktop/src/shared/spanReplaceCommit.ts`
- Create `apps/desktop/src/shared/spanReplaceCommit.test.ts`

**Interfaces:**
- Produces (pure, testable): `buildVoiceoverCommit(args: { startWordId: string; endWordId: string; newText: string; voiceId: string | null; words: ProjectedWord[] }): { seqIn: number; seqOut: number; text: string; voiceId?: string; mixMode: "replace_original"; duckingPercent: 0 } | null` — maps a word span to the VO request (original audio out). Returns `null` when text is unchanged/blank or span invalid.
- Consumes: `ProjectedWord` (`{ id; seq_in_frame; seq_out_frame_exclusive; text }`) from `transcriptProjection`; `useRoughCutTranscript.replaceSpanText(startWordId,endWordId,newText,voiceId)` (signature from Phase A contract); `client.createVoiceover` (C-reused).

Steps:

- [ ] **Step 1: Write failing test for the pure commit builder.**

```ts
// apps/desktop/src/shared/spanReplaceCommit.test.ts
import { describe, expect, it } from "vitest";
import { buildVoiceoverCommit } from "./spanReplaceCommit";

const words = [
  { id: "w1", seq_in_frame: 10, seq_out_frame_exclusive: 20, text: "alt" },
  { id: "w2", seq_in_frame: 20, seq_out_frame_exclusive: 40, text: "text" },
];

describe("buildVoiceoverCommit", () => {
  it("maps span to a replace_original VO request, ducking 0", () => {
    const out = buildVoiceoverCommit({
      startWordId: "w1", endWordId: "w2", newText: "neuer text", voiceId: "Hedda", words,
    });
    expect(out).toEqual({
      seqIn: 10, seqOut: 40, text: "neuer text",
      voiceId: "Hedda", mixMode: "replace_original", duckingPercent: 0,
    });
  });

  it("returns null when text is blank", () => {
    expect(buildVoiceoverCommit({
      startWordId: "w1", endWordId: "w2", newText: "   ", voiceId: null, words,
    })).toBeNull();
  });

  it("returns null when the span words are missing", () => {
    expect(buildVoiceoverCommit({
      startWordId: "wX", endWordId: "w2", newText: "x", voiceId: null, words,
    })).toBeNull();
  });

  it("omits voiceId when null (backend uses default voice)", () => {
    const out = buildVoiceoverCommit({
      startWordId: "w1", endWordId: "w2", newText: "x", voiceId: null, words,
    });
    expect(out).not.toBeNull();
    expect(out && "voiceId" in out).toBe(false);
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.**
  `cd apps/desktop && npx vitest run src/shared/spanReplaceCommit.test.ts`
  Expected: `Failed to resolve import "./spanReplaceCommit"`.

- [ ] **Step 3: Implement the pure builder.**

```ts
// apps/desktop/src/shared/spanReplaceCommit.ts
export interface ProjectedWord {
  id: string;
  seq_in_frame: number;
  seq_out_frame_exclusive: number;
  text: string;
}

export interface VoiceoverCommit {
  seqIn: number;
  seqOut: number;
  text: string;
  voiceId?: string;
  mixMode: "replace_original";
  duckingPercent: 0;
}

/**
 * Map a transcript word-span text edit to a VO request: original audio out
 * (mix_mode=replace_original, ducking 0). Pure; the hook handles debounce + fetch.
 * Returns null for an empty/blank edit or an unresolvable span (no enqueue).
 */
export function buildVoiceoverCommit(args: {
  startWordId: string;
  endWordId: string;
  newText: string;
  voiceId: string | null;
  words: ProjectedWord[];
}): VoiceoverCommit | null {
  const trimmed = args.newText.trim();
  if (trimmed === "") return null;
  const byId = new Map(args.words.map((w) => [w.id, w] as const));
  const start = byId.get(args.startWordId);
  const end = byId.get(args.endWordId);
  if (start === undefined || end === undefined) return null;
  const seqIn = Math.min(start.seq_in_frame, end.seq_in_frame);
  const seqOut = Math.max(start.seq_out_frame_exclusive, end.seq_out_frame_exclusive);
  if (seqOut <= seqIn) return null;
  const base: VoiceoverCommit = {
    seqIn,
    seqOut,
    text: trimmed,
    mixMode: "replace_original",
    duckingPercent: 0,
  };
  return args.voiceId ? { ...base, voiceId: args.voiceId } : base;
}
```

In `useRoughCutTranscript.ts`, implement `replaceSpanText` to call `buildVoiceoverCommit(...)`, and on a non-null result call `client.createVoiceover(timelineId, commit)` (the debounce timer lives in the component/hook; the per-keystroke vs commit distinction is the caller's — this builder runs on commit only).

- [ ] **Step 4: Run it — expect PASS (4 passed).**
  `cd apps/desktop && npx vitest run src/shared/spanReplaceCommit.test.ts`

- [ ] **Step 5: Commit.**
  `git add apps/desktop/src/shared/spanReplaceCommit.ts apps/desktop/src/shared/spanReplaceCommit.test.ts apps/desktop/src/hooks/useRoughCutTranscript.ts`
  `git commit -m "feat(ui): replaceSpanText commit -> replace_original VO request builder"`

---

### Task C6: Auto-MARK same-source jump-cut + one-tap smooth helper (no silent apply)

**Files:**
- Create `apps/desktop/src/shared/smoothEdge.ts`
- Create `apps/desktop/src/shared/smoothEdge.test.ts`

**Interfaces:**
- Produces:
  - `findSameSourceEdge(clips: TimelineClip[], atSeqFrame: number): BoundaryIdentity | null` — the lane-0 boundary at `atSeqFrame` that is a contiguous same-source jump-cut (asset_a==asset_b AND src_in_b==src_out_a), else null. Mirrors backend `same_source`.
  - `crossfadeFix(frames?: number): SuggestedFix` — default `{ kind: "transition", transition_style: "crossfade", transition_frames: 6 }`. (One-tap smooth payload for `client.applyTransitionFix`.)
- Consumes: `TimelineClip`, `BoundaryIdentity`, `SuggestedFix` from `api.ts`; smoothing is applied only on explicit user tap via the existing `applyTransitionFix` (NOT auto-applied — spec §8).

Steps:

- [ ] **Step 1: Write failing test.**

```ts
// apps/desktop/src/shared/smoothEdge.test.ts
import { describe, expect, it } from "vitest";
import { crossfadeFix, findSameSourceEdge } from "./smoothEdge";

const clip = (o: Partial<Record<string, unknown>>) => ({
  id: "x", asset_id: "A", lane: 0,
  src_in_frame: 0, src_out_frame_exclusive: 10,
  seq_in_frame: 0, seq_out_frame_exclusive: 10, ...o,
}) as never;

describe("findSameSourceEdge", () => {
  it("flags a contiguous same-source jump-cut at the boundary", () => {
    const clips = [
      clip({ id: "a", asset_id: "A", src_in_frame: 0, src_out_frame_exclusive: 10,
             seq_in_frame: 0, seq_out_frame_exclusive: 10 }),
      clip({ id: "b", asset_id: "A", src_in_frame: 10, src_out_frame_exclusive: 25,
             seq_in_frame: 10, seq_out_frame_exclusive: 25 }),
    ];
    const id = findSameSourceEdge(clips, 10);
    expect(id).toEqual({ asset_a: "A", asset_b: "A", src_out_a: 10, src_in_b: 10 });
  });

  it("returns null across distinct assets (clean cut)", () => {
    const clips = [
      clip({ id: "a", asset_id: "A", src_out_frame_exclusive: 10, seq_out_frame_exclusive: 10 }),
      clip({ id: "b", asset_id: "B", src_in_frame: 0, src_out_frame_exclusive: 15,
             seq_in_frame: 10, seq_out_frame_exclusive: 25 }),
    ];
    expect(findSameSourceEdge(clips, 10)).toBeNull();
  });

  it("returns null when no boundary sits at the frame", () => {
    const clips = [clip({ id: "a", asset_id: "A" })];
    expect(findSameSourceEdge(clips, 10)).toBeNull();
  });
});

describe("crossfadeFix", () => {
  it("defaults to a 6-frame crossfade", () => {
    expect(crossfadeFix()).toEqual({
      kind: "transition", transition_style: "crossfade", transition_frames: 6,
    });
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.**
  `cd apps/desktop && npx vitest run src/shared/smoothEdge.test.ts`
  Expected: `Failed to resolve import "./smoothEdge"`.

- [ ] **Step 3: Implement.**

```ts
// apps/desktop/src/shared/smoothEdge.ts
import type { BoundaryIdentity, SuggestedFix, TimelineClip } from "../api";

/**
 * The lane-0 boundary exactly at `atSeqFrame` that is a contiguous same-source jump
 * (asset_a==asset_b AND src_in_b==src_out_a) — the canonical dead-air cut a delete
 * produces. Mirrors transition_review's `same_source`. Caller MARKS it and offers a
 * one-tap smooth; it is never applied silently (spec §8). Null when no such edge.
 */
export function findSameSourceEdge(
  clips: TimelineClip[],
  atSeqFrame: number,
): BoundaryIdentity | null {
  const lane0 = clips
    .filter((c) => (c.lane ?? 0) === 0)
    .slice()
    .sort((a, b) => a.seq_in_frame - b.seq_in_frame);
  for (let i = 0; i < lane0.length - 1; i += 1) {
    const a = lane0[i]!;
    const b = lane0[i + 1]!;
    if (a.seq_out_frame_exclusive !== atSeqFrame || b.seq_in_frame !== atSeqFrame) continue;
    if (a.asset_id === b.asset_id && b.src_in_frame === a.src_out_frame_exclusive) {
      return {
        asset_a: a.asset_id,
        asset_b: b.asset_id,
        src_out_a: a.src_out_frame_exclusive,
        src_in_b: b.src_in_frame,
      };
    }
  }
  return null;
}

/** One-tap smooth payload: a short crossfade (spec §8 default). */
export function crossfadeFix(frames = 6): SuggestedFix {
  return { kind: "transition", transition_style: "crossfade", transition_frames: frames };
}
```

(Verify the exact field names of `BoundaryIdentity`/`SuggestedFix` in `api.ts` — they back the existing `applyTransitionFix` call (~line 1195) and the backend `apply_fix` `identity`/`fix` dataclasses; match them verbatim.)

- [ ] **Step 4: Run it — expect PASS (4 passed).**
  `cd apps/desktop && npx vitest run src/shared/smoothEdge.test.ts`

- [ ] **Step 5: Commit.**
  `git add apps/desktop/src/shared/smoothEdge.ts apps/desktop/src/shared/smoothEdge.test.ts`
  `git commit -m "feat(ui): same-source jump-cut detection + one-tap crossfade smooth helper"`

---

### Task C7: `EditorialToolsBar` — voice picker, smooth action, manual Reenact, disclosure

**Files:**
- Create `apps/desktop/src/components/EditorialToolsBar.tsx`
- Create `apps/desktop/src/components/EditorialToolsBar.test.tsx`

**Interfaces:**
- Produces: `EditorialToolsBar` (named export). Props:
  ```ts
  interface EditorialToolsBarProps {
    voices: VoiceoverVoice[];
    voiceId: string | null;
    onVoiceChange(voiceId: string | null): void;
    pendingEdge: BoundaryIdentity | null;   // set when a same-source jump-cut was auto-marked
    onSmooth(): void;                        // applies crossfadeFix via client.applyTransitionFix
    onReenact(): void;                       // manual creative action (NOT auto)
    syntheticEffects: string[];             // e.g. ["Stimme","Lippensync"]; drives the always-on line
    busy?: boolean;
  }
  ```
  Renders: voice `<select>` (Auto + each voice), a "Übergang glätten" button enabled only when `pendingEdge !== null`, a "Reenact" button, and an always-on disclosure line "Enthält synthetische Inhalte: {effects}". No VO/lipsync toggle (those are automatic).
- Consumes: `VoiceoverVoice`, `BoundaryIdentity` from `api.ts`; reuses `listVoiceoverVoices` data fetched by the parent.

Steps:

- [ ] **Step 1: Write failing test** (React Testing Library; the project already uses vitest + RTL — confirm via an existing `*.test.tsx`).

```tsx
// apps/desktop/src/components/EditorialToolsBar.test.tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { EditorialToolsBar } from "./EditorialToolsBar";

const voices = [{ name: "Hedda", culture: "de-DE", gender: "Female" }];

describe("EditorialToolsBar", () => {
  it("shows the always-on synthetic-content disclosure with effects", () => {
    render(
      <EditorialToolsBar
        voices={voices} voiceId={null} onVoiceChange={() => {}}
        pendingEdge={null} onSmooth={() => {}} onReenact={() => {}}
        syntheticEffects={["Stimme", "Lippensync"]}
      />,
    );
    expect(screen.getByText(/Enthält synthetische Inhalte/i)).toHaveTextContent("Stimme");
    expect(screen.getByText(/Enthält synthetische Inhalte/i)).toHaveTextContent("Lippensync");
  });

  it("disables smooth until an edge is marked, enables + fires when present", () => {
    const onSmooth = vi.fn();
    const { rerender } = render(
      <EditorialToolsBar
        voices={voices} voiceId={null} onVoiceChange={() => {}}
        pendingEdge={null} onSmooth={onSmooth} onReenact={() => {}}
        syntheticEffects={[]}
      />,
    );
    expect(screen.getByRole("button", { name: /glätten/i })).toBeDisabled();
    rerender(
      <EditorialToolsBar
        voices={voices} voiceId={null} onVoiceChange={() => {}}
        pendingEdge={{ asset_a: "A", asset_b: "A", src_out_a: 10, src_in_b: 10 }}
        onSmooth={onSmooth} onReenact={() => {}} syntheticEffects={[]}
      />,
    );
    const btn = screen.getByRole("button", { name: /glätten/i });
    expect(btn).not.toBeDisabled();
    fireEvent.click(btn);
    expect(onSmooth).toHaveBeenCalledTimes(1);
  });

  it("fires onVoiceChange with the picked voice (null for Auto)", () => {
    const onVoiceChange = vi.fn();
    render(
      <EditorialToolsBar
        voices={voices} voiceId={null} onVoiceChange={onVoiceChange}
        pendingEdge={null} onSmooth={() => {}} onReenact={() => {}}
        syntheticEffects={[]}
      />,
    );
    fireEvent.change(screen.getByLabelText(/Stimme/i), { target: { value: "Hedda" } });
    expect(onVoiceChange).toHaveBeenCalledWith("Hedda");
  });
});
```

- [ ] **Step 2: Run it — expect FAIL.**
  `cd apps/desktop && npx vitest run src/components/EditorialToolsBar.test.tsx`
  Expected: `Failed to resolve import "./EditorialToolsBar"`.

- [ ] **Step 3: Implement.**

```tsx
// apps/desktop/src/components/EditorialToolsBar.tsx
import type { BoundaryIdentity, VoiceoverVoice } from "../api";

export interface EditorialToolsBarProps {
  voices: VoiceoverVoice[];
  voiceId: string | null;
  onVoiceChange(voiceId: string | null): void;
  pendingEdge: BoundaryIdentity | null;
  onSmooth(): void;
  onReenact(): void;
  syntheticEffects: string[];
  busy?: boolean;
}

/**
 * Compact strip under the player (spec §10): the ONLY explicit choice is the voice.
 * VO + lipsync happen automatically on a transcript edit (no toggles here). Smooth is
 * offered one-tap only when a same-source jump-cut was auto-marked; Reenact is a manual
 * creative action. The synthetic-content disclosure line is always on (spec §7).
 */
export function EditorialToolsBar({
  voices,
  voiceId,
  onVoiceChange,
  pendingEdge,
  onSmooth,
  onReenact,
  syntheticEffects,
  busy = false,
}: EditorialToolsBarProps): JSX.Element {
  return (
    <div className="flex flex-col gap-1 border-y border-bezel/80 px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label className="flex items-center gap-1 text-content-muted">
          <span className="uppercase tracking-wide text-[10px]">Stimme</span>
          <select
            aria-label="Stimme"
            value={voiceId ?? ""}
            disabled={busy}
            onChange={(e) => onVoiceChange(e.target.value === "" ? null : e.target.value)}
            className="rounded border border-bezel bg-surface-1 px-1 py-1 text-[11px] text-content-strong disabled:opacity-40"
          >
            <option value="">Auto</option>
            {voices.map((v) => (
              <option key={v.name} value={v.name}>{`${v.name} (${v.culture})`}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={onSmooth}
          disabled={busy || pendingEdge === null}
          title="Markierte Schnittkante mit einer kurzen Blende glätten"
          className="rounded bg-sky-700 px-3 py-1 text-[11px] font-medium text-white hover:bg-sky-600 disabled:opacity-40"
        >
          Übergang glätten
        </button>
        <button
          type="button"
          onClick={onReenact}
          disabled={busy}
          className="rounded border border-bezel bg-surface-1 px-3 py-1 text-[11px] text-content-strong hover:border-sky-600 disabled:opacity-40"
        >
          Reenact
        </button>
      </div>
      <div className="text-[10px] text-content-faint">
        Enthält synthetische Inhalte
        {syntheticEffects.length > 0 ? `: ${syntheticEffects.join(", ")}` : ""}
      </div>
    </div>
  );
}
```

(If the project's JSX return type convention is `React.ReactElement` rather than `JSX.Element`, match the surrounding components — check `AssembleView.tsx`.)

- [ ] **Step 4: Run it — expect PASS (3 passed).**
  `cd apps/desktop && npx vitest run src/components/EditorialToolsBar.test.tsx`

- [ ] **Step 5: Commit.**
  `git add apps/desktop/src/components/EditorialToolsBar.tsx apps/desktop/src/components/EditorialToolsBar.test.tsx`
  `git commit -m "feat(ui): EditorialToolsBar (voice picker, one-tap smooth, manual Reenact, disclosure)"`

---

### Task C8: Green verification — Phase C touched files

**Files:** (verify only; no edits)
- Backend: `ai/auto_pipeline.py`, `ai/handlers.py`, `db/repos.py` + tests `test_auto_pipeline_plan.py`, `test_consent_resolve.py`, `test_voiceover_lipsync_hook.py`
- Frontend: `api.ts`, `hooks/useRoughCutTranscript.ts`, `shared/spanReplaceCommit.ts`, `shared/smoothEdge.ts`, `components/EditorialToolsBar.tsx` + their `*.test.ts(x)`

Steps:

- [ ] **Step 1: Backend lint + format check (touched files).**
  `cd services/local-api && uv run --no-sync ruff check src/laura/ai/auto_pipeline.py src/laura/ai/handlers.py src/laura/db/repos.py tests/test_auto_pipeline_plan.py tests/test_consent_resolve.py tests/test_voiceover_lipsync_hook.py`
  Expected: `All checks passed!`

- [ ] **Step 2: Backend types (mypy).**
  `cd services/local-api && uv run --no-sync mypy src/laura/ai/auto_pipeline.py src/laura/ai/handlers.py src/laura/db/repos.py`
  Expected: `Success: no issues found in 3 source files` (CI-env: `ctranslate2` absent is tolerated per repo lessons).

- [ ] **Step 3: Backend tests (Phase C + regression on touched VO/lipsync suites).**
  `cd services/local-api && uv run --no-sync pytest tests/test_auto_pipeline_plan.py tests/test_consent_resolve.py tests/test_voiceover_lipsync_hook.py tests/test_voiceover.py tests/test_lipsync_job.py -q`
  Expected: all pass (lipsync/VO ffmpeg suites skip cleanly if `ffmpeg` is absent in CI minimal extras).

- [ ] **Step 4: Frontend typecheck.**
  `cd apps/desktop && npx tsc --noEmit`
  Expected: no output (exit 0); strict mode, no `any`.

- [ ] **Step 5: Frontend tests (Phase C suites).**
  `cd apps/desktop && npx vitest run src/api.cutAtFrame.test.ts src/shared/spanReplaceCommit.test.ts src/shared/smoothEdge.test.ts src/components/EditorialToolsBar.test.tsx`
  Expected: `Test Files 4 passed`, all assertions green.

- [ ] **Step 6: Mark the UI-integration acceptance as manual.**
  Manual (headless nicht prüfbar, live CDP 9222): edit a transcript span → confirm (debounced) → new voice becomes audible in the preview (Phase B mixer), original audio under the span is gone; if a face is present + consent exists, a lipsync overlay appears after the job finishes (inline progress); if no face/sidecar, only VO (no error); a delete that creates a same-source jump-cut auto-marks the edge and the "Übergang glätten" button enables (applies a crossfade only on tap, never silently). Verify the synthetic-content disclosure line is always visible while editing.

- [ ] **Step 7: Commit (verification snapshot only if any nit-fix was needed; otherwise no-op).**
  `git add services/local-api/src/laura/ai/handlers.py apps/desktop/src/api.ts`
  `git commit -m "test(phase-c): verify auto-pipeline (VO->lipsync hook, smooth, tools bar) green"`

---

**Files produced/modified in Phase C (all absolute):**
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\ai\auto_pipeline.py` (new)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\ai\handlers.py` (modify: imports + `handle_voiceover` success hook)
- `C:\Users\User\Desktop\Laura\services\local-api\src\laura\db\repos.py` (modify: `get_active_consent_id`)
- `C:\Users\User\Desktop\Laura\services\local-api\tests\test_auto_pipeline_plan.py`, `test_consent_resolve.py`, `test_voiceover_lipsync_hook.py` (new)
- `C:\Users\User\Desktop\Laura\apps\desktop\src\api.ts` (modify: `cutAtFrame`)
- `C:\Users\User\Desktop\Laura\apps\desktop\src\hooks\useRoughCutTranscript.ts` (modify: `replaceSpanText` body — file created in Phase A)
- `C:\Users\User\Desktop\Laura\apps\desktop\src\shared\spanReplaceCommit.ts`, `smoothEdge.ts` (new) + their tests
- `C:\Users\User\Desktop\Laura\apps\desktop\src\components\EditorialToolsBar.tsx` (new) + test

---

## Phase D — Compliance immer-an

Diese Phase macht den EU-Act/Synthetik-Hinweis zur Pflicht, schreibt `audit_events` bei jedem AI-Job-Erfolg und jedem Export-Render, und gibt dem Nutzer einen minimalen „Das bin ich"-Consent-Inspektor. Die Datenmodelle (`audit_events`, `consent_records` inkl. `revoked_at`), die Repos (`insert_audit_event`, `create/list/revoke_consent_record`, `set_asset_synthetic`), der `audit.record(...)`-Helper und die Consent-CRUD-Endpoints (`POST/GET/POST …/revoke` in `reenact.py`) existieren bereits — diese Phase **verdrahtet** sie. Neu sind nur: ein geteilter System-Principal-Helper für Jobs, die `audit.record`-Aufrufe in den vier Erfolgspfaden, die Disclosure-Präsenz-Erzwingung im Reel-Render, zwei fehlende `api.ts`-Methoden (`listConsent`, `revokeConsent`), die Disclosure-Read-only-Bestätigung in `ExportView`, der Consent-Inspektor + die immer-an-Disclosure-Zeile im `EditorialToolsBar` (aus Phase C).

---

### Task D1: System-Principal-Helper für Job-Audit + audit.record in den drei AI-Erfolgspfaden

`JobContext` trägt **keinen** Principal (nur `db`, siehe `jobs/runner.py:42-50`). AI-Jobs laufen serverseitig ohne Request-Principal, also brauchen wir einen deterministischen System-Principal für den Audit-Eintrag. Diesen legen wir einmal in `audit.py` ab und nutzen ihn in `handle_voiceover`/`handle_lipsync`/`handle_reenact`.

**Files:**
- Modify `services/local-api/src/laura/audit.py` (add `system_principal()` after `record`, line ~33)
- Modify `services/local-api/src/laura/ai/handlers.py` (import audit at top ~line 14; add `audit.record(...)` before each handler `return`: voiceover ~436, reenact ~285, lipsync ~652)
- Create `services/local-api/tests/test_ai_audit.py`

**Interfaces:**
- Produces: `audit.system_principal() -> Principal` (`kind="local"`, `role="owner"`, `user_id=None`, `org_id=None`).
- Consumes (existing, verbatim): `audit.record(db, principal, action, *, entity_type, entity_id, payload)`; handler return dicts carry `asset_id`, `seq_in_frame`, `seq_out_frame_exclusive` (voiceover also `audio_clip_id`; lipsync also `audio_asset_id`).
- Produces (contract): `audit.record(...)` called on success of voiceover/lipsync/reenact jobs (action = `"ai.voiceover"` / `"ai.lipsync"` / `"ai.reenact"`, `entity_type="media_asset"`, `entity_id=<new asset_id>`).

- [ ] **Step 1: Write failing test (`test_ai_audit.py`).** Drive each handler through its stub backend and assert exactly one matching audit row lands. Real test code:
  ```python
  from __future__ import annotations

  from laura.ai import handlers
  from laura.db import repos
  from laura.jobs.runner import JobContext


  def _ctx(db, kind: str, payload: dict) -> JobContext:
      return JobContext(job_id="j1", kind=kind, queue="ai", payload=payload, db=db)


  def test_voiceover_success_writes_audit_event(seeded_timeline) -> None:
      db, timeline_id, segment_id = seeded_timeline
      handlers.handle_voiceover(
          _ctx(db, "ai.voiceover", {
              "timeline_id": timeline_id,
              "segment_id": segment_id,
              "text": "hallo welt",
              "backend": "stub",
          })
      )
      events = repos.list_audit_events(db, limit=100)
      vo = [e for e in events if e["action"] == "ai.voiceover"]
      assert len(vo) == 1
      assert vo[0]["entity_type"] == "media_asset"
      assert vo[0]["entity_id"]  # the new VO asset id
  ```
  (`seeded_timeline` reuses the fixture the existing `tests/test_voiceover*.py` already build a timeline+segment with — copy its arrange block into a `conftest.py` fixture if not already shared; show that fixture inline in the PR if you add it.)

- [ ] **Step 2: Run it — expect FAIL.**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_ai_audit.py -q
  ```
  Expected: `AttributeError: module 'laura.audit' has no attribute 'system_principal'` (or, once that exists, `assert len(vo) == 1` fails with `0`).

- [ ] **Step 3: Add `system_principal()` to `audit.py`.** Real code (append after `record`):
  ```python
  def system_principal() -> Principal:
      """The principal recorded for server-side automation (job handlers).

      Jobs run without a request principal; audit rows still need a stable
      actor. This is the local owner identity, matching the default request
      principal in auth/deps.py.
      """
      return Principal(kind="local", role="owner")
  ```

- [ ] **Step 4: Call `audit.record` in each AI success path.** Import at top of `handlers.py` (after line 14): `from .. import audit`. Then immediately before `handle_voiceover`'s `return` (~436):
  ```python
  audit.record(
      ctx.db,
      audit.system_principal(),
      "ai.voiceover",
      entity_type="media_asset",
      entity_id=asset["id"],
      payload={
          "timeline_id": timeline["id"],
          "audio_clip_id": clip["id"],
          "seq_in_frame": seq_in,
          "seq_out_frame_exclusive": seq_out,
      },
  )
  ```
  Before `handle_reenact`'s `return` (~285):
  ```python
  audit.record(
      ctx.db,
      audit.system_principal(),
      "ai.reenact",
      entity_type="media_asset",
      entity_id=asset["id"],
      payload={
          "timeline_id": tl["id"],
          "consent_id": consent_id,
          "seq_in_frame": seq_in,
          "seq_out_frame_exclusive": seq_out,
      },
  )
  ```
  Before `handle_lipsync`'s `return` (~652):
  ```python
  audit.record(
      ctx.db,
      audit.system_principal(),
      "ai.lipsync",
      entity_type="media_asset",
      entity_id=asset["id"],
      payload={
          "timeline_id": timeline["id"],
          "audio_asset_id": audio_asset_id,
          "consent_id": consent_id,
          "seq_in_frame": seq_in,
          "seq_out_frame_exclusive": seq_out,
      },
  )
  ```

- [ ] **Step 5: Run it — expect PASS.**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_ai_audit.py -q
  ```
  Expected: `3 passed`.

- [ ] **Step 6: Commit.**
  ```
  git add services/local-api/src/laura/audit.py services/local-api/src/laura/ai/handlers.py services/local-api/tests/test_ai_audit.py
  git commit -m "feat(compliance): audit_events on voiceover/lipsync/reenact job success"
  ```

---

### Task D2: audit.record on export-render success + enforce disclosure presence in the reel renderer

Two changes in the render path: (1) write an `audit_event` when an export render completes (`handle_render` success, `render/handlers.py:346-352`); (2) make the synthetic disclosure **mandatory in the renderer** — a blank/whitespace `disclosure_text` is replaced by `"KI · synthetisch"` so the drawtext layer always fires (`render/mp4.py:289` currently skips the disclosure when the string is falsy).

**Files:**
- Modify `services/local-api/src/laura/render/handlers.py` (import audit ~line 11; `audit.record(...)` after `set_export_done` ~346)
- Modify `services/local-api/src/laura/render/mp4.py` (~289: coerce blank disclosure to default)
- Create `services/local-api/tests/test_disclosure_enforced.py`

**Interfaces:**
- Produces (contract): `audit.record(...)` on export-render success — action `"export.render"`, `entity_type="export"`, `entity_id=export_id`.
- Produces (contract): Reel render — `disclosure_text` presence ENFORCED; empty/blank → default `"KI · synthetisch"`; option can no longer disable it.
- Produces: `render.mp4._DEFAULT_DISCLOSURE: str = "KI · synthetisch"` (module constant, reused by test).

- [ ] **Step 1: Write failing tests.** One for the render-handler audit row, one pure-string assertion for the disclosure coercion. Real code (`test_disclosure_enforced.py`):
  ```python
  from __future__ import annotations

  import pytest

  from laura.render import mp4


  @pytest.mark.parametrize("blank", ["", "   ", None])
  def test_blank_disclosure_coerced_to_default(blank: str | None) -> None:
      assert mp4._effective_disclosure(blank) == mp4._DEFAULT_DISCLOSURE


  def test_nonblank_disclosure_kept_verbatim() -> None:
      assert mp4._effective_disclosure("Mein Text") == "Mein Text"
  ```
  And an export-audit test (`test_render_audit.py`, separate file so the heavy render fixture stays isolated) that runs `handle_render` against the existing reel fixture used by `tests/test_reel_render_api.py` and asserts:
  ```python
  events = repos.list_audit_events(db, limit=50)
  assert any(e["action"] == "export.render" and e["entity_type"] == "export" for e in events)
  ```

- [ ] **Step 2: Run — expect FAIL.**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_disclosure_enforced.py tests/test_render_audit.py -q
  ```
  Expected: `AttributeError: module 'laura.render.mp4' has no attribute '_effective_disclosure'` and the audit assertion fails.

- [ ] **Step 3: Implement disclosure coercion in `mp4.py`.** Add the constant + helper above `render_clips_mp4`, and rewrite the guard at line 289. Real code:
  ```python
  _DEFAULT_DISCLOSURE = "KI · synthetisch"


  def _effective_disclosure(disclosure_text: str | None) -> str:
      """Disclosure presence is mandatory (EU AI Act). Blank → default label."""
      text = (disclosure_text or "").strip()
      return text if text else _DEFAULT_DISCLOSURE
  ```
  Then replace the `if disclosure_text:` block (~289) so it always writes the file:
  ```python
      disclosure = _effective_disclosure(disclosure_text)
      disc_path = dest.parent / f"{dest.stem}.reel_disclosure.txt"
      disc_path.write_text(disclosure, encoding="utf-8")
      reel_files.append(disc_path)
      disc_tf = disc_path.name
  ```

- [ ] **Step 4: Add the export-render audit call in `handlers.py`.** Import `from .. import audit` (after line 11). After `repos.set_export_done(...)` (~346):
  ```python
  audit.record(
      ctx.db,
      audit.system_principal(),
      "export.render",
      entity_type="export",
      entity_id=export_id,
      payload={
          "timeline_id": exp.get("timeline_id"),
          "format": "mp4",
          "path": str(dest),
          "size_bytes": size_bytes,
      },
  )
  ```

- [ ] **Step 5: Run — expect PASS.**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_disclosure_enforced.py tests/test_render_audit.py -q
  ```
  Expected: `4 passed` (3 param + 1 verbatim) plus the audit test green.

- [ ] **Step 6: Commit.**
  ```
  git add services/local-api/src/laura/render/mp4.py services/local-api/src/laura/render/handlers.py services/local-api/tests/test_disclosure_enforced.py services/local-api/tests/test_render_audit.py
  git commit -m "feat(compliance): enforce reel disclosure presence + audit export renders"
  ```

---

### Task D3: ReelRenderRequest disclosure_text cannot disable disclosure (model + reels endpoint)

The renderer now enforces presence (D2), but the API still lets a client pass `disclosure_text=""`/`null` through (`reels.py:51` forwards it raw, `models.py:715` defaults it). Tighten the model so the **persisted option** is never empty: a validator coerces blank → default. This keeps the export option editable (text) but removes the off-switch at the API boundary, matching the renderer.

**Files:**
- Modify `services/local-api/src/laura/api/models.py` (`ReelRenderRequest.disclosure_text` ~715; add field validator)
- Create `services/local-api/tests/test_reel_disclosure_model.py`

**Interfaces:**
- Produces: `ReelRenderRequest.disclosure_text` always resolves to a non-blank string after validation (default `"KI · synthetisch"`).
- Consumes: `reels.render_reel` already copies `body.disclosure_text` into `options` (`reels.py:51`) — no endpoint change needed once the model guarantees non-blank.

- [ ] **Step 1: Write failing test.** Real code:
  ```python
  from __future__ import annotations

  import pytest

  from laura.api.models import ReelRenderRequest


  @pytest.mark.parametrize("raw", ["", "   ", None])
  def test_blank_disclosure_becomes_default(raw: str | None) -> None:
      req = ReelRenderRequest(disclosure_text=raw)
      assert req.disclosure_text == "KI · synthetisch"


  def test_custom_disclosure_is_preserved() -> None:
      req = ReelRenderRequest(disclosure_text="Synthetische Stimme")
      assert req.disclosure_text == "Synthetische Stimme"
  ```

- [ ] **Step 2: Run — expect FAIL.**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_reel_disclosure_model.py -q
  ```
  Expected: `AssertionError` for the blank cases (currently `None`/`""` pass through unchanged).

- [ ] **Step 3: Add a field validator on `ReelRenderRequest`.** Change the field type to non-optional and add a `field_validator`. Real code (replace line 715 and add the validator after the class fields; import `field_validator` from `pydantic`):
  ```python
      disclosure_text: str = "KI · synthetisch"

      @field_validator("disclosure_text", mode="before")
      @classmethod
      def _disclosure_required(cls, v: str | None) -> str:
          """EU AI Act: disclosure presence is mandatory; blank → default label."""
          text = (v or "").strip()
          return text or "KI · synthetisch"
  ```

- [ ] **Step 4: Run — expect PASS.**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_reel_disclosure_model.py -q
  ```
  Expected: `3 passed`.

- [ ] **Step 5: Commit.**
  ```
  git add services/local-api/src/laura/api/models.py services/local-api/tests/test_reel_disclosure_model.py
  git commit -m "feat(compliance): ReelRenderRequest coerces blank disclosure to default"
  ```

---

### Task D4: api.ts — listConsent + revokeConsent client methods

`createConsent` exists (`api.ts:1286`); `listConsent`/`revokeConsent` do not. The backend endpoints already exist (`reenact.py:71` GET, `:83` POST revoke) and return `ConsentOut`. Add the two client methods so the Phase-D inspector and the Phase-C consent-once can read and revoke records.

**Files:**
- Modify `apps/desktop/src/api.ts` (add after `createConsent` ~1294)
- Create `apps/desktop/src/api.consent.test.ts`

**Interfaces:**
- Produces: `listConsent(projectId: string): Promise<ConsentRecord[]>` (GET `/projects/{projectId}/consent`).
- Produces: `revokeConsent(projectId: string, consentId: string): Promise<ConsentRecord>` (POST `/projects/{projectId}/consent/{consentId}/revoke`).
- Consumes (existing): `ConsentRecord` interface (`api.ts:646`), `this.request<T>(path, init)`.

- [ ] **Step 1: Write failing vitest.** Real code (`api.consent.test.ts`):
  ```ts
  import { describe, expect, it, vi } from "vitest";
  import { LauraClient, type ConsentRecord } from "./api";

  const rec: ConsentRecord = {
    id: "c1", project_id: "p1", subject_label: "Laura",
    confirmed_at: "2026-06-22T00:00:00Z", confirmed_by: null,
    source_asset_id: null, note: null, revoked_at: null,
  };

  function clientWith(fetchImpl: typeof fetch): LauraClient {
    return new LauraClient("http://127.0.0.1:8765", undefined, fetchImpl);
  }

  describe("consent client", () => {
    it("listConsent GETs the project consent collection", async () => {
      const fetchMock = vi.fn(async () =>
        new Response(JSON.stringify([rec]), { status: 200 }),
      ) as unknown as typeof fetch;
      const records = await clientWith(fetchMock).listConsent("p1");
      expect(records).toEqual([rec]);
      const [url, init] = (fetchMock as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
      expect(String(url)).toContain("/projects/p1/consent");
      expect((init as RequestInit).method ?? "GET").toBe("GET");
    });

    it("revokeConsent POSTs the revoke route", async () => {
      const fetchMock = vi.fn(async () =>
        new Response(JSON.stringify({ ...rec, revoked_at: "2026-06-22T01:00:00Z" }), { status: 200 }),
      ) as unknown as typeof fetch;
      const out = await clientWith(fetchMock).revokeConsent("p1", "c1");
      expect(out.revoked_at).not.toBeNull();
      const [url, init] = (fetchMock as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
      expect(String(url)).toContain("/projects/p1/consent/c1/revoke");
      expect((init as RequestInit).method).toBe("POST");
    });
  });
  ```
  (Match the existing `LauraClient` constructor signature — adapt the `clientWith` factory to however the other `*.test.ts` files instantiate it; the load-bearing assertions are the URL + method.)

- [ ] **Step 2: Run — expect FAIL.**
  ```
  cd apps/desktop && npx vitest run src/api.consent.test.ts
  ```
  Expected: `TypeError: client.listConsent is not a function`.

- [ ] **Step 3: Implement the two methods.** Real code (insert after `createConsent`, ~1294):
  ```ts
    /**
     * List all consent records for a project, newest first.
     * GET /projects/{projectId}/consent → 200 ConsentRecord[]
     */
    listConsent(projectId: string): Promise<ConsentRecord[]> {
      return this.request<ConsentRecord[]>(`/projects/${projectId}/consent`);
    }

    /**
     * Revoke a consent record. Lipsync/reenact gates refuse it afterwards.
     * POST /projects/{projectId}/consent/{consentId}/revoke → 200 ConsentRecord
     */
    revokeConsent(projectId: string, consentId: string): Promise<ConsentRecord> {
      return this.request<ConsentRecord>(
        `/projects/${projectId}/consent/${consentId}/revoke`,
        { method: "POST" },
      );
    }
  ```

- [ ] **Step 4: Run — expect PASS.**
  ```
  cd apps/desktop && npx vitest run src/api.consent.test.ts
  ```
  Expected: `2 passed`.

- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/api.ts apps/desktop/src/api.consent.test.ts
  git commit -m "feat(compliance): listConsent + revokeConsent client methods"
  ```

---

### Task D5: ExportView — disclosure becomes a read-only mandatory confirmation (off-switch removed)

Remove the `reelDisclosure` checkbox (`ExportView.tsx:110` state, `:491-500` UI) that let the user disable the KI-Kennzeichnung. Replace it with an always-on read-only line that confirms presence (text remains editable via the existing/new text input, but it can never be turned off). The render request no longer sends a boolean toggle — `disclosure_text` is always populated.

**Files:**
- Modify `apps/desktop/src/components/ExportView.tsx` (remove `reelDisclosure` state ~110; remove checkbox ~491-500; ensure the `renderReel` call always passes a non-empty `disclosureText`)
- Create `apps/desktop/src/components/ExportView.disclosure.test.tsx`

**Interfaces:**
- Consumes: `client.renderReel(...)` (`api.ts:736`) — `disclosureText` argument now mandatory non-empty.
- Produces: ExportView renders a persistent disclosure confirmation; no control exists to omit the disclosure.

- [ ] **Step 1: Write failing test.** Render `ExportView`, assert (a) no checkbox labelled "KI-Kennzeichnung einblenden" exists, and (b) a mandatory-disclosure element is present. Real code (`ExportView.disclosure.test.tsx`):
  ```tsx
  import { render, screen } from "@testing-library/react";
  import { describe, expect, it } from "vitest";
  import { ExportView } from "./ExportView";
  // reuse the existing ExportView test harness/props factory from ExportView.test.tsx

  describe("ExportView disclosure is mandatory", () => {
    it("has no off-switch for the KI disclosure", () => {
      render(<ExportView {...makeExportViewProps()} />);
      expect(screen.queryByLabelText(/KI-Kennzeichnung einblenden/i)).toBeNull();
    });

    it("shows a persistent disclosure confirmation", () => {
      render(<ExportView {...makeExportViewProps()} />);
      expect(screen.getByText(/KI-Kennzeichnung/i)).toBeInTheDocument();
    });
  });
  ```
  (If `ExportView.test.tsx` does not export a props factory, lift its arrange block into a small `makeExportViewProps()` helper in this test file — show that helper inline.)

- [ ] **Step 2: Run — expect FAIL.**
  ```
  cd apps/desktop && npx vitest run src/components/ExportView.disclosure.test.tsx
  ```
  Expected: the first test fails (`queryByLabelText` still finds the checkbox).

- [ ] **Step 3: Remove the off-switch.** Delete the `reelDisclosure` state (line 110) and the checkbox block (491-500). Replace the block with a read-only confirmation row that keeps Tailwind styling consistent with the surrounding labels:
  ```tsx
          <div
            className="flex items-center gap-2 text-xs text-content-muted"
            aria-label="KI-Kennzeichnung verpflichtend"
          >
            <span aria-hidden className="text-content-strong">●</span>
            KI-Kennzeichnung wird immer eingeblendet (EU AI Act)
          </div>
  ```
  At the `renderReel` call site, drop any `disclosure: reelDisclosure` flag and ensure `disclosureText` is always passed (use the existing disclosure-text input value, falling back to `"KI · synthetisch"`):
  ```tsx
            disclosureText: reelDisclosureText.trim() || "KI · synthetisch",
  ```
  (If no text input exists yet, the constant fallback alone satisfies the contract; the backend coerces blanks regardless per D2/D3.)

- [ ] **Step 4: Run — expect PASS.**
  ```
  cd apps/desktop && npx vitest run src/components/ExportView.disclosure.test.tsx
  ```
  Expected: `2 passed`.

- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/ExportView.tsx apps/desktop/src/components/ExportView.disclosure.test.tsx
  git commit -m "feat(compliance): remove ExportView disclosure off-switch (presence mandatory)"
  ```

---

### Task D6: useConsent hook — list/create/revoke per project

A thin data hook so the inspector (D7) and the EditorialToolsBar disclosure line stay declarative. Wraps `client.listConsent`/`createConsent`/`revokeConsent`. Pure-logic parts (active vs. revoked partition) are unit-tested; the fetch wiring is exercised via a mocked client.

**Files:**
- Create `apps/desktop/src/hooks/useConsent.ts`
- Create `apps/desktop/src/hooks/useConsent.test.ts`

**Interfaces:**
- Produces: `useConsent(projectId: string | null) -> { records: ConsentRecord[]; active: ConsentRecord[]; loading: boolean; error: string | null; create(subjectLabel: string): Promise<void>; revoke(consentId: string): Promise<void>; reload(): Promise<void> }`.
- Produces (pure, exported for test): `partitionConsent(records: ConsentRecord[]): { active: ConsentRecord[]; revoked: ConsentRecord[] }` (active = `revoked_at == null`).
- Consumes: `client.listConsent`, `client.createConsent`, `client.revokeConsent` (D4).

- [ ] **Step 1: Write failing test for the pure partition.** Real code (`useConsent.test.ts`):
  ```ts
  import { describe, expect, it } from "vitest";
  import { partitionConsent } from "./useConsent";
  import type { ConsentRecord } from "../api";

  const base: ConsentRecord = {
    id: "x", project_id: "p", subject_label: "s",
    confirmed_at: "t", confirmed_by: null, source_asset_id: null,
    note: null, revoked_at: null,
  };

  describe("partitionConsent", () => {
    it("splits active vs revoked by revoked_at", () => {
      const recs: ConsentRecord[] = [
        { ...base, id: "a", revoked_at: null },
        { ...base, id: "b", revoked_at: "2026-06-22T00:00:00Z" },
      ];
      const { active, revoked } = partitionConsent(recs);
      expect(active.map((r) => r.id)).toEqual(["a"]);
      expect(revoked.map((r) => r.id)).toEqual(["b"]);
    });

    it("treats empty input as all-empty", () => {
      expect(partitionConsent([])).toEqual({ active: [], revoked: [] });
    });
  });
  ```

- [ ] **Step 2: Run — expect FAIL.**
  ```
  cd apps/desktop && npx vitest run src/hooks/useConsent.test.ts
  ```
  Expected: `Failed to resolve import "./useConsent"`.

- [ ] **Step 3: Implement the hook.** Real code (`useConsent.ts`) — follow the existing `useScenes.ts` shape for client access (`useLauraClient()` or the prop pattern the repo already uses; mirror it exactly):
  ```ts
  import { useCallback, useEffect, useState } from "react";
  import type { ConsentRecord } from "../api";
  import { useLauraClient } from "./useLauraClient";

  export function partitionConsent(records: ConsentRecord[]): {
    active: ConsentRecord[];
    revoked: ConsentRecord[];
  } {
    const active: ConsentRecord[] = [];
    const revoked: ConsentRecord[] = [];
    for (const r of records) (r.revoked_at == null ? active : revoked).push(r);
    return { active, revoked };
  }

  export function useConsent(projectId: string | null) {
    const client = useLauraClient();
    const [records, setRecords] = useState<ConsentRecord[]>([]);
    const [loading, setLoading] = useState<boolean>(false);
    const [error, setError] = useState<string | null>(null);

    const reload = useCallback(async (): Promise<void> => {
      if (!projectId) {
        setRecords([]);
        return;
      }
      setLoading(true);
      setError(null);
      try {
        setRecords(await client.listConsent(projectId));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setLoading(false);
      }
    }, [client, projectId]);

    useEffect(() => {
      void reload();
    }, [reload]);

    const create = useCallback(
      async (subjectLabel: string): Promise<void> => {
        if (!projectId) return;
        await client.createConsent(projectId, { subjectLabel });
        await reload();
      },
      [client, projectId, reload],
    );

    const revoke = useCallback(
      async (consentId: string): Promise<void> => {
        if (!projectId) return;
        await client.revokeConsent(projectId, consentId);
        await reload();
      },
      [client, projectId, reload],
    );

    const { active } = partitionConsent(records);
    return { records, active, loading, error, create, revoke, reload };
  }
  ```
  (If the repo wires the client by prop rather than a `useLauraClient` hook, accept `client` as a second arg instead — match `useSceneTimeline.ts` exactly.)

- [ ] **Step 4: Run — expect PASS.**
  ```
  cd apps/desktop && npx vitest run src/hooks/useConsent.test.ts
  ```
  Expected: `2 passed`.

- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/hooks/useConsent.ts apps/desktop/src/hooks/useConsent.test.ts
  git commit -m "feat(compliance): useConsent hook (list/create/revoke + partition)"
  ```

---

### Task D7: EditorialToolsBar — always-on synthetic-disclosure line + „Das bin ich"-Consent-Inspektor

Extend the `EditorialToolsBar` (created in Phase C) with (1) a persistent, dezente disclosure line `Enthält synthetische Inhalte: {Effekte} · Einwilligung: {Subjekte}` and (2) a minimal consent inspector (create by subject label, list active, revoke). The effects list is derived from the scene-grouped clips' synthetic flags / `ai_effect` (already on assets via `set_asset_synthetic` + `MediaSidebar` KI badge logic); the subjects come from `useConsent(projectId).active`.

**Files:**
- Modify `apps/desktop/src/components/EditorialToolsBar.tsx` (add disclosure line + consent inspector section; consumes Phase C component)
- Create `apps/desktop/src/components/EditorialToolsBar.consent.test.tsx`

**Interfaces:**
- Consumes: `useConsent` (D6) → `{ active, create, revoke }`; `EditorialToolsBar` props gain `projectId: string`, `syntheticEffects: string[]` (e.g. `["VO","Lippensync"]`, derived upstream from clip `ai_effect`).
- Produces: an always-present disclosure line (no toggle) + a consent create/list/revoke mini-inspector feeding Phase C's consent-once.

- [ ] **Step 1: Write failing test.** Real code (`EditorialToolsBar.consent.test.tsx`):
  ```tsx
  import { fireEvent, render, screen } from "@testing-library/react";
  import { describe, expect, it, vi } from "vitest";
  import { EditorialToolsBar } from "./EditorialToolsBar";

  vi.mock("../hooks/useConsent", () => ({
    useConsent: () => ({
      active: [{ id: "c1", subject_label: "Laura", revoked_at: null }],
      create: vi.fn(),
      revoke: vi.fn(),
      records: [],
      loading: false,
      error: null,
      reload: vi.fn(),
    }),
    partitionConsent: () => ({ active: [], revoked: [] }),
  }));

  describe("EditorialToolsBar compliance", () => {
    it("always shows the synthetic-content disclosure with effects + subjects", () => {
      render(<EditorialToolsBar {...makeToolsBarProps({ syntheticEffects: ["VO", "Lippensync"] })} />);
      const line = screen.getByText(/Enthält synthetische Inhalte/i);
      expect(line.textContent).toMatch(/VO/);
      expect(line.textContent).toMatch(/Lippensync/);
      expect(line.textContent).toMatch(/Laura/);
    });

    it("renders no control to hide the disclosure", () => {
      render(<EditorialToolsBar {...makeToolsBarProps({ syntheticEffects: [] })} />);
      expect(screen.queryByLabelText(/Kennzeichnung.*ausblenden/i)).toBeNull();
    });
  });
  ```
  (`makeToolsBarProps` mirrors the props the Phase-C task defined for `EditorialToolsBar`; supply `projectId`, `voices`, etc. as that task established.)

- [ ] **Step 2: Run — expect FAIL.**
  ```
  cd apps/desktop && npx vitest run src/components/EditorialToolsBar.consent.test.tsx
  ```
  Expected: `getByText(/Enthält synthetische Inhalte/i)` throws (line not yet rendered).

- [ ] **Step 3: Implement disclosure line + inspector.** Add to `EditorialToolsBar` (mirror its existing Tailwind strip styling). Real code (the disclosure line + a collapsible inspector):
  ```tsx
    const { active, create, revoke } = useConsent(projectId);
    const [subject, setSubject] = useState<string>("");

    const effectsLabel = syntheticEffects.length
      ? syntheticEffects.join(", ")
      : "keine";
    const subjectsLabel = active.length
      ? active.map((c) => c.subject_label).join(", ")
      : "—";
  ```
  ```tsx
        <div className="flex items-center gap-2 text-[11px] text-content-muted">
          <span aria-hidden className="text-amber-400">⬤</span>
          <span>
            Enthält synthetische Inhalte: {effectsLabel} · Einwilligung: {subjectsLabel}
          </span>
        </div>
        <details className="text-[11px] text-content-muted">
          <summary className="cursor-pointer">Das bin ich (Einwilligung)</summary>
          <div className="mt-1 flex items-center gap-2">
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Name der Person"
              className="rounded bg-surface-2 px-2 py-1 text-xs text-content-strong"
            />
            <button
              type="button"
              disabled={!subject.trim()}
              onClick={() => { void create(subject.trim()); setSubject(""); }}
              className="rounded bg-surface-3 px-2 py-1 text-xs disabled:opacity-40"
            >
              Bestätigen
            </button>
          </div>
          <ul className="mt-1 space-y-0.5">
            {active.map((c) => (
              <li key={c.id} className="flex items-center justify-between gap-2">
                <span>{c.subject_label}</span>
                <button
                  type="button"
                  onClick={() => { void revoke(c.id); }}
                  className="text-content-faint hover:text-content-strong"
                >
                  widerrufen
                </button>
              </li>
            ))}
          </ul>
        </details>
  ```
  Add `projectId: string` and `syntheticEffects: string[]` to the component's props interface and import `useConsent` + `useState`.

- [ ] **Step 4: Run — expect PASS.**
  ```
  cd apps/desktop && npx vitest run src/components/EditorialToolsBar.consent.test.tsx
  ```
  Expected: `2 passed`.

- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/EditorialToolsBar.tsx apps/desktop/src/components/EditorialToolsBar.consent.test.tsx
  git commit -m "feat(compliance): always-on disclosure line + Das-bin-ich consent inspector"
  ```

- [ ] **Step 6: Manual check (headless nicht prüfbar, live auf CDP 9222).** Im Feinschnitt: Disclosure-Zeile sichtbar während des Editierens; „Das bin ich" → Subjekt anlegen erscheint sofort in „Einwilligung:"; widerrufen entfernt es; kein Off-Schalter vorhanden.

---

### Task D8: Green verification — ruff + mypy + pytest (backend) and tsc + vitest (frontend) for touched files

**Files:** all files touched in D1–D7 (no new files).

**Interfaces:** none — verification only.

- [ ] **Step 1: Backend lint + format check.**
  ```
  cd services/local-api && uv run --no-sync ruff check src/laura/audit.py src/laura/ai/handlers.py src/laura/render/mp4.py src/laura/render/handlers.py src/laura/api/models.py tests/test_ai_audit.py tests/test_disclosure_enforced.py tests/test_render_audit.py tests/test_reel_disclosure_model.py
  ```
  Expected: `All checks passed!`.

- [ ] **Step 2: Backend types.**
  ```
  cd services/local-api && uv run --no-sync mypy src/laura/audit.py src/laura/ai/handlers.py src/laura/render/mp4.py src/laura/render/handlers.py src/laura/api/models.py
  ```
  Expected: `Success: no issues found in 5 source files`.

- [ ] **Step 3: Backend tests (Phase-D + the touched suites that guard regressions).**
  ```
  cd services/local-api && uv run --no-sync pytest tests/test_ai_audit.py tests/test_disclosure_enforced.py tests/test_render_audit.py tests/test_reel_disclosure_model.py tests/test_reel_render.py tests/test_reel_render_api.py tests/test_consent_repos.py -q
  ```
  Expected: all green, e.g. `... passed` with `0 failed`.

- [ ] **Step 4: Frontend types.**
  ```
  cd apps/desktop && npx tsc --noEmit
  ```
  Expected: no output (exit 0); zero errors, no `any`.

- [ ] **Step 5: Frontend tests (Phase-D files).**
  ```
  cd apps/desktop && npx vitest run src/api.consent.test.ts src/hooks/useConsent.test.ts src/components/ExportView.disclosure.test.tsx src/components/EditorialToolsBar.consent.test.tsx
  ```
  Expected: `4 test files … passed` (8 tests).

- [ ] **Step 6: Acceptance gate (spec §12 D).** Confirm: disclosure not switchable off (D2/D3/D5); `audit_events` written for voiceover/lipsync/reenact + export (D1/D2 tests green); consent create/list/revoke reachable end-to-end (D4/D6/D7). If all four commands above are green and the manual D7 check passed, Phase D is done.

- [ ] **Step 7: No extra commit needed** (verification only). If lint/format auto-fixed anything, commit just those files:
  ```
  git add <only the files ruff --fix or formatting changed>
  git commit -m "style(compliance): ruff/format fixups for Phase D"
  ```

---

Relevant absolute paths confirmed during research:
- Backend success paths: `C:\Users\User\Desktop\Laura\services\local-api\src\laura\ai\handlers.py` (voiceover return ~436, reenact ~285, lipsync ~652); `…\render\handlers.py` (export done ~346); `…\render\mp4.py` (disclosure guard ~289); `…\render\reel.py` (drawtext ~53); `…\api\models.py` (`ReelRenderRequest` 713-728, `ConsentOut/ConsentRequest` 818-833); `…\api\reels.py` (option passthrough 51); `…\api\reenact.py` (consent CRUD endpoints 48-98 — already exist); `…\audit.py` (`record` 15-33); `…\db\repos.py` (`insert_audit_event` 1109, consent create/get/list/revoke 1758-1890, `set_asset_synthetic` 227); `…\jobs\runner.py` (`JobContext` 42-50, no principal); `…\auth\principal.py`.
- Frontend: `C:\Users\User\Desktop\Laura\apps\desktop\src\api.ts` (`ConsentRecord` 646, `createConsent` 1286 — `listConsent`/`revokeConsent` MISSING); `…\components\ExportView.tsx` (`reelDisclosure` state 110, checkbox 491-500). `EditorialToolsBar.tsx` does not yet exist (created in Phase C — D7 modifies it).

---

## Phase E — Zusammenfuegen verschlanken + Fixes

Phase E ist die Aufraeum- und Verdrahtungsphase: Editorial-Tools (VO, Lipsync, Reenact) wandern aus `AssembleView` in den Feinschnitt, Zusammenfuegen behaelt nur Sequenz-Tools, Reenact wird eine manuelle Feinschnitt-Aktion in der `EditorialToolsBar`, das kaputte Job-Polling in `LipsyncPanel` + `DemoAssistantPanel` wird ueber `useJobStatus` gefixt, und am Ende steht die phasenuebergreifende gruene Verifikation. Diese Phase **konsumiert** aus frueheren Phasen: `EditorialToolsBar` (Phase C), `ContinuousTranscript`/`useRoughCutTranscript` (Phasen A/C). Alle Tests sind Frontend (vitest); reine UI-Darstellung wird als "manuell zu pruefen (live CDP 9222)" markiert.

---

### Task E1: LipsyncPanel — Job-Polling ueber useJobStatus fixen

Heute setzt `LipsyncPanel` nur `setJobId(accepted.job_id)` und zeigt statisch "Job gestartet: {id}" ([LipsyncPanel.tsx:88](apps/desktop/src/components/LipsyncPanel.tsx), [LipsyncPanel.tsx:114](apps/desktop/src/components/LipsyncPanel.tsx)) — es gibt **kein** Polling und keinen Terminal-Status. Wir integrieren `useJobStatus` analog zu `ReenactPanel`.

**Files:**
- Modify `apps/desktop/src/components/LipsyncPanel.tsx` (Imports Zeile 1-4; State Zeile 33; Submit-Erfolg Zeile 88-89; Statusanzeige Zeile 114)
- Modify `apps/desktop/src/components/LipsyncPanel.test.tsx` (neuer Polling-Test ans Ende der `describe`)

**Interfaces:**
- Consumes: `useJobStatus(client: LauraClient, jobId: string | null): { jobStatus: JobStatus | null; error: string | null; isRunning: boolean }` ([hooks/useJobStatus.ts:36](apps/desktop/src/hooks/useJobStatus.ts)); existing `client.lipsync(...)`.
- Produces: keine neue Signatur (interne Polling-Verdrahtung); `onChange()` feuert weiterhin genau einmal pro erfolgreichem Job.

- [ ] **Step 1: Failing-Test schreiben — Polling zeigt Terminal-Status + onChange feuert einmal bei success.** In `LipsyncPanel.test.tsx` ans Ende der `describe` einfuegen:
  ```tsx
  it("polls the lipsync job to a terminal status and fires onChange once on success", async () => {
    vi.useFakeTimers();
    const getJob = vi
      .fn()
      .mockResolvedValueOnce({ id: "lip-job-1", status: "running" })
      .mockResolvedValue({ id: "lip-job-1", status: "succeeded" });
    const onChange = vi.fn();
    const c = client({ getJob });
    const { getByLabelText, getByRole, findByText } = render(
      <LipsyncPanel
        client={c}
        projectId="p"
        timelineId="tl-1"
        assets={[asset("audio-1", "audio", "voice.wav")]}
        onChange={onChange}
      />,
    );
    fireEvent.change(getByLabelText("Subjekt-Label für Lipsync-Consent"), { target: { value: "Person A" } });
    fireEvent.click(getByRole("button", { name: "Consent bestätigen" }));
    await vi.waitFor(() => expect(c.createConsent).toHaveBeenCalled());
    fireEvent.click(getByLabelText("Lizenz und Nutzung bestätigt"));
    fireEvent.change(getByLabelText("Lipsync seq out"), { target: { value: "30" } });
    fireEvent.click(getByRole("button", { name: "Lipsync (stub)" }));
    await vi.waitFor(() => expect(c.lipsync).toHaveBeenCalled());
    await vi.advanceTimersByTimeAsync(1600);
    await findByText("Läuft…");
    await vi.advanceTimersByTimeAsync(1600);
    await findByText("Fertig ✓");
    expect(onChange).toHaveBeenCalledOnce();
    vi.useRealTimers();
  });
  ```
- [ ] **Step 2: Test laufen lassen — erwartet FAIL.**
  ```
  npm --prefix apps/desktop run test -- src/components/LipsyncPanel.test.tsx
  ```
  Erwartung: FAIL — `Unable to find an element with the text: Läuft…` (kein Polling/Statuslabel vorhanden), und `onChange` feuert heute bereits beim Submit statt bei Success.
- [ ] **Step 3: Minimal-Implementierung — `useJobStatus` einhaengen, Status-Chip rendern, onChange auf Success verschieben.** Imports ergaenzen:
  ```tsx
  import { type ReactElement, useEffect, useMemo, useRef, useState } from "react";
  import { type Asset, type LauraClient } from "../api";
  import { useJobStatus } from "../hooks/useJobStatus";
  import { log } from "../shared/log";
  ```
  Chip-Helper oben in der Datei (vor der Komponente) ergaenzen:
  ```tsx
  function jobChipClass(status: string): string {
    if (status === "failed") return "border-status-err bg-status-err/15 text-status-err";
    if (status === "succeeded") return "border-status-ok bg-status-ok/20 text-status-ok";
    return "border-sky-800 bg-sky-950/30 text-sky-200";
  }
  function jobChipLabel(status: string): string {
    if (status === "succeeded") return "Fertig ✓";
    if (status === "failed") return "Fehlgeschlagen";
    if (status === "cancelled") return "Abgebrochen";
    if (status === "queued") return "In Warteschlange";
    return "Läuft…";
  }
  ```
  Nach `const [jobId, setJobId] = useState<string | null>(null);` ergaenzen:
  ```tsx
  const { jobStatus, error: jobError, isRunning } = useJobStatus(client, jobId);
  const onChangeFiredRef = useRef<string | null>(null);
  useEffect(() => {
    if (jobStatus?.status === "succeeded" && onChangeFiredRef.current !== jobStatus.id) {
      onChangeFiredRef.current = jobStatus.id;
      onChange();
    }
  }, [jobStatus, onChange]);
  ```
  In `submit()` das `onChange()` aus dem Try-Block entfernen (es feuert jetzt ueber den Effect) und vor dem Submit den Ref zuruecksetzen:
  ```tsx
      setBusy(true);
      setError(null);
      setJobId(null);
      onChangeFiredRef.current = null;
      try {
        const accepted = await client.lipsync(timelineId, {
          seqIn, seqOut, audioAssetId, consentId, licenseAccepted, backend, qualityThreshold: 0.6,
        });
        setJobId(accepted.job_id);
      } catch (e) {
  ```
  Die statische Anzeige `{jobId !== null && <div ...>Job gestartet: {jobId}</div>}` ersetzen durch:
  ```tsx
      {jobId !== null && jobStatus !== null && (
        <div className="flex flex-col gap-1.5">
          <div className="flex items-center gap-2">
            <span className={`rounded border px-2 py-0.5 text-[11px] ${jobChipClass(jobStatus.status)}`}>
              {jobChipLabel(jobStatus.status)}
            </span>
            <span className="truncate text-[10px] text-content-faint">{jobId}</span>
          </div>
          {jobStatus.status === "failed" && jobError !== null && (
            <div className="rounded border border-status-err/40 bg-status-err/10 p-2 text-xs text-status-err">
              {jobError}
            </div>
          )}
        </div>
      )}
  ```
  Den Submit-Button zusaetzlich waehrend `isRunning` deaktivieren: in der `disabled`-Berechnung `busy ||` durch `busy || isRunning ||` ersetzen.
- [ ] **Step 4: Test laufen lassen — erwartet PASS.**
  ```
  npm --prefix apps/desktop run test -- src/components/LipsyncPanel.test.tsx
  ```
  Erwartung: PASS (alle 3 Tests gruen).
- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/LipsyncPanel.tsx apps/desktop/src/components/LipsyncPanel.test.tsx
  git commit -m "fix(ui): poll lipsync job via useJobStatus, fire onChange on success not submit"
  ```

---

### Task E2: DemoAssistantPanel — getJob-once durch useJobStatus-Polling ersetzen

`DemoAssistantPanel` ruft `client.getJob(accepted.job_id)` **genau einmal** ([DemoAssistantPanel.tsx:54-56](apps/desktop/src/components/DemoAssistantPanel.tsx)) und liest den Draft direkt danach — bei laufendem Job (`status !== "succeeded"`) zieht es den Draft trotzdem sofort und der Status bleibt falsch. Wir warten via `useJobStatus` auf den Terminal-Status, bevor der Draft geladen wird.

**Files:**
- Modify `apps/desktop/src/components/DemoAssistantPanel.tsx` (Imports Zeile 1-4; State Zeile 27-32; `createDraft` Zeile 44-67; Status-Render Zeile 111)
- Modify `apps/desktop/src/components/DemoAssistantPanel.test.tsx` (Test "lists only video assets and creates a draft" Zeile 68-87 an Polling anpassen; neuer Running-Test)

**Interfaces:**
- Consumes: `useJobStatus`; existing `client.createDemoDraft(assetId)`, `client.getDemoDraft(draftId)`.
- Produces: keine neue Signatur; `getDemoDraft` wird erst nach Job-Terminal aufgerufen.

- [ ] **Step 1: Failing-Test schreiben.** Den bestehenden Test (Zeile 68-87) so anpassen, dass `getJob` zuerst `running`, dann `succeeded` liefert, und ergaenzen, dass der Draft erst nach dem zweiten Tick geladen wird. Ans Ende der `describe` zusaetzlich:
  ```tsx
  it("waits for the draft job to reach a terminal status before loading the draft", async () => {
    vi.useFakeTimers();
    const getJob = vi
      .fn()
      .mockResolvedValueOnce({ id: "job-1", status: "running" })
      .mockResolvedValue({ id: "job-1", status: "succeeded" });
    const getDemoDraft = vi.fn().mockResolvedValue(draft);
    const c = client({ getJob, getDemoDraft });
    const { getByRole, findByDisplayValue } = render(
      <DemoAssistantPanel client={c} assets={[asset("video-1", "video", "screen.mp4")]} onApplied={vi.fn()} />,
    );
    fireEvent.click(getByRole("button", { name: "Demo-Draft erzeugen" }));
    await vi.advanceTimersByTimeAsync(1600);
    expect(getDemoDraft).not.toHaveBeenCalled();
    await vi.advanceTimersByTimeAsync(1600);
    await findByDisplayValue("Intro");
    expect(getDemoDraft).toHaveBeenCalledWith("draft-1");
    vi.useRealTimers();
  });
  ```
- [ ] **Step 2: Test laufen lassen — erwartet FAIL.**
  ```
  npm --prefix apps/desktop run test -- src/components/DemoAssistantPanel.test.tsx
  ```
  Erwartung: FAIL — `expected getDemoDraft not to have been called` (heute laeuft `getDemoDraft` sofort nach dem einmaligen `getJob`, ohne auf Terminal zu warten).
- [ ] **Step 3: Minimal-Implementierung — Job-Id-State + useJobStatus, Draft-Laden in Success-Effect.** Imports:
  ```tsx
  import { type ReactElement, useEffect, useMemo, useRef, useState } from "react";
  import { type Asset, type DemoDraft, type DemoDraftItem, type LauraClient } from "../api";
  import { useJobStatus } from "../hooks/useJobStatus";
  import { log } from "../shared/log";
  ```
  State ergaenzen (nach `status`):
  ```tsx
  const [draftJobId, setDraftJobId] = useState<string | null>(null);
  const pendingDraftIdRef = useRef<string | null>(null);
  const loadedJobRef = useRef<string | null>(null);
  const { jobStatus, error: jobError } = useJobStatus(client, draftJobId);
  ```
  `createDraft()` umschreiben — nur enqueuen, nicht mehr `getJob`/`getDemoDraft` inline:
  ```tsx
  async function createDraft(): Promise<void> {
    if (!assetId) { setError("Kein Video-Asset verfügbar."); return; }
    setBusy(true);
    setError(null);
    setStatus(null);
    setDraftJobId(null);
    loadedJobRef.current = null;
    try {
      const accepted = await client.createDemoDraft(assetId);
      pendingDraftIdRef.current = accepted.draft_id;
      setDraftJobId(accepted.job_id);
      setStatus(jobStatusText("running"));
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("createDemoDraft failed:", msg);
      setError(msg);
      setBusy(false);
    }
  }
  ```
  Success/Terminal-Effect ergaenzen:
  ```tsx
  useEffect(() => {
    if (jobStatus === null || draftJobId === null || loadedJobRef.current === jobStatus.id) return;
    if (jobStatus.status === "succeeded") {
      loadedJobRef.current = jobStatus.id;
      const draftId = pendingDraftIdRef.current;
      if (draftId === null) { setBusy(false); return; }
      client
        .getDemoDraft(draftId)
        .then((loaded) => { setDraft(loaded); setItems(loaded.items); setStatus(jobStatusText("succeeded")); })
        .catch((e: unknown) => { setError(e instanceof Error ? e.message : String(e)); })
        .finally(() => setBusy(false));
    } else if (jobStatus.status === "failed" || jobStatus.status === "cancelled") {
      loadedJobRef.current = jobStatus.id;
      setStatus(jobStatusText(jobStatus.status));
      if (jobError !== null) setError(jobError);
      setBusy(false);
    }
  }, [jobStatus, jobError, draftJobId, client]);
  ```
- [ ] **Step 4: Test laufen lassen — erwartet PASS.**
  ```
  npm --prefix apps/desktop run test -- src/components/DemoAssistantPanel.test.tsx
  ```
  Erwartung: PASS (beide bestehenden Tests + neuer Running-Test gruen).
- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/DemoAssistantPanel.tsx apps/desktop/src/components/DemoAssistantPanel.test.tsx
  git commit -m "fix(ui): poll demo-draft job via useJobStatus instead of single getJob read"
  ```

---

### Task E3: Reenact als manuelle Aktion in EditorialToolsBar (Feinschnitt)

Reenact ist eine kreative, **nicht** transkript-getriebene Wahl (Spec §9). Es lebt unter dem Video in der `EditorialToolsBar` (aus Phase C). `ReenactPanel` selbst ist bereits korrekt verdrahtet (eigenes `useJobStatus`-Polling); wir rendern es als ausklappbare manuelle Aktion in der Toolbar, statt es in Zusammenfuegen zu zeigen.

**Files:**
- Modify `apps/desktop/src/components/EditorialToolsBar.tsx` (Reenact-Toggle + eingebettetes `ReenactPanel`)
- Modify `apps/desktop/src/components/EditorialToolsBar.test.tsx` (neuer Reenact-Toggle-Test)

**Interfaces:**
- Consumes (aus Phase C): `EditorialToolsBar` mit den Props `{ client, projectId, timelineId, assets, currentSeqFrame, rateNum, rateDen, voiceId, onVoiceChange, onChange }` (verbatim wie Phase C produziert).
- Consumes: `ReenactPanel` mit `ReenactPanelProps { client, projectId, timelineId, assets: {id;display_name}[], onChange, currentSeqFrame, rateNum, rateDen }` ([ReenactPanel.tsx:8-21](apps/desktop/src/components/ReenactPanel.tsx)).
- Produces: kein neuer externer Vertrag — Reenact ist intern in der Toolbar, hinter einem Toggle "Reenact" (manuell).

- [ ] **Step 1: Failing-Test schreiben.** In `EditorialToolsBar.test.tsx`:
  ```tsx
  it("hides the reenact panel until the manual Reenact action is opened", () => {
    const c = { listVoiceoverVoices: vi.fn().mockResolvedValue([]) } as unknown as LauraClient;
    const { getByRole, queryByText } = render(
      <EditorialToolsBar
        client={c}
        projectId="p"
        timelineId="tl-1"
        assets={[{ id: "a1", display_name: "clip.mp4" }]}
        currentSeqFrame={0}
        rateNum={30}
        rateDen={1}
        voiceId=""
        onVoiceChange={vi.fn()}
        onChange={vi.fn()}
      />,
    );
    expect(queryByText("Reenact (Identitäts-Ebene)")).toBeNull();
    fireEvent.click(getByRole("button", { name: /Reenact/ }));
    expect(queryByText("Reenact (Identitäts-Ebene)")).not.toBeNull();
  });
  ```
- [ ] **Step 2: Test laufen lassen — erwartet FAIL.**
  ```
  npm --prefix apps/desktop run test -- src/components/EditorialToolsBar.test.tsx
  ```
  Erwartung: FAIL — `Unable to find an accessible element with the role "button" and name /Reenact/` (kein Reenact-Toggle in der Toolbar).
- [ ] **Step 3: Minimal-Implementierung.** In `EditorialToolsBar.tsx` Import + State + Toggle ergaenzen:
  ```tsx
  import { ReenactPanel } from "./ReenactPanel";
  // ... innerhalb der Komponente:
  const [reenactOpen, setReenactOpen] = useState(false);
  ```
  Im Toolbar-Streifen einen Button und das eingebettete Panel rendern:
  ```tsx
  <button
    type="button"
    onClick={() => setReenactOpen((v) => !v)}
    aria-expanded={reenactOpen}
    className="rounded border border-bezel bg-surface-1 px-2 py-1 text-xs text-content-muted hover:bg-surface-2 hover:text-content-strong"
  >
    Reenact
  </button>
  {reenactOpen && (
    <ReenactPanel
      client={client}
      projectId={projectId}
      timelineId={timelineId}
      assets={assets}
      onChange={onChange}
      currentSeqFrame={currentSeqFrame}
      rateNum={rateNum}
      rateDen={rateDen}
    />
  )}
  ```
- [ ] **Step 4: Test laufen lassen — erwartet PASS.**
  ```
  npm --prefix apps/desktop run test -- src/components/EditorialToolsBar.test.tsx
  ```
  Erwartung: PASS.
- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/EditorialToolsBar.tsx apps/desktop/src/components/EditorialToolsBar.test.tsx
  git commit -m "feat(ui): expose Reenact as a manual action inside EditorialToolsBar"
  ```

---

### Task E4: AssembleView entschlacken — Editorial-Tools raus, nur Sequenz-Tools bleiben

`AssembleView` zeigt heute im Tools-Tab `TransitionReviewPanel`, `AudioLaneControls`, `DemoAssistantPanel`, `OverlayControls`, `ReenactPanel`, `LipsyncPanel` ([AssembleView.tsx:916-991](apps/desktop/src/components/AssembleView.tsx)) **und** im Transkript-Tab den editorischen `SequenceTranscriptPanel`/`TranscriptBlockEditor` mit VO-Erzeugung. Per Spec §9 bleiben in Zusammenfuegen nur **Sequenz**-Tools: Szenen-Reihenfolge (Storyboard), Overlay, Audio-Lane, Demo-Draft. VO/Lipsync/Reenact/TransitionReview wandern raus (Reenact ist via E3 jetzt im Feinschnitt). Keine Tot-Knoepfe: ungenutzte Imports/State werden entfernt.

**Files:**
- Modify `apps/desktop/src/components/AssembleView.tsx` (Imports 12-33; `TranscriptBlockEditor`/`SequenceTranscriptPanel` 121-424 — VO-Teile entfernen; Tools-Tab 916-991)
- Modify `apps/desktop/src/components/AssembleView.test.tsx` (Assertions: Editorial-Tools nicht mehr gerendert; Sequenz-Tools bleiben)

**Interfaces:**
- Consumes: `OverlayControls`, `AudioLaneControls`, `DemoAssistantPanel` (unveraendert).
- Produces: `AssembleView`-Tools-Tab enthaelt **nur noch** `AudioLaneControls`, `DemoAssistantPanel`, `OverlayControls`; das Transkript-Rail bleibt read-only (Caption-Preview), aber **ohne** VO-Erzeugungs-Buttons.

- [ ] **Step 1: Failing-Test schreiben.** In `AssembleView.test.tsx` ergaenzen (Mock-Client mit den noetigen list-Methoden vorausgesetzt — vorhandenen Test-Setup-Helper wiederverwenden):
  ```tsx
  it("keeps only sequence tools in Zusammenfügen — no VO/Lipsync/Reenact", async () => {
    const c = makeClient(); // bestehender Helper im File
    const { getByRole, queryByText, findByRole } = render(
      <AssembleView client={c} projectId="p" roughCutId={null} onSeekScene={vi.fn()} />,
    );
    fireEvent.click(await findByRole("button", { name: "Tools" }));
    expect(queryByText("Reenact (Identitäts-Ebene)")).toBeNull();
    expect(queryByText("Lipsync (Deepfake)")).toBeNull();
    // Sequenz-Tools bleiben:
    expect(queryByText("Demo-Draft")).not.toBeNull();
  });
  ```
  Zusätzlich im Transkript-Tab pruefen, dass es keinen "Stimme erzeugen"-Button mehr gibt:
  ```tsx
  it("transcript rail in Zusammenfügen has no voiceover button", async () => {
    const c = makeClient();
    const { queryByRole, findByText } = render(
      <AssembleView client={c} projectId="p" roughCutId={null} onSeekScene={vi.fn()} />,
    );
    await findByText(/Szenen-Bin/);
    expect(queryByRole("button", { name: "Stimme erzeugen" })).toBeNull();
  });
  ```
- [ ] **Step 2: Test laufen lassen — erwartet FAIL.**
  ```
  npm --prefix apps/desktop run test -- src/components/AssembleView.test.tsx
  ```
  Erwartung: FAIL — "Lipsync (Deepfake)" und "Reenact (Identitäts-Ebene)" sind heute im Tools-Tab vorhanden; "Stimme erzeugen" existiert im Transkript-Tab.
- [ ] **Step 3: Minimal-Implementierung — Editorial-Tools entfernen.**
  - Imports streichen: `LipsyncPanel`, `ReenactPanel`, `TransitionReviewPanel`, `useJobStatus`, `VoiceoverVoice` (Zeilen 23, 28, 30, 32 + der Type-Import). `voices`-State und der `listVoiceoverVoices`-Effect (Zeile 464-478) entfallen.
  - `TranscriptBlockEditor` auf read-only reduzieren: VO-State (`voiceBusy`, `voMode`, `voiceId`, `activeJobId`, `activeJobKindRef`, `useJobStatus`, `generateVoiceover`, `saveAndRealign`-Job-Verdrahtung), den Voice-/VO-Block (Zeile 334-368) und die Job-Status-Box (Zeile 301-324) entfernen. Es bleibt: Text-Anzeige + "Speichern + neu ausrichten" (realign, ohne VO). `onVoiceoverCreated`-Prop aus `TranscriptBlockEditor`/`SequenceTranscriptPanel` entfernen.
  - Im Tools-Tab (Zeile 916-991) `TransitionReviewPanel`, `ReenactPanel`, `LipsyncPanel` und die "KI-Status"-Section entfernen. Es bleiben `AudioLaneControls`, `DemoAssistantPanel`, `OverlayControls`. Der `onVoiceoverCreated`-Callback am `SequenceTranscriptPanel` (Zeile 911-914) entfaellt.
  - `assetOptions` bleibt (von `OverlayControls` genutzt); `rateNum`/`rateDen` bleiben Props (von `OverlayControls`).
  Resultierender Tools-Tab-JSX:
  ```tsx
  ) : (
    <div className="flex flex-col gap-3">
      <AudioLaneControls
        client={client}
        timelineId={sequence?.timeline_id ?? null}
        assets={assets}
        onChange={() => {
          reloadSeqClips();
          reloadAudioClips();
          void reloadSequence();
          reloadTranscript();
        }}
      />
      <DemoAssistantPanel
        client={client}
        assets={assets}
        onApplied={() => {
          void reloadSequence();
          reloadSeqClips();
          reloadAudioClips();
          reloadTranscript();
        }}
      />
      <OverlayControls
        client={client}
        timelineId={sequence?.timeline_id ?? null}
        assets={assetOptions}
        onChange={() => {
          reloadSeqClips();
          void reloadSequence();
          reloadTranscript();
        }}
        currentSeqFrame={seqFrame}
        rateNum={rateNum}
        rateDen={rateDen}
      />
    </div>
  )}
  ```
- [ ] **Step 4: Test laufen lassen — erwartet PASS.** Außerdem sicherstellen, dass keine ungenutzten Imports/Variablen uebrig sind (sonst schlaegt `tsc` in E5 fehl).
  ```
  npm --prefix apps/desktop run test -- src/components/AssembleView.test.tsx
  ```
  Erwartung: PASS.
- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/AssembleView.tsx apps/desktop/src/components/AssembleView.test.tsx
  git commit -m "refactor(ui): slim Zusammenfügen to sequence tools; move VO/Lipsync/Reenact to Feinschnitt"
  ```

---

### Task E5: EditorialToolsBar im FineCutView verdrahten — keine Tot-Knoepfe

Die editorischen Tools sind nun aus Zusammenfuegen entfernt (E4) und muessen im Feinschnitt erreichbar sein. `FineCutView` rendert die `EditorialToolsBar` (aus Phase C) unter dem Player; sie traegt Stimmenwahl (VO), Uebergaenge-glaetten und — via E3 — Reenact. Lipsync ist probe-gated automatisch (Phase C); ein manueller Lipsync-Knopf entfaellt damit. Diese Task stellt sicher, dass der Feinschnitt die Toolbar mit den korrekten Props (timelineId des Rough-Cut, assets, rate) versorgt und kein toter Pfad bleibt.

**Files:**
- Modify `apps/desktop/src/components/FineCutView.tsx` (EditorialToolsBar unter dem Player; Props aus Rough-Cut-Timeline + asset)
- Modify `apps/desktop/src/components/FineCutView.test.tsx` (EditorialToolsBar gerendert; Voice-Picker sichtbar)

**Interfaces:**
- Consumes (Phase C): `EditorialToolsBar` mit `{ client, projectId, timelineId, assets, currentSeqFrame, rateNum, rateDen, voiceId, onVoiceChange, onChange }`.
- Consumes (vorhanden): `FineCutView`-Props ([FineCutView.tsx:31-41](apps/desktop/src/components/FineCutView.tsx)); `scene.timeline.id` als `timelineId`, `asset.project_id` als `projectId`.
- Produces: `FineCutView` rendert genau **eine** `EditorialToolsBar` unter dem Player; manuelles `LipsyncPanel` wird **nicht** mehr separat gerendert.

- [ ] **Step 1: Failing-Test schreiben.** In `FineCutView.test.tsx` ergaenzen (Mock-Client + Szenen-Setup wie im File ueblich):
  ```tsx
  it("renders the EditorialToolsBar with a voice picker under the player", async () => {
    const c = makeClient(); // bestehender Helper
    const { findByLabelText } = render(
      <FineCutView
        client={c}
        asset={makeAsset("video-1")}
        roughCutId="rc-1"
        segments={[]}
        currentFrame={0}
        seek={null}
        onSeek={vi.fn()}
        onFrame={vi.fn()}
      />,
    );
    expect(await findByLabelText("Stimme")).not.toBeNull();
  });
  ```
- [ ] **Step 2: Test laufen lassen — erwartet FAIL.**
  ```
  npm --prefix apps/desktop run test -- src/components/FineCutView.test.tsx
  ```
  Erwartung: FAIL — `Unable to find a label with the text of: Stimme` (keine EditorialToolsBar im Feinschnitt).
- [ ] **Step 3: Minimal-Implementierung.** Import + State + Render in `FineCutView.tsx`:
  ```tsx
  import { EditorialToolsBar } from "./EditorialToolsBar";
  // ... innerhalb der Komponente, z. B. neben selectedClipId:
  const [voiceId, setVoiceId] = useState<string>("");
  ```
  Direkt nach dem Player-`<div>` (nach Zeile 197, vor `<TimelineBar ...>`) einfuegen:
  ```tsx
  <EditorialToolsBar
    client={client}
    projectId={asset?.project_id ?? null}
    timelineId={scene.timeline?.id ?? null}
    assets={(scene.timeline?.clips ?? [])
      .map((cl) => cl.asset_id)
      .filter((id): id is string => id !== null)
      .map((id) => ({ id, display_name: asset?.display_name ?? "Video" }))}
    currentSeqFrame={currentFrame}
    rateNum={asset?.rate_num ?? 30}
    rateDen={asset?.rate_den ?? 1}
    voiceId={voiceId}
    onVoiceChange={setVoiceId}
    onChange={() => void scene.reload()}
  />
  ```
  (Hinweis: Phase C definiert `EditorialToolsBar` so, dass `assets` `{id; display_name}[]` sind; oben werden die Clip-Assets dedupliziert-projiziert. Falls `EditorialToolsBar` eine eigene Voice-Liste laedt, ist `voiceId`/`onVoiceChange` der einzige extern gehaltene Zustand.)
- [ ] **Step 4: Test laufen lassen — erwartet PASS.**
  ```
  npm --prefix apps/desktop run test -- src/components/FineCutView.test.tsx
  ```
  Erwartung: PASS.
- [ ] **Step 5: Commit.**
  ```
  git add apps/desktop/src/components/FineCutView.tsx apps/desktop/src/components/FineCutView.test.tsx
  git commit -m "feat(ui): mount EditorialToolsBar (voice/transitions/reenact) under the Feinschnitt player"
  ```

---

### Task E6: Phasenuebergreifende gruene Verifikation (tsc + vitest)

Letzte Task der Phase E **und** der Spec: Typecheck + komplette vitest-Suite ueber die in den Phasen A–E beruehrten Frontend-Dateien gruen. Diese Task schreibt keinen neuen Code — sie verifiziert und committet nur, falls noetig, einen reinen Aufraeum-Fix (z. B. ungenutzte Imports nach E4).

**Files:**
- (nur Verifikation; ggf. Mini-Fix in bereits beruehrten Dateien)

**Interfaces:**
- Consumes: gesamter `apps/desktop`-Build.
- Produces: gruener `tsc --noEmit` + gruene vitest-Suite.

- [ ] **Step 1: Typecheck laufen lassen.**
  ```
  npm --prefix apps/desktop run typecheck
  ```
  Erwartung: PASS, keine Fehler. Haeufigste reale Fehlerquelle nach E4: ein verwaister Import oder ungenutzte Variable (`voices`, `useJobStatus`, `VoiceoverVoice`, `ReenactPanel`, `LipsyncPanel`, `TransitionReviewPanel`) in `AssembleView.tsx`. Falls `tsc` so etwas meldet, den verwaisten Import/State entfernen.
- [ ] **Step 2: Vollständige vitest-Suite laufen lassen.**
  ```
  npm --prefix apps/desktop run test -- --run
  ```
  Erwartung: PASS — insbesondere `LipsyncPanel.test.tsx`, `DemoAssistantPanel.test.tsx`, `EditorialToolsBar.test.tsx`, `AssembleView.test.tsx`, `FineCutView.test.tsx`, `useJobStatus`-konsumierende Tests, `ContinuousTranscript`/`useRoughCutTranscript`-Tests (aus Phasen A/C). Output enthaelt `Test Files  N passed (N)` ohne `failed`.
- [ ] **Step 3: Lint laufen lassen (kein `console.log`, kein `any`).**
  ```
  npm --prefix apps/desktop run lint
  ```
  Erwartung: PASS — keine `no-explicit-any`- oder `no-console`-Verstoesse in den beruehrten Dateien.
- [ ] **Step 4: Manuell-zu-pruefen-Liste dokumentieren (headless nicht pruefbar, live CDP 9222).** Im PR/Abschluss notieren — **nicht** als Code:
  - Zusammenfuegen-Tools-Tab zeigt nur Audio-Lane, Demo-Draft, Overlay (kein VO/Lipsync/Reenact/TransitionReview).
  - Feinschnitt-Player zeigt darunter die `EditorialToolsBar` mit Stimmenwahl, Uebergaenge, Reenact (manuell ausklappbar); Synthetik-Hinweis-Zeile immer an.
  - LipsyncPanel- und Demo-Draft-Status laufen sichtbar bis Terminal (kein dauerhaftes "Job gestartet"/falscher Status).
  - Reenact-Aktion in der Toolbar oeffnet/schliesst das Panel; kein Tot-Knopf.
- [ ] **Step 5: Commit (falls in Step 1 ein Aufraeum-Fix noetig war; sonst entfaellt der Commit).**
  ```
  git add apps/desktop/src/components/AssembleView.tsx
  git commit -m "chore(ui): drop orphaned editorial imports after slimming Zusammenfügen"
  ```
