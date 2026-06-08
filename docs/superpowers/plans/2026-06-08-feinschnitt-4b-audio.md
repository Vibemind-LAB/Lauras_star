# Feinschnitt 4b (Audio/Music) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Add one music track per scene — pick an imported asset as music with a gain, persist it as scene metadata, and mix it into the MP4 render (which is currently video-only).

**Architecture:** Music is scene metadata (`scenes.music_asset_id` + `music_gain_percent`, already added by migration `0009`) — not a timeline clip, so the editing core stays untouched. A music API sets/clears it. `render_clips_mp4` gains an optional `music=(path, gain_percent)` param that adds an audio input + `volume`/`atrim` filter and maps a second output stream; with no music it is byte-for-byte the current video-only behavior (backward compatible). The render job handler looks up the rendered timeline's scene and passes its music. Frontend: a `SceneMusicControls` (picker + gain + best-effort `<audio>` preview) inside the existing `FineCutView`. **No `App.tsx` change.**

**Tech Stack / conventions:** identical to `2026-06-08-feinschnitt-4a-core.md` (uv/pytest/ruff/mypy strict, TS strict no `any`, vitest plain asserts, Conventional Commits, frame/sample invariants). **GIT HYGIENE:** stage only listed files; never `.claude/`/`build/`/`uv.lock`; don't switch branches.

**Reference spec:** `docs/superpowers/specs/2026-06-08-feinschnitt-stage-design.md` §5–§6.

**Verbatim anchors (current code to extend):**
- `render/mp4.py` `render_clips_mp4(clips: list[tuple[Path, int, int]], dest: Path, *, rate_num: int, rate_den: int) -> None` — builds `[i:v]trim=start_frame=…:end_frame=…,setpts=PTS-STARTPTS[vi]`, then `…concat=n=N:v=1:a=0[out]`, runs ffmpeg with `-map "[out]" -c:v libx264 -pix_fmt yuv420p -r num/den`.
- `render/handlers.py` `handle_render`: builds `clips` from `repos.list_timeline_clips`, computes `dest`, calls `render_clips_mp4(clips, dest, rate_num=project["sequence_rate_num"], rate_den=project["sequence_rate_den"])` inside a `try/except Exception` that persists `set_export_error` + unlinks partial output.
- `SceneOut` already has `music_asset_id: str | None`, `music_gain_percent: int` (4a). `repos.get_asset` returns `source_path`. Scene repos (`get_scene`, `list_scenes`) `SELECT *` so music columns are already returned.

---

### Task 1: Music repos — `set_scene_music` / `clear_scene_music` / `get_scene_by_timeline`

**Files:** Modify `services/local-api/src/laura/db/repos.py` (append); Test `services/local-api/tests/test_scene_music_repo.py`

- [ ] **Step 1: Failing test**

```python
# services/local-api/tests/test_scene_music_repo.py
from __future__ import annotations
from pathlib import Path
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase


def _db(tmp_path: Path) -> SqliteDatabase:
    db = SqliteDatabase(Settings(workspace_root=tmp_path / "ws", start_runner=False).db_path)
    db.migrate()
    return db


def test_set_and_clear_scene_music(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_scenes(db, "p1", "tl1", [(0, 30)])
    sid = repos.list_scenes(db, "tl1")[0]["id"]
    repos.set_scene_music(db, sid, "asset-9", 150)
    s = repos.get_scene(db, sid)
    assert s["music_asset_id"] == "asset-9" and s["music_gain_percent"] == 150
    repos.clear_scene_music(db, sid)
    s = repos.get_scene(db, sid)
    assert s["music_asset_id"] is None and s["music_gain_percent"] == 100


def test_get_scene_by_timeline(tmp_path: Path) -> None:
    db = _db(tmp_path)
    repos.replace_scenes(db, "p1", "tl1", [(0, 30)])
    sid = repos.list_scenes(db, "tl1")[0]["id"]
    repos.set_scene_timeline(db, sid, "scene-tl-7")
    assert repos.get_scene_by_timeline(db, "scene-tl-7")["id"] == sid
    assert repos.get_scene_by_timeline(db, "nope") is None
```

- [ ] **Step 2: Run — expect FAIL.** `uv run --directory services/local-api pytest tests/test_scene_music_repo.py -q`

- [ ] **Step 3: Implement** — append to `repos.py`:

