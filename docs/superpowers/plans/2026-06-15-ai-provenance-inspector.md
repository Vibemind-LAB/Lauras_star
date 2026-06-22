# AI Provenance Inspector Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Laura's AI provenance sidecars inspectable from the local API and visible in the media bin for selected synthetic assets.

**Architecture:** Keep the manifest file as the source of truth and expose it through a narrow read-only asset endpoint. The renderer only fetches provenance for the selected synthetic asset, so the media bin stays light and non-synthetic media avoid extra requests.

**Tech Stack:** FastAPI, Pydantic-style API models by `dict[str, Any]`, React/TypeScript strict, Vitest, pytest.

---

### Task 1: Backend Provenance Read API

**Files:**
- Modify: `services/local-api/tests/test_api_assets.py`
- Modify: `services/local-api/src/laura/api/assets.py`

- [ ] **Step 1: Write the failing API test**

```python
def test_get_asset_provenance_returns_manifest(
    client: TestClient, db: Database, tmp_path: Path
) -> None:
    project_id = _make_project(client)
    media = tmp_path / "ai.wav"
    media.write_bytes(b"voice")
    asset = repos.create_asset(
        db,
        project_id=project_id,
        type="audio",
        display_name="voice.wav",
        source_path=str(media),
        synthetic=True,
        ai_effect="voiceover",
    )
    manifest = {
        "schema": "laura.ai.provenance.v1",
        "asset_id": asset["id"],
        "project_id": project_id,
        "synthetic": True,
        "ai_effect": "voiceover",
        "media_sha256": "abc",
        "source": {"timeline_id": "tl-1"},
    }
    Path(f"{media}.laura-provenance.json").write_text(json.dumps(manifest), encoding="utf-8")

    resp = client.get(f"/assets/{asset['id']}/provenance")

    assert resp.status_code == 200
    assert resp.json()["asset_id"] == asset["id"]
    assert resp.json()["source"]["timeline_id"] == "tl-1"
```

- [ ] **Step 2: Verify RED**

Run: `uv run pytest tests/test_api_assets.py::test_get_asset_provenance_returns_manifest -q`
Expected: `404 Not Found`, because the endpoint does not exist.

- [ ] **Step 3: Implement minimal endpoint**

Add `GET /assets/{asset_id}/provenance` in `api/assets.py`:
- 404 when asset is unknown
- 404 when the sidecar is missing
- 409 when the manifest's `asset_id` does not match the requested asset
- return the parsed JSON object otherwise

- [ ] **Step 4: Verify GREEN**

Run: `uv run pytest tests/test_api_assets.py::test_get_asset_provenance_returns_manifest -q`
Expected: pass.

### Task 2: Frontend Client Contract

**Files:**
- Modify: `apps/desktop/src/api.test.ts`
- Modify: `apps/desktop/src/api.ts`

- [ ] **Step 1: Write the failing client test**

```ts
it("GETs asset provenance", async () => {
  const fn = mockFetch({ schema: "laura.ai.provenance.v1", asset_id: "a1" });
  const c = new LauraClient("http://h", "tok");
  await c.getAssetProvenance("a1");
  expect(fn).toHaveBeenCalledWith("http://h/assets/a1/provenance", expect.anything());
});
```

- [ ] **Step 2: Verify RED**

Run: `pnpm --dir apps/desktop test -- src/api.test.ts -t "GETs asset provenance"`
Expected: TypeScript/Vitest failure because `getAssetProvenance` is missing.

- [ ] **Step 3: Implement minimal client type + method**

Add an `AiProvenanceManifest` interface with the fields rendered by the UI and a `getAssetProvenance(assetId)` method that calls `/assets/{id}/provenance`.

- [ ] **Step 4: Verify GREEN**

Run: `pnpm --dir apps/desktop test -- src/api.test.ts -t "GETs asset provenance"`
Expected: pass.

### Task 3: Media Bin Provenance Visibility

**Files:**
- Modify: `apps/desktop/src/components/MediaSidebar.test.tsx`
- Modify: `apps/desktop/src/components/MediaSidebar.tsx`

- [ ] **Step 1: Write the failing UI test**

```ts
it("shows provenance details for the selected synthetic asset", async () => {
  const getAssetProvenance = vi.fn().mockResolvedValue({
    schema: "laura.ai.provenance.v1",
    ai_effect: "lipsync",
    media_sha256: "0123456789abcdef",
    source: { timeline_id: "tl-1" },
  });
  const client = makeClient({ getAssetProvenance });
  const synthetic = { ...makeAsset("synthetic-1", "lipsync-output.mp4"), synthetic: true, ai_effect: "lipsync" };
  render(<MediaSidebar client={client} assets={[synthetic]} selectedAssetId="synthetic-1" onSelect={vi.fn()} />);

  expect(await screen.findByText("Provenance")).toBeTruthy();
  expect(screen.getByText("sha256 0123456789ab")).toBeTruthy();
});
```

- [ ] **Step 2: Verify RED**

Run: `pnpm --dir apps/desktop test -- src/components/MediaSidebar.test.tsx -t "provenance details"`
Expected: failure because no detail panel exists.

- [ ] **Step 3: Implement selected-row provenance fetch**

Fetch provenance only when `isSelected && asset.synthetic`, render a compact block with schema/effect/hash, and show clear missing/error states.

- [ ] **Step 4: Verify GREEN**

Run: `pnpm --dir apps/desktop test -- src/components/MediaSidebar.test.tsx -t "provenance details"`
Expected: pass.

### Task 4: Gates and Living Todo

**Files:**
- Modify: `tasks/todo.md`

- [ ] **Step 1: Run focused gates**

Run:
- `uv run pytest tests/test_api_assets.py tests/test_ai_provenance.py -q`
- `uv run ruff check src/laura/api/assets.py tests/test_api_assets.py`
- `uv run mypy src/laura/api/assets.py`
- `pnpm --dir apps/desktop test -- src/api.test.ts src/components/MediaSidebar.test.tsx`
- `pnpm --dir apps/desktop exec tsc --noEmit`

- [ ] **Step 2: Update `tasks/todo.md`**

Append a checked `VV7 AI Provenance Inspector v1` line under `VibeVideo-Integration`.

- [ ] **Step 3: Run final gates**

Run:
- `uv run pytest -q`
- `uv run ruff check .`
- `uv run mypy src`
- `pnpm --dir apps/desktop test`
- `pnpm --dir apps/desktop run build:renderer`

---

## Self-Review

Spec coverage: API read path, client contract, UI visibility, and verification are covered.

Placeholder scan: no `TBD`, `TODO`, or unspecified "appropriate handling" remains.

Type consistency: backend endpoint name maps to `LauraClient.getAssetProvenance`, and UI reads `AiProvenanceManifest.media_sha256`.
