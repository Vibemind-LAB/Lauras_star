# Pipeline Foundation (Nav-Rail + Download/Import Galleries) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Introduce the 6-stage left nav-rail and turn Download + Import into card-gallery views (reusing the existing download/import backend), with stages 3–5 routed to today's editor and Export stubbed — the runnable foundation of the editorial pipeline.

**Architecture:** New self-contained React components (`NavRail`, `MediaCard`, `DownloadView`, `ImportView`) built and unit-tested first with zero `App.tsx` churn; then a single careful `App.tsx` wiring task adds a `stage` state and switches the main area between the new views and the existing editor body. No backend changes.

**Tech Stack:** Electron 33, React 18 + TS strict (no `any`), Tailwind, Vitest + Testing Library (jsdom; NO jest-dom matchers — use plain assertions).

**Working directory:** `apps/desktop`. Commands: `npm --prefix apps/desktop run typecheck`, `npm --prefix apps/desktop test`, single file: `npm --prefix apps/desktop test -- <name>`.

**Git hygiene:** Branch `feat/editorial-pipeline` (do not switch). Commit only the files each task lists; `git status` before commit. If `No space left on device`, STOP and report BLOCKED.

**Spec:** [`docs/superpowers/specs/2026-06-03-editorial-pipeline-architecture-design.md`](../specs/2026-06-03-editorial-pipeline-architecture-design.md)

**Reused (already built):** `LauraClient.importAsset/importAssetFromUrl/getImportStatus/retryImport`, `useImportStatus`, `DropZone`, `ImportProgress`, `window.laura.pickMediaFiles/pickFolder/listMediaInFolder/pathForFile`, `client.fileObjectUrl(assetId,"poster")`, `Asset` type.

---

## File Structure
- Create: `apps/desktop/src/components/NavRail.tsx` — the 6-stage left rail (pure presentational).
- Create: `apps/desktop/src/pipeline/stages.ts` — the `Stage` type + ordered stage metadata.
- Create: `apps/desktop/src/components/MediaCard.tsx` — gallery card (thumb + title + meta + progress).
- Create: `apps/desktop/src/components/DownloadView.tsx` — download gallery + URL add.
- Create: `apps/desktop/src/components/ImportView.tsx` — import gallery + picker/drop.
- Modify: `apps/desktop/src/App.tsx` — `stage` state, render `NavRail`, switch main area.
- Tests: `stages.test.ts`, `NavRail.test.tsx`, `MediaCard.test.tsx`.

---

## Task 1: Stage metadata

**Files:** Create `apps/desktop/src/pipeline/stages.ts`, Test `apps/desktop/src/pipeline/stages.test.ts`

- [ ] **Step 1: Write the failing test** — `stages.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { STAGES, type Stage } from "./stages";

describe("pipeline stages", () => {
  it("defines the six stages in order", () => {
    expect(STAGES.map((s) => s.id)).toEqual([
      "download", "import", "roughcut", "finecut", "assemble", "export",
    ]);
  });
  it("every stage has a label", () => {
    expect(STAGES.every((s) => s.label.length > 0)).toBe(true);
  });
  it("Stage type accepts a known id", () => {
    const s: Stage = "import";
    expect(STAGES.some((x) => x.id === s)).toBe(true);
  });
});
```

- [ ] **Step 2: Run → fail.** `npm --prefix apps/desktop test -- stages` → cannot resolve module.

- [ ] **Step 3: Implement** `stages.ts`:
```ts
export type Stage = "download" | "import" | "roughcut" | "finecut" | "assemble" | "export";

export interface StageMeta {
  id: Stage;
  label: string;
}

export const STAGES: readonly StageMeta[] = [
  { id: "download", label: "Download" },
  { id: "import", label: "Import" },
  { id: "roughcut", label: "Rough Cut" },
  { id: "finecut", label: "Feinschnitt" },
  { id: "assemble", label: "Zusammenfügen" },
  { id: "export", label: "Export" },
] as const;
```

- [ ] **Step 4: Run → pass.** `npm --prefix apps/desktop test -- stages` → 3 passed.