```python
def set_scene_music(db: Database, scene_id: str, asset_id: str, gain_percent: int) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE scenes SET music_asset_id=?, music_gain_percent=? WHERE id=?",
            (asset_id, gain_percent, scene_id),
        )


def clear_scene_music(db: Database, scene_id: str) -> None:
    with db.transaction() as conn:
        conn.execute(
            "UPDATE scenes SET music_asset_id=NULL, music_gain_percent=100 WHERE id=?",
            (scene_id,),
        )


def get_scene_by_timeline(db: Database, scene_timeline_id: str) -> dict[str, Any] | None:
    with db.connection() as conn:
        row = conn.execute(
            "SELECT * FROM scenes WHERE scene_timeline_id=? LIMIT 1", (scene_timeline_id,)
        ).fetchone()
        return dict(row) if row is not None else None
```

- [ ] **Step 4: Run — expect PASS.** ruff + mypy on `repos.py`.
- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/db/repos.py services/local-api/tests/test_scene_music_repo.py
git commit -m "feat(feinschnitt): scene music repos"
```

---

### Task 2: Music API — `PUT`/`DELETE /scenes/{id}/music`

**Files:** Modify `services/local-api/src/laura/api/models.py` (request model); Modify `services/local-api/src/laura/api/scenes.py` (two endpoints); Test `services/local-api/tests/test_scene_music_api.py`

- [ ] **Step 1: Failing test** (mirror the fixture from `tests/test_scene_open.py`):

```python
# services/local-api/tests/test_scene_music_api.py
from __future__ import annotations
from pathlib import Path
from fastapi.testclient import TestClient
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.main import create_app

_TOKEN = "test-token"


def _app(tmp_path: Path):
    app = create_app(Settings(workspace_root=tmp_path / "ws", start_runner=False, token=_TOKEN))
    return TestClient(app), app.state.db


def test_set_then_clear_music(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    project = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p")
    music = repos.create_asset(db, project_id=project["id"], type="audio", display_name="m", source_path="/tmp/m.mp3")
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], tl["id"], [(0, 30)])
    sid = repos.list_scenes(db, tl["id"])[0]["id"]
    h = {"X-Laura-Token": _TOKEN}
    r = client.put(f"/scenes/{sid}/music", json={"asset_id": music["id"], "gain_percent": 120}, headers=h)
    assert r.status_code == 200, r.text
    assert r.json()["music_asset_id"] == music["id"] and r.json()["music_gain_percent"] == 120
    r2 = client.request("DELETE", f"/scenes/{sid}/music", headers=h)
    assert r2.status_code == 200
    assert r2.json()["music_asset_id"] is None


def test_set_music_unknown_asset_404(tmp_path: Path) -> None:
    client, db = _app(tmp_path)
    project = repos.create_project(db, name="p", rate_num=30, rate_den=1, drop_frame=False, workspace_root="/tmp/p")
    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_scenes(db, project["id"], tl["id"], [(0, 30)])
    sid = repos.list_scenes(db, tl["id"])[0]["id"]
    r = client.put(f"/scenes/{sid}/music", json={"asset_id": "nope", "gain_percent": 100}, headers={"X-Laura-Token": _TOKEN})
    assert r.status_code == 404
```

- [ ] **Step 2: Run — expect FAIL.**

- [ ] **Step 3a: Model** — append to `api/models.py`:

```python
class SetSceneMusicRequest(BaseModel):
    asset_id: str
    gain_percent: int = Field(default=100, ge=0, le=400)
```

- [ ] **Step 3b: Endpoints** — add to `api/scenes.py` (import `SetSceneMusicRequest`, `SceneOut`):

```python
@router.put("/scenes/{scene_id}/music", response_model=SceneOut)
def set_scene_music(scene_id: str, body: SetSceneMusicRequest, request: Request) -> SceneOut:
    db = _db(request)
    scene = repos.get_scene(db, scene_id)
    if scene is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    if repos.get_asset(db, body.asset_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "asset not found")
    repos.set_scene_music(db, scene_id, body.asset_id, body.gain_percent)
    updated = repos.get_scene(db, scene_id)
    assert updated is not None
    return SceneOut(**updated)


