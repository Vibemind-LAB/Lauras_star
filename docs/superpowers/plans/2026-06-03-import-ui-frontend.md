# Import UI (Phase 2 — Frontend) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A desktop import UI — drag&drop files, drag&drop/paste URLs, folder/batch — with real per-asset progress bars (MB/%, speed, ETA), phase labels, and a retry button, all driven by the Phase-1 backend endpoints.

**Architecture:** A window-level `DropZone` overlay classifies drops (files → local path via `webUtils`; folder → enumerate in main; URL → `source_url` import). An `ImportBar` offers a URL field + file/folder pickers. Each importing asset renders an `ImportProgress` fed by a `useImportStatus` polling hook hitting `GET /assets/{id}/import-status`.

**Tech Stack:** Electron 33 (preload `webUtils`, IPC), React 18 + TypeScript (strict, no `any`), Tailwind, Vitest + Testing Library.

**Working directory:** `apps/desktop`. Commands: `npm --prefix apps/desktop run <script>` from repo root, or `npm run <script>` inside `apps/desktop`. Scripts: `typecheck` (`tsc --noEmit`), `test` (`vitest run`), `lint` (`eslint .`).

**Git hygiene:** Commit only the listed files per task; `git status` before commit. Branch: `docs/resilient-url-ingest-spec`. NOTE: this branch already carries the user's in-progress desktop refactor — only stage files YOU create/modify for this plan.

**Depends on:** Phase 1 backend plan (`2026-06-03-import-progress-backend.md`) — the endpoints `GET /assets/{id}/import-status`, `POST /assets/{id}/import-retry`, and the existing `source_url` import must exist. Implement Phase 1 first.

**Spec:** [`docs/superpowers/specs/2026-06-03-import-ui-and-progress-design.md`](../specs/2026-06-03-import-ui-and-progress-design.md)

---

## File Structure

- Modify: `apps/desktop/src/main.ts` — IPC: `laura:pick-files`, `laura:pick-folder`, `laura:list-media-in-folder`.
- Modify: `apps/desktop/src/preload.ts` — bridge: `pathForFile`, `pickMediaFiles`, `pickFolder`, `listMediaInFolder`.
- Modify: `apps/desktop/src/api.ts` — `ImportStatus` type + `importAssetFromUrl`, `getImportStatus`, `retryImport`.
- Create: `apps/desktop/src/import/format.ts` — `formatBytes`, `formatSpeed`, `formatEta` (pure, unit-tested).
- Create: `apps/desktop/src/import/classifyDrop.ts` — pure drop classifier (unit-tested).
- Create: `apps/desktop/src/hooks/useImportStatus.ts` — polling hook.
- Create: `apps/desktop/src/components/DropZone.tsx`
- Create: `apps/desktop/src/components/ImportBar.tsx`
- Create: `apps/desktop/src/components/ImportProgress.tsx`
- Modify: `apps/desktop/src/App.tsx` — render the above, wire handlers.
- Tests: `apps/desktop/src/import/format.test.ts`, `classifyDrop.test.ts`, `src/api.test.ts` (extend), `src/hooks/useImportStatus.test.ts`, `src/components/ImportProgress.test.tsx`.

---

## Task 1: Pure format helpers

**Files:**
- Create: `apps/desktop/src/import/format.ts`
- Test: `apps/desktop/src/import/format.test.ts`

- [ ] **Step 1: Write the failing test** — `apps/desktop/src/import/format.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { formatBytes, formatEta, formatSpeed } from "./format";

describe("format helpers", () => {
  it("formats bytes in binary units", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(30 * 1024 ** 3)).toBe("30.0 GiB");
  });
  it("formats speed per second", () => {
    expect(formatSpeed(5 * 1024 ** 2)).toBe("5.0 MiB/s");
    expect(formatSpeed(null)).toBe("");
  });
  it("formats eta as m:ss / h:mm:ss", () => {
    expect(formatEta(0)).toBe("0:00");
    expect(formatEta(75)).toBe("1:15");
    expect(formatEta(3661)).toBe("1:01:01");
    expect(formatEta(null)).toBe("");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm --prefix apps/desktop test -- format` → FAIL (module missing).