- [ ] **Step 5: Typecheck + commit.**
```
npm --prefix apps/desktop run typecheck
git add apps/desktop/src/pipeline/stages.ts apps/desktop/src/pipeline/stages.test.ts
git status
git commit -m "$(printf 'feat(desktop): pipeline stage metadata\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 2: NavRail component

**Files:** Create `apps/desktop/src/components/NavRail.tsx`, Test `NavRail.test.tsx`

- [ ] **Step 1: Write the failing test** — `NavRail.test.tsx`:
```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { NavRail } from "./NavRail";

describe("NavRail", () => {
  it("renders all six stages and marks the active one", () => {
    render(<NavRail active="import" onSelect={vi.fn()} />);
    expect(screen.getByText("Download")).toBeTruthy();
    expect(screen.getByText("Export")).toBeTruthy();
    const active = screen.getByRole("button", { name: "Import" });
    expect(active.getAttribute("aria-current")).toBe("page");
  });
  it("calls onSelect with the stage id when clicked", () => {
    const onSelect = vi.fn();
    render(<NavRail active="download" onSelect={onSelect} />);
    fireEvent.click(screen.getByRole("button", { name: "Export" }));
    expect(onSelect).toHaveBeenCalledWith("export");
  });
});
```

- [ ] **Step 2: Run → fail.** `npm --prefix apps/desktop test -- NavRail`.

- [ ] **Step 3: Implement** `NavRail.tsx`:
```tsx
import type { ReactElement } from "react";

import { STAGES, type Stage } from "../pipeline/stages";

export function NavRail({
  active,
  onSelect,
}: {
  active: Stage;
  onSelect: (stage: Stage) => void;
}): ReactElement {
  return (
    <nav className="flex w-44 shrink-0 flex-col gap-1 border-r border-edge bg-panel p-2">
      {STAGES.map((s, i) => (
        <button
          key={s.id}
          type="button"
          aria-current={s.id === active ? "page" : undefined}
          onClick={() => onSelect(s.id)}
          className={`flex items-center gap-2 rounded px-3 py-2 text-left text-sm transition ${
            s.id === active
              ? "bg-sky-600/20 text-sky-300"
              : "text-slate-300 hover:bg-edge hover:text-white"
          }`}
        >
          <span className="w-4 text-right text-[10px] tabular-nums text-slate-500">{i + 1}</span>
          {s.label}
        </button>
      ))}
    </nav>
  );
}
```

- [ ] **Step 4: Run → pass.** `npm --prefix apps/desktop test -- NavRail` → 2 passed.

- [ ] **Step 5: Typecheck + commit.**
```
npm --prefix apps/desktop run typecheck
git add apps/desktop/src/components/NavRail.tsx apps/desktop/src/components/NavRail.test.tsx
git status
git commit -m "$(printf 'feat(desktop): NavRail with the six pipeline stages\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 3: MediaCard component

**Files:** Create `apps/desktop/src/components/MediaCard.tsx`, Test `MediaCard.test.tsx`

A gallery card: thumbnail (optional), title, meta line, `⋯` slot, and an optional progress footer (reuses `ImportProgress`).

- [ ] **Step 1: Write the failing test** — `MediaCard.test.tsx`:
```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ImportStatus } from "../api";
import { MediaCard } from "./MediaCard";

const dl = (over: Partial<ImportStatus>): ImportStatus => ({
  phase: "downloading", downloaded_bytes: null, total_bytes: null,
  speed_bps: null, eta_seconds: null, error: null, ...over,
});

describe("MediaCard", () => {
  it("renders title and meta", () => {
    render(<MediaCard title="Clip A" meta="MP4 · 1080p" onClick={vi.fn()} onRetry={vi.fn()} />);
    expect(screen.getByText("Clip A")).toBeTruthy();
    expect(screen.getByText("MP4 · 1080p")).toBeTruthy();
  });
  it("fires onClick when the card body is clicked", () => {
    const onClick = vi.fn();
    render(<MediaCard title="Clip A" onClick={onClick} onRetry={vi.fn()} />);
    fireEvent.click(screen.getByText("Clip A"));
    expect(onClick).toHaveBeenCalledOnce();
  });
  it("shows a progress footer when status is non-terminal", () => {
    render(
      <MediaCard
        title="Clip B"
        status={dl({ downloaded_bytes: 50, total_bytes: 100, speed_bps: 10 })}
        onClick={vi.fn()}
        onRetry={vi.fn()}
      />,
    );
    expect(screen.getByText(/50%/)).toBeTruthy();
  });
});
```