@router.delete("/scenes/{scene_id}/music", response_model=SceneOut)
def clear_scene_music(scene_id: str, request: Request) -> SceneOut:
    db = _db(request)
    scene = repos.get_scene(db, scene_id)
    if scene is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "scene not found")
    repos.clear_scene_music(db, scene_id)
    updated = repos.get_scene(db, scene_id)
    assert updated is not None
    return SceneOut(**updated)
```

- [ ] **Step 4: Run — expect PASS.** No regression: `uv run --directory services/local-api pytest tests/test_scenes_api.py tests/test_scene_open.py -q`. ruff + mypy.
- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/api/models.py services/local-api/src/laura/api/scenes.py services/local-api/tests/test_scene_music_api.py
git commit -m "feat(feinschnitt): scene music API"
```

---

### Task 3: Render audio-mix (`render_clips_mp4` music param + handler lookup)

**Files:** Modify `services/local-api/src/laura/render/mp4.py`; Modify `services/local-api/src/laura/render/handlers.py`; Test `services/local-api/tests/test_render_music.py`

- [ ] **Step 1: Failing test** (real ffmpeg; skip if absent — mirror `tests/test_render_mp4.py`'s skip + `_clip` helper):

```python
# services/local-api/tests/test_render_music.py
import os
import shutil
import subprocess
import pytest
from laura.ingest.ffmpeg import run_ffmpeg
from laura.render.mp4 import render_clips_mp4

pytestmark = pytest.mark.skipif(
    shutil.which(os.environ.get("LAURA_FFMPEG", "ffmpeg")) is None, reason="ffmpeg")


def _video(tmp_path, secs):
    p = tmp_path / "v.mp4"
    run_ffmpeg(["-f", "lavfi", "-i", f"testsrc=duration={secs}:size=320x240:rate=30",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(p)])
    return p


def _audio(tmp_path, secs):
    p = tmp_path / "m.m4a"
    run_ffmpeg(["-f", "lavfi", "-i", f"sine=frequency=440:duration={secs}",
                "-c:a", "aac", str(p)])
    return p


def _has_audio(path) -> bool:
    ffprobe = os.environ.get("LAURA_FFPROBE", "ffprobe")
    out = subprocess.run([ffprobe, "-v", "error", "-select_streams", "a",
                          "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(path)],
                         capture_output=True, text=True)
    return "audio" in out.stdout


def test_render_with_music_has_audio_stream(tmp_path):
    v = _video(tmp_path, 2)
    m = _audio(tmp_path, 5)
    out = tmp_path / "out.mp4"
    render_clips_mp4([(v, 0, 30)], out, rate_num=30, rate_den=1, music=(m, 100))
    assert out.exists() and _has_audio(out)


def test_render_without_music_has_no_audio(tmp_path):
    v = _video(tmp_path, 1)
    out = tmp_path / "out2.mp4"
    render_clips_mp4([(v, 0, 30)], out, rate_num=30, rate_den=1)
    assert out.exists() and not _has_audio(out)
```

- [ ] **Step 2: Run — expect FAIL** (`render_clips_mp4() got an unexpected keyword argument 'music'`).

- [ ] **Step 3a: Extend `render_clips_mp4`** in `render/mp4.py`:

```python
from pathlib import Path

from ..ingest.ffmpeg import run_ffmpeg


def render_clips_mp4(
    clips: list[tuple[Path, int, int]],
    dest: Path,
    *,
    rate_num: int,
    rate_den: int,
    music: tuple[Path, int] | None = None,
) -> None:
    """Trim each clip by frame range (end-exclusive) and concat into one MP4. With ``music``
    (path, gain_percent) a single audio track is mixed at that gain, trimmed to the video
    length. Without ``music`` the output is video-only (unchanged)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    filt: list[str] = []
    for i, (src, fin, fout) in enumerate(clips):
        inputs += ["-i", str(src)]
        filt.append(
            f"[{i}:v]trim=start_frame={fin}:end_frame={fout},setpts=PTS-STARTPTS[v{i}]"
        )
    concat_in = "".join(f"[v{i}]" for i in range(len(clips)))
    parts = ";".join(filt) + f";{concat_in}concat=n={len(clips)}:v=1:a=0[out]"
    audio_maps: list[str] = []
    if music is not None:
        music_path, gain_percent = music
        total = sum(fout - fin for _, fin, fout in clips)
        dur = total * rate_den / rate_num
        inputs += ["-i", str(music_path)]  # input index == len(clips)
        parts += (
            f";[{len(clips)}:a]volume={gain_percent / 100},"
            f"atrim=0:{dur},asetpts=PTS-STARTPTS[aout]"
        )
        audio_maps = ["-map", "[aout]", "-c:a", "aac"]
    run_ffmpeg([
        *inputs,
        "-filter_complex", parts,
        "-map", "[out]",
        *audio_maps,
        "-c:v", "libx264",
        "-pix_fmt", "yuv420p",
        "-r", f"{rate_num}/{rate_den}",
        str(dest),
    ])
```

- [ ] **Step 3b: Handler lookup** in `render/handlers.py` — just before the `render_clips_mp4(...)` call, resolve the scene's music and pass it:

```python
    music: tuple[Path, int] | None = None
    scene = repos.get_scene_by_timeline(ctx.db, exp["timeline_id"])
    if scene is not None and scene.get("music_asset_id"):
        masset = repos.get_asset(ctx.db, scene["music_asset_id"])
        if masset is not None:
            music = (Path(masset["source_path"]), int(scene["music_gain_percent"]))
```

Then change the call to `render_clips_mp4(clips, dest, rate_num=..., rate_den=..., music=music)`.

- [ ] **Step 4: Run — expect PASS** (both: with-music → audio stream; without → none). Regression: `uv run --directory services/local-api pytest tests/test_render_mp4.py tests/test_render_job.py tests/test_render_job_errors.py -q`. ruff + mypy.
- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/render/mp4.py services/local-api/src/laura/render/handlers.py services/local-api/tests/test_render_music.py
git commit -m "feat(feinschnitt): mix scene music into MP4 render"
```

---

### Task 4: Frontend — music client methods + `SceneMusicControls` in `FineCutView`

**Files:** Modify `apps/desktop/src/api.ts` (two methods); Create `apps/desktop/src/components/SceneMusicControls.tsx`; Modify `apps/desktop/src/components/FineCutView.tsx`; Test `apps/desktop/src/components/SceneMusicControls.test.tsx`

- [ ] **Step 1: api.ts methods** (collision guard `git status --short apps/desktop/src/api.ts` first; the `Scene` music fields already exist as optional from 4a):

```typescript
  setSceneMusic(sceneId: string, assetId: string, gainPercent: number): Promise<Scene> {
    return this.request<Scene>(`/scenes/${sceneId}/music`, {
      method: "PUT",
      body: JSON.stringify({ asset_id: assetId, gain_percent: gainPercent }),
    });
  }

  removeSceneMusic(sceneId: string): Promise<Scene> {
    return this.request<Scene>(`/scenes/${sceneId}/music`, { method: "DELETE" });
  }
```

- [ ] **Step 2: Failing test** for `SceneMusicControls`:

```typescript
// apps/desktop/src/components/SceneMusicControls.test.tsx
import { fireEvent, render, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { type Asset, type LauraClient, type Scene } from "../api";
import { SceneMusicControls } from "./SceneMusicControls";

const scene: Scene = { id: "s1", project_id: "p", source_timeline_id: "tl", name: "Szene 1",
  order_index: 0, seq_in_frame: 0, seq_out_frame_exclusive: 30,
  music_asset_id: null, music_gain_percent: 100 };
const assets = [{ id: "m1", display_name: "song.mp3", type: "audio" }] as unknown as Asset[];

function client(over: Partial<LauraClient>): LauraClient {
  return { listAssets: vi.fn().mockResolvedValue(assets),
    setSceneMusic: vi.fn().mockResolvedValue({ ...scene, music_asset_id: "m1" }),
    removeSceneMusic: vi.fn().mockResolvedValue(scene), ...over } as unknown as LauraClient;
}

describe("SceneMusicControls", () => {
  it("sets music for the scene", async () => {
    const c = client({});
    const onChange = vi.fn();
    const { getByText, getByRole } = render(
      <SceneMusicControls client={c} projectId="p" scene={scene} onChange={onChange} />);
    await waitFor(() => expect(c.listAssets).toHaveBeenCalledWith("p"));
    fireEvent.change(getByRole("combobox"), { target: { value: "m1" } });
    fireEvent.click(getByText("Musik setzen"));
    await waitFor(() => expect(c.setSceneMusic).toHaveBeenCalledWith("s1", "m1", 100));
    expect(onChange).toHaveBeenCalled();
  });
});
```

- [ ] **Step 3: Implement `SceneMusicControls`** — `apps/desktop/src/components/SceneMusicControls.tsx`:

```typescript
import { type ReactElement, useEffect, useState } from "react";

import { type Asset, type LauraClient, type Scene } from "../api";

export function SceneMusicControls({
  client, projectId, scene, onChange,
}: {
  client: LauraClient;
  projectId: string | null;
  scene: Scene;
  onChange: () => void;
}): ReactElement {
  const [assets, setAssets] = useState<Asset[]>([]);
  const [pick, setPick] = useState<string>(scene.music_asset_id ?? "");
  const [gain, setGain] = useState<number>(scene.music_gain_percent ?? 100);

  useEffect(() => {
    if (!projectId) return;
    void client.listAssets(projectId).then(setAssets).catch(() => undefined);
  }, [client, projectId]);

  const apply = async (): Promise<void> => {
    if (!pick) return;
    await client.setSceneMusic(scene.id, pick, gain);
    onChange();
  };
  const clear = async (): Promise<void> => {
    await client.removeSceneMusic(scene.id);
    onChange();
  };

  return (
    <div className="flex items-center gap-2 border-t border-edge px-3 py-2 text-xs">
      <span className="text-slate-400">Musik</span>
      <select value={pick} onChange={(e) => setPick(e.target.value)}
        className="rounded bg-slate-800 px-2 py-1 text-slate-100">
        <option value="">— keine —</option>
        {assets.map((a) => <option key={a.id} value={a.id}>{a.display_name}</option>)}
      </select>
      <label className="flex items-center gap-1 text-slate-400">
        Gain
        <input type="range" min={0} max={400} value={gain}
          onChange={(e) => setGain(Number(e.target.value))} />
        <span className="w-10 tabular-nums">{gain}%</span>
      </label>
      <button type="button" onClick={() => void apply()} disabled={!pick}
        className="rounded bg-sky-600 px-2 py-1 text-white disabled:opacity-40">Musik setzen</button>
      {scene.music_asset_id && (
        <button type="button" onClick={() => void clear()}
          className="rounded bg-slate-700 px-2 py-1 text-slate-200">entfernen</button>
      )}
    </div>
  );
}
```

> Best-effort `<audio>` preview is optional for v1 — if `api.ts` already exposes an asset media-URL accessor (check for `assetMediaUrl`/`proxyUrl`/`laura-media`), you MAY add a small `<audio controls>` of the picked asset; otherwise omit it (the spec marks preview as best-effort).

- [ ] **Step 4: Wire into `FineCutView`** — render `<SceneMusicControls client={client} projectId={asset?.project_id ?? null} scene={selectedScene} onChange={() => void reloadScenes()} />` for the currently selected scene (derive `selectedScene` from the `useScenes` list by `selectedSceneId`, and call the hook's `reload` on change). Keep it additive; the existing `FineCutView` test must still pass (it mocks children — add a `vi.mock("./SceneMusicControls", …)` there if the real one fetches assets on mount, OR provide `listAssets` in that test's fake client).

- [ ] **Step 5: Run** `npm --prefix apps/desktop test -- SceneMusicControls` and the full suite + `npm --prefix apps/desktop run typecheck` — all green.
- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/api.ts apps/desktop/src/components/SceneMusicControls.tsx apps/desktop/src/components/FineCutView.tsx apps/desktop/src/components/FineCutView.test.tsx
git commit -m "feat(feinschnitt): scene music UI (picker + gain)"
```

---

## Final verification (4b)
```
uv run --directory services/local-api pytest tests/test_scene_music_repo.py tests/test_scene_music_api.py tests/test_render_music.py -q
uv run --directory services/local-api pytest tests/test_render_mp4.py tests/test_render_job.py tests/test_exports_api.py -q   # no regression
npm --prefix apps/desktop run typecheck && npm --prefix apps/desktop test
```
Manual (headless can't verify audio playback): import an audio file → Feinschnitt → pick it as scene music + gain → export the scene → confirm the MP4 has the music.

## Spec coverage (4b)
| Spec § | Task |
|---|---|
| §5.1 Music API | 1,2 |
| §5.2 Render-Mix + handler lookup | 3 |
| §6 Music UI (picker + gain) | 4 |

## Deferred (per spec §11)
No ducking, no loop, one music asset per scene, no cross-scene sequence audio (Stage 5), `<audio>` preview best-effort only.