- [ ] **Step 3: Implement** `apps/desktop/src/import/format.ts`:
```ts
const UNITS = ["B", "KiB", "MiB", "GiB", "TiB"] as const;

export function formatBytes(n: number): string {
  if (n <= 0) return "0 B";
  let i = 0;
  let v = n;
  while (v >= 1024 && i < UNITS.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${i === 0 ? v.toFixed(0) : v.toFixed(1)} ${UNITS[i]}`;
}

export function formatSpeed(bps: number | null | undefined): string {
  if (bps == null || bps <= 0) return "";
  return `${formatBytes(bps)}/s`;
}

export function formatEta(seconds: number | null | undefined): string {
  if (seconds == null || seconds < 0) return "";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  const pad = (x: number): string => x.toString().padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(sec)}` : `${m}:${pad(sec)}`;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm --prefix apps/desktop test -- format` → PASS.

- [ ] **Step 5: Typecheck + commit**

Run: `npm --prefix apps/desktop run typecheck`
```bash
git add apps/desktop/src/import/format.ts apps/desktop/src/import/format.test.ts
git status
git commit -m "$(printf 'feat(desktop): byte/speed/eta format helpers\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 2: Pure drop classifier

**Files:**
- Create: `apps/desktop/src/import/classifyDrop.ts`
- Test: `apps/desktop/src/import/classifyDrop.test.ts`

The classifier is pure: given a minimal view of a `DataTransfer`, decide what to import. The component (Task 6) supplies the real DataTransfer + the `pathForFile` bridge.

- [ ] **Step 1: Write the failing test** — `apps/desktop/src/import/classifyDrop.test.ts`:
```ts
import { describe, expect, it } from "vitest";

import { classifyDrop, type DropInput } from "./classifyDrop";

const input = (over: Partial<DropInput>): DropInput => ({
  filePaths: [],
  directoryPaths: [],
  uriText: "",
  ...over,
});