- [ ] **Step 2: Run → fail.** `npm --prefix apps/desktop test -- MediaCard`.

- [ ] **Step 3: Implement** `MediaCard.tsx`:
```tsx
import { type ReactElement, type ReactNode, useEffect, useState } from "react";

import type { ImportStatus } from "../api";
import { ImportProgress } from "./ImportProgress";

export function MediaCard({
  title,
  meta,
  thumbnail,
  status,
  onClick,
  onRetry,
  menu,
}: {
  title: string;
  meta?: string;
  thumbnail?: Promise<string> | string | null;
  status?: ImportStatus | null;
  onClick: () => void;
  onRetry: () => void;
  menu?: ReactNode;
}): ReactElement {
  const [src, setSrc] = useState<string | null>(typeof thumbnail === "string" ? thumbnail : null);

  useEffect(() => {
    if (thumbnail == null || typeof thumbnail === "string") return;
    let active = true;
    void thumbnail.then((url) => {
      if (active) setSrc(url);
    }).catch(() => undefined);
    return () => {
      active = false;
    };
  }, [thumbnail]);

  return (
    <div className="flex flex-col overflow-hidden rounded-lg border border-edge bg-panel">
      <button
        type="button"
        onClick={onClick}
        className="flex aspect-video w-full items-center justify-center bg-black text-slate-700"
      >
        {src ? (
          <img src={src} alt="" className="h-full w-full object-cover" />
        ) : (
          <span className="text-xs">kein Vorschaubild</span>
        )}
      </button>
      <div className="flex items-start justify-between gap-2 p-2">
        <button type="button" onClick={onClick} className="min-w-0 text-left">
          <div className="truncate text-sm text-slate-100">{title}</div>
          {meta && <div className="truncate text-[11px] text-slate-500">{meta}</div>}
        </button>
        {menu}
      </div>
      {status && (
        <div className="px-2 pb-2">
          <ImportProgress status={status} onRetry={onRetry} />
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run → pass.** `npm --prefix apps/desktop test -- MediaCard` → 3 passed.

- [ ] **Step 5: Typecheck + commit.**
```
npm --prefix apps/desktop run typecheck
git add apps/desktop/src/components/MediaCard.tsx apps/desktop/src/components/MediaCard.test.tsx
git status
git commit -m "$(printf 'feat(desktop): MediaCard gallery card with progress footer\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 4: ImportView (gallery of project media)

**Files:** Create `apps/desktop/src/components/ImportView.tsx`

Presentational-ish: receives `client`, `projectId`, `assets`, and import callbacks (the App already owns these from the prior feature). Renders the `ImportBar` header + a `MediaCard` grid. No new tests (it composes already-tested units); verified by typecheck + the App wiring smoke in Task 6.

- [ ] **Step 1: Implement** `ImportView.tsx`:
```tsx
import { type ReactElement, useMemoimport } from "react";
```
Replace that broken first line — use exactly:
```tsx
import { type ReactElement } from "react";

import type { Asset, LauraClient } from "../api";
import { ImportBar } from "./ImportBar";
import { MediaCard } from "./MediaCard";
import { useImportStatus } from "../hooks/useImportStatus";

function assetMeta(a: Asset): string {
  const kind = a.type === "audio" ? "Audio" : "Video";
  const res = a.width && a.height ? `${a.width}×${a.height}` : "";
  return [kind, res].filter(Boolean).join(" · ");
}

function ImportCard({ client, asset }: { client: LauraClient; asset: Asset }): ReactElement {
  const settled = asset.files?.some((f) => f.kind === "waveform" || f.kind === "proxy") ?? false;
  const status = useImportStatus(client, settled ? null : asset.id);
  return (
    <MediaCard
      title={asset.display_name}
      meta={assetMeta(asset)}
      thumbnail={client.fileObjectUrl(asset.id, "poster").catch(() => "")}
      status={status}
      onClick={() => undefined}
      onRetry={() => void client.retryImport(asset.id)}
    />
  );
}

export function ImportView({
  client,
  disabled,
  assets,
  onUrl,
  onPickFiles,
  onPickFolder,
}: {
  client: LauraClient;
  disabled: boolean;
  assets: Asset[];
  onUrl: (url: string) => void;
  onPickFiles: () => void;
  onPickFolder: () => void;
}): ReactElement {
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4">
      <div className="mb-3 max-w-md">
        <ImportBar disabled={disabled} onUrl={onUrl} onPickFiles={onPickFiles} onPickFolder={onPickFolder} />
      </div>
      {assets.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
          Dateien/Ordner/Links hier ablegen oder importieren.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {assets.map((a) => (
            <ImportCard key={a.id} client={client} asset={a} />
          ))}
        </div>
      )}
    </div>
  );
}
```
(Delete the broken `useMemoimport` line entirely — it is shown only to flag that the first import line must be exactly `import { type ReactElement } from "react";`.)

- [ ] **Step 2: Typecheck.** `npm --prefix apps/desktop run typecheck` → no errors.

- [ ] **Step 3: Commit.**
```
git add apps/desktop/src/components/ImportView.tsx
git status
git commit -m "$(printf 'feat(desktop): ImportView gallery (MediaCard grid + ImportBar)\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 5: DownloadView (gallery of url-imported / in-flight assets)

**Files:** Create `apps/desktop/src/components/DownloadView.tsx`

For the Foundation, the Download view reuses the same asset list but focuses the header on URL entry and shows the same per-asset progress. (A dedicated downloads-only filter/queue is a later refinement.)

- [ ] **Step 1: Implement** `DownloadView.tsx`:
```tsx
import { type FormEvent, type ReactElement, useState } from "react";

import type { Asset, LauraClient } from "../api";
import { MediaCard } from "./MediaCard";
import { useImportStatus } from "../hooks/useImportStatus";

function DownloadCard({ client, asset }: { client: LauraClient; asset: Asset }): ReactElement {
  const settled = asset.files?.some((f) => f.kind === "waveform" || f.kind === "proxy") ?? false;
  const status = useImportStatus(client, settled ? null : asset.id);
  return (
    <MediaCard
      title={asset.display_name}
      meta={asset.online ? "fertig" : "lädt…"}
      thumbnail={client.fileObjectUrl(asset.id, "poster").catch(() => "")}
      status={status}
      onClick={() => undefined}
      onRetry={() => void client.retryImport(asset.id)}
    />
  );
}