describe("classifyDrop", () => {
  it("classifies dropped files", () => {
    expect(classifyDrop(input({ filePaths: ["/a/x.mp4", "/a/y.mov"] }))).toEqual([
      { kind: "file", path: "/a/x.mp4" },
      { kind: "file", path: "/a/y.mov" },
    ]);
  });
  it("classifies a directory", () => {
    expect(classifyDrop(input({ directoryPaths: ["/a/folder"] }))).toEqual([
      { kind: "folder", path: "/a/folder" },
    ]);
  });
  it("classifies an http(s) url from uri-list", () => {
    expect(classifyDrop(input({ uriText: "https://x/y.mp4\r\n" }))).toEqual([
      { kind: "url", url: "https://x/y.mp4" },
    ]);
  });
  it("classifies a magnet url", () => {
    expect(classifyDrop(input({ uriText: "magnet:?xt=urn:btih:abc" }))).toEqual([
      { kind: "url", url: "magnet:?xt=urn:btih:abc" },
    ]);
  });
  it("ignores non-url text", () => {
    expect(classifyDrop(input({ uriText: "just text" }))).toEqual([]);
  });
  it("prefers files over text when both present", () => {
    expect(classifyDrop(input({ filePaths: ["/a/x.mp4"], uriText: "https://x/y" }))).toEqual([
      { kind: "file", path: "/a/x.mp4" },
    ]);
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm --prefix apps/desktop test -- classifyDrop` → FAIL.

- [ ] **Step 3: Implement** `apps/desktop/src/import/classifyDrop.ts`:
```ts
export interface DropInput {
  filePaths: string[];
  directoryPaths: string[];
  uriText: string;
}

export type DropItem =
  | { kind: "file"; path: string }
  | { kind: "folder"; path: string }
  | { kind: "url"; url: string };

const URL_RE = /^(https?:|ftp:|ftps:|sftp:|magnet:)/i;

export function classifyDrop(input: DropInput): DropItem[] {
  if (input.filePaths.length > 0 || input.directoryPaths.length > 0) {
    return [
      ...input.filePaths.map((path) => ({ kind: "file" as const, path })),
      ...input.directoryPaths.map((path) => ({ kind: "folder" as const, path })),
    ];
  }
  const text = input.uriText.trim().split(/\r?\n/)[0]?.trim() ?? "";
  if (text && URL_RE.test(text)) {
    return [{ kind: "url", url: text }];
  }
  return [];
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm --prefix apps/desktop test -- classifyDrop` → PASS.

- [ ] **Step 5: Typecheck + commit**

Run: `npm --prefix apps/desktop run typecheck`
```bash
git add apps/desktop/src/import/classifyDrop.ts apps/desktop/src/import/classifyDrop.test.ts
git status
git commit -m "$(printf 'feat(desktop): pure drop classifier (file/folder/url)\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 3: API client methods

**Files:**
- Modify: `apps/desktop/src/api.ts`
- Test: `apps/desktop/src/api.test.ts`

- [ ] **Step 1: Write the failing test** — append to `apps/desktop/src/api.test.ts`:
```ts
import { afterEach, vi } from "vitest";
import { LauraClient } from "./api";

afterEach(() => vi.restoreAllMocks());

function mockFetch(json: unknown) {
  const fn = vi.fn().mockResolvedValue({
    ok: true, status: 200, json: async () => json, text: async () => "",
  });
  vi.stubGlobal("fetch", fn);
  return fn;
}

describe("import client methods", () => {
  it("importAssetFromUrl posts source_url", async () => {
    const fn = mockFetch({ asset_id: "a", job_id: "j" });
    const c = new LauraClient("http://h", "tok");
    await c.importAssetFromUrl("p1", "https://x/y.mp4");
    expect(fn).toHaveBeenCalledWith(
      "http://h/projects/p1/assets/import",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ source_url: "https://x/y.mp4" }) }),
    );
  });

  it("getImportStatus GETs the status", async () => {
    const fn = mockFetch({ phase: "downloading", downloaded_bytes: 1, total_bytes: 2 });
    const c = new LauraClient("http://h", "tok");
    const st = await c.getImportStatus("a1");
    expect(fn).toHaveBeenCalledWith("http://h/assets/a1/import-status", expect.anything());
    expect(st.phase).toBe("downloading");
  });

  it("retryImport posts import-retry", async () => {
    const fn = mockFetch({ asset_id: "a1", job_id: "j" });
    const c = new LauraClient("http://h", "tok");
    await c.retryImport("a1");
    expect(fn).toHaveBeenCalledWith(
      "http://h/assets/a1/import-retry",
      expect.objectContaining({ method: "POST" }),
    );
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm --prefix apps/desktop test -- api` → FAIL (methods missing).

- [ ] **Step 3: Implement** — add the type near the other exported types in `api.ts`:
```ts
export interface ImportStatus {
  phase: "queued" | "downloading" | "verifying" | "analyzing" | "ready" | "error";
  downloaded_bytes: number | null;
  total_bytes: number | null;
  speed_bps: number | null;
  eta_seconds: number | null;
  error: string | null;
}
```
and add the methods inside `LauraClient` (next to `importAsset`):
```ts
  importAssetFromUrl(projectId: string, url: string): Promise<ImportAccepted> {
    return this.request<ImportAccepted>(`/projects/${projectId}/assets/import`, {
      method: "POST",
      body: JSON.stringify({ source_url: url }),
    });
  }

  getImportStatus(assetId: string): Promise<ImportStatus> {
    return this.request<ImportStatus>(`/assets/${assetId}/import-status`);
  }

  retryImport(assetId: string): Promise<ImportAccepted> {
    return this.request<ImportAccepted>(`/assets/${assetId}/import-retry`, { method: "POST" });
  }
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm --prefix apps/desktop test -- api` → PASS.

- [ ] **Step 5: Typecheck + commit**

Run: `npm --prefix apps/desktop run typecheck`
```bash
git add apps/desktop/src/api.ts apps/desktop/src/api.test.ts
git status
git commit -m "$(printf 'feat(desktop): api client for url import, status, retry\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 4: Preload bridge + main IPC

**Files:**
- Modify: `apps/desktop/src/preload.ts`
- Modify: `apps/desktop/src/main.ts`

This is Electron main/preload wiring — not unit-testable headless; verify via typecheck + a manual smoke note.

- [ ] **Step 1: Extend the preload bridge** in `apps/desktop/src/preload.ts`:

Change the electron import to include `webUtils`:
```ts
import { contextBridge, ipcRenderer, webUtils } from "electron";
```
Add to the `bridge` object:
```ts
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
  pickMediaFiles: (): Promise<string[]> => ipcRenderer.invoke("laura:pick-files"),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke("laura:pick-folder"),
  listMediaInFolder: (folder: string): Promise<string[]> =>
    ipcRenderer.invoke("laura:list-media-in-folder", folder),
```
(`LauraBridge` type is `typeof bridge`, so `window.laura` picks these up automatically.)

- [ ] **Step 2: Add the main-process handlers** in `apps/desktop/src/main.ts`

Add near the top with the other node imports:
```ts
import { readdir } from "node:fs/promises";
```
Add a media-extension constant and handlers (register alongside the existing `ipcMain.handle("laura:pick-file", ...)`):
```ts
const MEDIA_EXTS = new Set([
  ".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mxf", ".mpg", ".mpeg",
  ".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".aac",
]);

ipcMain.handle("laura:pick-files", async (): Promise<string[]> => {
  const r = await dialog.showOpenDialog({ properties: ["openFile", "multiSelections"] });
  return r.canceled ? [] : r.filePaths;
});

ipcMain.handle("laura:pick-folder", async (): Promise<string | null> => {
  const r = await dialog.showOpenDialog({ properties: ["openDirectory"] });
  return r.canceled || r.filePaths.length === 0 ? null : r.filePaths[0];
});

ipcMain.handle("laura:list-media-in-folder", async (_e, folder: string): Promise<string[]> => {
  const entries = await readdir(folder, { withFileTypes: true });
  return entries
    .filter((d) => d.isFile() && MEDIA_EXTS.has(path.extname(d.name).toLowerCase()))
    .map((d) => path.join(folder, d.name));
});
```
(`dialog`, `ipcMain`, `path` are already imported in main.ts.)

- [ ] **Step 3: Typecheck**

Run: `npm --prefix apps/desktop run typecheck` → no errors.

- [ ] **Step 4: Commit**

```bash
git add apps/desktop/src/preload.ts apps/desktop/src/main.ts
git status
git commit -m "$(printf 'feat(desktop): preload/main bridge for drop paths, multi-pick, folder listing\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 5: `useImportStatus` polling hook

**Files:**
- Create: `apps/desktop/src/hooks/useImportStatus.ts`
- Test: `apps/desktop/src/hooks/useImportStatus.test.ts`

- [ ] **Step 1: Write the failing test** — `apps/desktop/src/hooks/useImportStatus.test.ts`:
```ts
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ImportStatus, LauraClient } from "../api";
import { useImportStatus } from "./useImportStatus";

const status = (phase: ImportStatus["phase"]): ImportStatus => ({
  phase, downloaded_bytes: null, total_bytes: null, speed_bps: null,
  eta_seconds: null, error: null,
});

beforeEach(() => vi.useFakeTimers());
afterEach(() => vi.useRealTimers());

describe("useImportStatus", () => {
  it("polls until ready, then stops", async () => {
    const getImportStatus = vi
      .fn<[string], Promise<ImportStatus>>()
      .mockResolvedValueOnce(status("downloading"))
      .mockResolvedValueOnce(status("ready"));
    const client = { getImportStatus } as unknown as LauraClient;

    const { result } = renderHook(() => useImportStatus(client, "a1", 1000));
    await waitFor(() => expect(result.current?.phase).toBe("downloading"));
    await vi.advanceTimersByTimeAsync(1000);
    await waitFor(() => expect(result.current?.phase).toBe("ready"));

    const callsAfterReady = getImportStatus.mock.calls.length;
    await vi.advanceTimersByTimeAsync(3000);
    expect(getImportStatus.mock.calls.length).toBe(callsAfterReady); // stopped
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm --prefix apps/desktop test -- useImportStatus` → FAIL.

- [ ] **Step 3: Implement** `apps/desktop/src/hooks/useImportStatus.ts`:
```ts
import { useEffect, useState } from "react";

import type { ImportStatus, LauraClient } from "../api";

const TERMINAL: ReadonlySet<ImportStatus["phase"]> = new Set(["ready", "error"]);

export function useImportStatus(
  client: LauraClient,
  assetId: string | null,
  intervalMs = 1000,
): ImportStatus | null {
  const [status, setStatus] = useState<ImportStatus | null>(null);

  useEffect(() => {
    if (assetId == null) {
      setStatus(null);
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async (): Promise<void> => {
      try {
        const s = await client.getImportStatus(assetId);
        if (!active) return;
        setStatus(s);
        if (!TERMINAL.has(s.phase)) {
          timer = setTimeout(poll, intervalMs);
        }
      } catch {
        if (active) timer = setTimeout(poll, intervalMs);
      }
    };
    void poll();

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [client, assetId, intervalMs]);

  return status;
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm --prefix apps/desktop test -- useImportStatus` → PASS.

- [ ] **Step 5: Typecheck + commit**

Run: `npm --prefix apps/desktop run typecheck`
```bash
git add apps/desktop/src/hooks/useImportStatus.ts apps/desktop/src/hooks/useImportStatus.test.ts
git status
git commit -m "$(printf 'feat(desktop): useImportStatus polling hook\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 6: `ImportProgress` component

**Files:**
- Create: `apps/desktop/src/components/ImportProgress.tsx`
- Test: `apps/desktop/src/components/ImportProgress.test.tsx`

A presentational component: given an `ImportStatus` and an `onRetry`, render a bar/phase/speed/eta or an error with a retry button. No data fetching inside (keeps it testable).

- [ ] **Step 1: Write the failing test** — `apps/desktop/src/components/ImportProgress.test.tsx`:
```tsx
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ImportStatus } from "../api";
import { ImportProgress } from "./ImportProgress";

const st = (over: Partial<ImportStatus>): ImportStatus => ({
  phase: "downloading", downloaded_bytes: null, total_bytes: null,
  speed_bps: null, eta_seconds: null, error: null, ...over,
});

describe("ImportProgress", () => {
  it("shows percent + speed while downloading", () => {
    render(<ImportProgress status={st({
      downloaded_bytes: 50 * 1024 ** 2, total_bytes: 100 * 1024 ** 2,
      speed_bps: 5 * 1024 ** 2, eta_seconds: 10,
    })} onRetry={vi.fn()} />);
    expect(screen.getByText(/50%/)).toBeInTheDocument();
    expect(screen.getByText(/5\.0 MiB\/s/)).toBeInTheDocument();
  });

  it("shows error + retry button on error", () => {
    const onRetry = vi.fn();
    render(<ImportProgress status={st({ phase: "error", error: "boom" })} onRetry={onRetry} />);
    expect(screen.getByText(/boom/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /erneut/i }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("renders nothing when ready", () => {
    const { container } = render(<ImportProgress status={st({ phase: "ready" })} onRetry={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `npm --prefix apps/desktop test -- ImportProgress` → FAIL.

- [ ] **Step 3: Implement** `apps/desktop/src/components/ImportProgress.tsx`:
```tsx
import type { ReactElement } from "react";

import type { ImportStatus } from "../api";
import { formatBytes, formatEta, formatSpeed } from "../import/format";

const PHASE_LABEL: Record<ImportStatus["phase"], string> = {
  queued: "Wartet…",
  downloading: "Lädt…",
  verifying: "Prüft…",
  analyzing: "Analysiert…",
  ready: "Fertig",
  error: "Fehler",
};

export function ImportProgress({
  status,
  onRetry,
}: {
  status: ImportStatus;
  onRetry: () => void;
}): ReactElement | null {
  if (status.phase === "ready") return null;

  if (status.phase === "error") {
    return (
      <div className="mt-1 flex items-center gap-2 text-xs text-red-400">
        <span className="truncate" title={status.error ?? undefined}>
          {status.error ?? "Import fehlgeschlagen"}
        </span>
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded bg-slate-700 px-2 py-0.5 text-slate-100 hover:bg-slate-600"
        >
          Erneut versuchen
        </button>
      </div>
    );
  }

  const { downloaded_bytes: dl, total_bytes: total } = status;
  const pct = dl != null && total != null && total > 0 ? Math.floor((dl / total) * 100) : null;
  const detail = [
    pct != null ? `${pct}%` : null,
    dl != null && total != null ? `${formatBytes(dl)} / ${formatBytes(total)}` : null,
    formatSpeed(status.speed_bps),
    status.eta_seconds != null ? `ETA ${formatEta(status.eta_seconds)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="mt-1">
      <div className="h-1.5 w-full overflow-hidden rounded bg-slate-800">
        <div
          className="h-full bg-sky-500 transition-all"
          style={{ width: pct != null ? `${pct}%` : "33%" }}
        />
      </div>
      <div className="mt-0.5 text-[11px] text-slate-500">
        {PHASE_LABEL[status.phase]} {detail && `· ${detail}`}
      </div>
    </div>
  );
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `npm --prefix apps/desktop test -- ImportProgress` → PASS.

- [ ] **Step 5: Typecheck + commit**

Run: `npm --prefix apps/desktop run typecheck`
```bash
git add apps/desktop/src/components/ImportProgress.tsx apps/desktop/src/components/ImportProgress.test.tsx
git status
git commit -m "$(printf 'feat(desktop): ImportProgress component (bar/phase/eta/retry)\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 7: `DropZone` + `ImportBar` components

**Files:**
- Create: `apps/desktop/src/components/DropZone.tsx`
- Create: `apps/desktop/src/components/ImportBar.tsx`

These call `window.laura` + invoke an `onImport` callback with resolved import requests. The classification logic is the already-tested `classifyDrop`; these components are thin adapters (verified by typecheck + the App-level manual smoke in Task 8).

- [ ] **Step 1: Implement** `apps/desktop/src/components/DropZone.tsx`:
```tsx
import { type ReactElement, useCallback, useEffect, useState } from "react";

import { classifyDrop, type DropItem } from "../import/classifyDrop";

export interface ResolvedImport {
  paths: string[]; // local file paths to import via source_path
  urls: string[]; // urls to import via source_url
}

async function resolve(items: DropItem[]): Promise<ResolvedImport> {
  const paths: string[] = [];
  const urls: string[] = [];
  for (const item of items) {
    if (item.kind === "file") paths.push(item.path);
    else if (item.kind === "url") urls.push(item.url);
    else if (item.kind === "folder") paths.push(...(await window.laura.listMediaInFolder(item.path)));
  }
  return { paths, urls };
}

export function DropZone({ onImport }: { onImport: (r: ResolvedImport) => void }): ReactElement {
  const [active, setActive] = useState(false);

  useEffect(() => {
    const over = (e: DragEvent): void => {
      e.preventDefault();
      setActive(true);
    };
    const leave = (e: DragEvent): void => {
      if (e.relatedTarget === null) setActive(false);
    };
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    return () => {
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
    };
  }, []);

  const onDrop = useCallback(
    async (e: React.DragEvent): Promise<void> => {
      e.preventDefault();
      setActive(false);
      const dt = e.dataTransfer;
      const files = Array.from(dt.files);
      const filePaths: string[] = [];
      const directoryPaths: string[] = [];
      for (const f of files) {
        const p = window.laura.pathForFile(f);
        // a directory dropped as a File has no type and no extension dot in name
        if (f.type === "" && !f.name.includes(".")) directoryPaths.push(p);
        else filePaths.push(p);
      }
      const uriText = dt.getData("text/uri-list") || dt.getData("text/plain");
      const items = classifyDrop({ filePaths, directoryPaths, uriText });
      onImport(await resolve(items));
    },
    [onImport],
  );

  return (
    <div
      onDrop={onDrop}
      onDragOver={(e) => e.preventDefault()}
      className={`pointer-events-auto fixed inset-0 z-50 flex items-center justify-center transition ${
        active ? "bg-ink/80 backdrop-blur-sm" : "pointer-events-none opacity-0"
      }`}
    >
      <div className="rounded-2xl border-2 border-dashed border-sky-500/60 px-12 py-10 text-center">
        <div className="text-lg text-slate-200">Dateien, Ordner oder Link hier ablegen</div>
        <div className="mt-1 text-sm text-slate-500">Video-Dateien · ganze Ordner · http(s)/Magnet-Links</div>
      </div>
    </div>
  );
}
```

- [ ] **Step 2: Implement** `apps/desktop/src/components/ImportBar.tsx`:
```tsx
import { type FormEvent, type ReactElement, useState } from "react";

export function ImportBar({
  disabled,
  onUrl,
  onPickFiles,
  onPickFolder,
}: {
  disabled: boolean;
  onUrl: (url: string) => void;
  onPickFiles: () => void;
  onPickFolder: () => void;
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
    <div className="flex flex-col gap-1.5">
      <form onSubmit={submit} className="flex gap-1">
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="URL einfügen (http/s, Magnet)…"
          disabled={disabled}
          className="min-w-0 flex-1 rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 placeholder:text-slate-600"
        />
        <button
          type="submit"
          disabled={disabled || url.trim() === ""}
          className="shrink-0 rounded bg-sky-600 px-2 py-1 text-xs text-white disabled:opacity-40"
        >
          Laden
        </button>
      </form>
      <div className="flex gap-1">
        <button
          type="button"
          onClick={onPickFiles}
          disabled={disabled}
          className="flex-1 rounded bg-slate-700 px-2 py-1 text-xs text-slate-100 hover:bg-slate-600 disabled:opacity-40"
        >
          + Datei(en)
        </button>
        <button
          type="button"
          onClick={onPickFolder}
          disabled={disabled}
          className="flex-1 rounded bg-slate-700 px-2 py-1 text-xs text-slate-100 hover:bg-slate-600 disabled:opacity-40"
        >
          + Ordner
        </button>
      </div>
    </div>
  );
}
```

- [ ] **Step 3: Typecheck + commit**

Run: `npm --prefix apps/desktop run typecheck` → no errors.
```bash
git add apps/desktop/src/components/DropZone.tsx apps/desktop/src/components/ImportBar.tsx
git status
git commit -m "$(printf 'feat(desktop): DropZone overlay + ImportBar (url/file/folder)\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Task 8: Wire into `App.tsx`

**Files:**
- Modify: `apps/desktop/src/App.tsx`

App.tsx already manages `client`, `selectedProjectId`, `assets`, and a refresh loop. Read it first; integrate without breaking the existing single-file import.

- [ ] **Step 1: Add import handlers**

Add these imports at the top of `App.tsx`:
```tsx
import { DropZone, type ResolvedImport } from "./components/DropZone";
import { ImportBar } from "./components/ImportBar";
import { ImportProgress } from "./components/ImportProgress";
import { useImportStatus } from "./hooks/useImportStatus";
```
Add handlers inside the `App` component (reuse the existing `client` and `selectedProjectId`, and whatever asset-refresh function already exists — call it after each import to surface the new asset):
```tsx
  const importPaths = useCallback(
    async (paths: string[]): Promise<void> => {
      if (!client || !selectedProjectId) return;
      for (const p of paths) await client.importAsset(selectedProjectId, p);
      await refreshAssets(); // use the existing refresh fn name in this file
    },
    [client, selectedProjectId],
  );
  const importUrls = useCallback(
    async (urls: string[]): Promise<void> => {
      if (!client || !selectedProjectId) return;
      for (const u of urls) await client.importAssetFromUrl(selectedProjectId, u);
      await refreshAssets();
    },
    [client, selectedProjectId],
  );
  const onDropImport = useCallback(
    (r: ResolvedImport): void => {
      void importPaths(r.paths);
      void importUrls(r.urls);
    },
    [importPaths, importUrls],
  );
```
NOTE: this file is under active refactor by the user — match the ACTUAL names of the client variable and the asset-refresh function already present (e.g. it may be a `loadAssets`/`refresh` closure or an effect trigger). Do not introduce a second refresh mechanism.

- [ ] **Step 2: Render `DropZone` and `ImportBar`**

- Render `<DropZone onImport={onDropImport} />` once near the root of the returned tree.
- Place `<ImportBar disabled={!selectedProjectId} onUrl={(u) => void importUrls([u])} onPickFiles={async () => importPaths(await window.laura.pickMediaFiles())} onPickFolder={async () => { const f = await window.laura.pickFolder(); if (f) importPaths(await window.laura.listMediaInFolder(f)); }} />` in the left sidebar, near the existing "+ Import" control (you may keep or replace the old button).

- [ ] **Step 3: Show per-asset progress in the asset list**

For each asset row that isn't fully ready, render a small wrapper that subscribes to status and shows progress. Add a tiny inline component at the bottom of `App.tsx`:
```tsx
function AssetImportRow({
  client,
  assetId,
}: {
  client: LauraClient;
  assetId: string;
}): ReactElement | null {
  const status = useImportStatus(client, assetId);
  if (!status) return null;
  return <ImportProgress status={status} onRetry={() => void client.retryImport(assetId)} />;
}
```
and render `<AssetImportRow client={client} assetId={a.id} />` under each asset `<li>` in the list (import `LauraClient`/`ReactElement` types as needed). It self-stops polling at ready/error.

- [ ] **Step 4: Verify**

Run: `npm --prefix apps/desktop run typecheck` → no errors.
Run: `npm --prefix apps/desktop test` → all desktop tests pass.

- [ ] **Step 5: Manual smoke (mark manual — not automated)**

Start backend + `npm --prefix apps/desktop start`. Verify: (a) dragging a video file onto the window imports it and shows progress→ready; (b) pasting an http URL in the ImportBar starts a download with a moving bar; (c) dropping a folder imports all media inside; (d) a bad URL ends in an error row with a working "Erneut versuchen". Record anything surprising in `lessons.md`.

- [ ] **Step 6: Commit**

```bash
git add apps/desktop/src/App.tsx
git status
git commit -m "$(printf 'feat(desktop): wire DropZone/ImportBar/ImportProgress into App\n\nCo-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>')"
```

---

## Notes
- The classifier and format helpers are pure and fully unit-tested; the Electron bridge and App wiring are verified by typecheck + a manual smoke (headless GUI/drag-drop isn't automatable here).
- `useImportStatus` self-stops at `ready`/`error`, so idle assets don't poll forever.
- Retry calls `POST import-retry` (Phase 1); the backend re-enqueues a resuming fetch.