export function DownloadView({
  client,
  disabled,
  assets,
  onUrl,
}: {
  client: LauraClient;
  disabled: boolean;
  assets: Asset[];
  onUrl: (url: string) => void;
}): ReactElement {
  const [url, setUrl] = useState("");
  const submit = (e: FormEvent): void => {
    e.preventDefault();
    const v = url.trim();
    if (v) {
      onUrl(v);
      setUrl("");
    }
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-auto p-4">
      <form onSubmit={submit} className="mb-3 flex max-w-md gap-1">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="Video-URL einfügen (http/s, Magnet)…"
          disabled={disabled}
          className="min-w-0 flex-1 rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 placeholder:text-slate-600"
        />
        <button
          type="submit"
          disabled={disabled || url.trim() === ""}
          className="shrink-0 rounded bg-sky-600 px-3 py-1 text-xs text-white disabled:opacity-40"
        >
          + Download
        </button>
      </form>
      {assets.length === 0 ? (
        <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
          Noch keine Downloads — füge eine URL hinzu oder ziehe einen Link ins Fenster.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-4">
          {assets.map((a) => (
            <DownloadCard key={a.id} client={client} asset={a} />
          ))}
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Typecheck.** `npm --prefix apps/desktop run typecheck` → no errors.

- [ ] **Step 3: Commit.**
```
git add apps/desktop/src/components/DownloadView.tsx
git status
git commit -m "$(printf 'feat(desktop): DownloadView gallery with URL entry\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 6: Wire NavRail + stage switch into App.tsx

**Files:** Modify `apps/desktop/src/App.tsx`

READ App.tsx fully first. It already has `client`, `selectedProjectId`, `assets`, `runImport`/`importPaths`/`importUrls`, the `<DropZone>`, and the editor body (`<main>` 3-col + `TimelineBar` + `TranscriptBar`). Integrate without breaking the editor.

- [ ] **Step 1: Add imports + stage state**
```tsx
import { NavRail } from "./components/NavRail";
import { DownloadView } from "./components/DownloadView";
import { ImportView } from "./components/ImportView";
import type { Stage } from "./pipeline/stages";
```
Add state inside `App`: `const [stage, setStage] = useState<Stage>("import");`

- [ ] **Step 2: Restructure the layout to place the NavRail on the left**
Wrap the existing content so the rail sits left of the main area. Replace the outer structure: keep the `<DropZone>` and `<header>` and `error` banner, then render a horizontal flex row: `<NavRail active={stage} onSelect={setStage} />` next to a stage-switched main region:
```tsx
      <div className="flex min-h-0 flex-1">
        <NavRail active={stage} onSelect={setStage} />
        <div className="flex min-h-0 flex-1 flex-col">
          {stage === "download" && client && (
            <DownloadView
              client={client}
              disabled={!selectedProjectId}
              assets={assets}
              onUrl={(u) => void importUrls([u])}
            />
          )}
          {stage === "import" && client && (
            <ImportView
              client={client}
              disabled={!selectedProjectId}
              assets={assets}
              onUrl={(u) => void importUrls([u])}
              onPickFiles={() => { void (async () => { try { const f = await window.laura.pickMediaFiles(); if (f.length) await importPaths(f); } catch (e) { setError(String(e)); } })(); }}
              onPickFolder={() => { void (async () => { try { const folder = await window.laura.pickFolder(); if (folder) await importPaths(await window.laura.listMediaInFolder(folder)); } catch (e) { setError(String(e)); } })(); }}
            />
          )}
          {(stage === "roughcut" || stage === "finecut" || stage === "assemble") && (
            <EXISTING_EDITOR_BODY />
          )}
          {stage === "export" && (
            <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
              Export — kommt als nächste Stufe.
            </div>
          )}
        </div>
      </div>
```
Where `EXISTING_EDITOR_BODY` means: move the current `<main className="grid ...">…</main>` + `<TimelineBar/>` + `<TranscriptBar/>` block here UNCHANGED (wrap them in a `<>…</>` fragment). Do not alter their internals. The project picker (`Projekte` list) currently lives inside that `<main>`'s left column — leave it there for now (reachable via the editor stages); a dedicated global project picker is a later refinement.

IMPORTANT: adapt to the ACTUAL current JSX (variable names, the exact editor block). The goal: rail always visible; `download`/`import` show the new galleries; `roughcut`/`finecut`/`assemble` show today's editor unchanged; `export` shows the stub. Remove the now-duplicated old `ImportBar`/`AssetImportRow` usage from the editor's media column ONLY if it would double-render with ImportView — otherwise leave the editor untouched. If unsure, keep the editor body 100% intact and just gate it behind the three stages.

- [ ] **Step 2b: Verify the editor still works**
The `detailAsset`, `analysis`, `roughCut`, player/timeline/transcript wiring must remain functional in the roughcut/finecut/assemble stages.

- [ ] **Step 3: Typecheck + test**
```
npm --prefix apps/desktop run typecheck
npm --prefix apps/desktop test
```
Expected: no type errors; all existing tests pass.

- [ ] **Step 4: Manual smoke (MANUAL — document, don't execute)**
Start app: rail shows 6 stages; Import shows the media gallery with progress cards; Download shows URL entry + gallery; Rough Cut/Feinschnitt/Zusammenfügen show the existing editor; Export shows the stub. Switching stages preserves the selected project.

- [ ] **Step 5: Commit**
```
git add apps/desktop/src/App.tsx
git status
git commit -m "$(printf 'feat(desktop): nav-rail stage switch; Download/Import galleries\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 7: Full verification
- [ ] `npm --prefix apps/desktop run typecheck` → clean.
- [ ] `npm --prefix apps/desktop test` → all pass.
- [ ] Confirm no `apps/desktop` file other than the intended ones changed across the branch (`git diff --stat main..HEAD -- apps/desktop`).

## Notes
- No backend changes in the Foundation — Download/Import reuse the existing fetch/import pipeline.
- Stages 3–5 intentionally share today's editor for now; the Rough-Cut / Feinschnitt / Zusammenfügen sub-projects replace each with its purpose-built view.
- Download vs Import currently show the same asset set; a download-only filter is a later refinement (noted in the spec).
