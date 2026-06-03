import { createReadStream } from "node:fs";
import { stat, writeFile } from "node:fs/promises";
import path from "node:path";
import { Readable } from "node:stream";

import { BrowserWindow, app, dialog, ipcMain, net, protocol, shell } from "electron";
import started from "electron-squirrel-startup";

import { startService } from "./service";
import type { ServiceInfo } from "./shared/ipc";
import { log } from "./shared/log";

// Quit early if launched by the Squirrel installer (Windows).
if (started) {
  app.quit();
}

// A privileged scheme to stream large local media (the proxy) straight into <video>.
// The renderer's cross-origin fetch+blob path fails for large bodies (TypeError: Failed
// to fetch) and buffers the whole file in memory; this streams from the main process
// instead — no CORS, native Range seeking, nothing held in the renderer.
protocol.registerSchemesAsPrivileged([
  {
    scheme: "laura-media",
    privileges: { standard: true, secure: true, stream: true, supportFetchAPI: true },
  },
]);

let serviceInfo: ServiceInfo | null = null;
let stopService: (() => void) | null = null;

// assetId/kind -> on-disk path, resolved once via the API and reused for every range.
const mediaPathCache = new Map<string, string>();

async function resolveMediaPath(assetId: string, kind: string): Promise<string | null> {
  const key = `${assetId}/${kind}`;
  const cached = mediaPathCache.get(key);
  if (cached) return cached;
  if (!serviceInfo) return null;
  const res = await net.fetch(`${serviceInfo.baseUrl}/assets/${assetId}`, {
    headers: { "X-Laura-Token": serviceInfo.token },
  });
  if (!res.ok) return null;
  const asset = (await res.json()) as { files?: { kind: string; path: string }[] };
  const file = asset.files?.find((f) => f.kind === kind);
  if (!file) return null;
  mediaPathCache.set(key, file.path);
  return file.path;
}

function createWindow(): void {
  const win = new BrowserWindow({
    width: 1280,
    height: 820,
    backgroundColor: "#0b0f14",
    show: false,
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
    },
  });

  win.once("ready-to-show", () => win.show());

  // Dev: mirror the renderer console into the main-process log so headless debugging
  // (where DevTools isn't visible) can still see renderer errors and diagnostics.
  if (MAIN_WINDOW_VITE_DEV_SERVER_URL) {
    win.webContents.on("console-message", (_e, level, message, line, sourceId) => {
      const tag = ["V", "I", "W", "E"][level] ?? "?";
      log.info(`[renderer ${tag}] ${message} (${sourceId}:${line})`);
    });
  }

  // Security: open external links in the OS browser, never in-app; block navigation.
  win.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    const devUrl = MAIN_WINDOW_VITE_DEV_SERVER_URL;
    if (devUrl && url.startsWith(devUrl)) {
      return;
    }
    event.preventDefault();
  });

  if (MAIN_WINDOW_VITE_DEV_SERVER_URL) {
    // Dev: start from a clean HTTP cache so a stale partial (e.g. a proxy load
    // interrupted by a previous restart) can't trigger a broken Range revalidation.
    void win.webContents.session.clearCache();
    void win.loadURL(MAIN_WINDOW_VITE_DEV_SERVER_URL);
  } else {
    void win.loadFile(path.join(__dirname, `../renderer/${MAIN_WINDOW_VITE_NAME}/index.html`));
  }
}

app
  .whenReady()
  .then(async () => {
    try {
      const svc = await startService();
      serviceInfo = svc.info;
      stopService = svc.stop;
    } catch (err) {
      log.error("failed to start local service", err);
    }

    // laura-media://media/<assetId>/<kind> -> stream the artifact straight off disk with
    // native Range support. Serving large media from the main process (not the loopback
    // uvicorn) avoids the connection-reset churn that made <video> playback flaky.
    const toBody = (stream: Readable): BodyInit => Readable.toWeb(stream) as unknown as BodyInit;
    protocol.handle("laura-media", async (request) => {
      const [assetId, kind] = new URL(request.url).pathname.split("/").filter(Boolean);
      if (!assetId || !kind) {
        return new Response("bad media url", { status: 400 });
      }
      const filePath = await resolveMediaPath(assetId, kind);
      if (!filePath) {
        return new Response("media not found", { status: 404 });
      }
      let total: number;
      try {
        total = (await stat(filePath)).size;
      } catch {
        return new Response("media missing on disk", { status: 404 });
      }
      const base = { "Content-Type": "video/mp4", "Accept-Ranges": "bytes" };
      const m = /bytes=(\d+)-(\d*)/.exec(request.headers.get("Range") ?? "");
      if (m) {
        const start = Number(m[1]);
        const end = m[2] ? Math.min(Number(m[2]), total - 1) : total - 1;
        if (start >= total || start > end) {
          return new Response("range not satisfiable", {
            status: 416,
            headers: { ...base, "Content-Range": `bytes */${total}` },
          });
        }
        return new Response(toBody(createReadStream(filePath, { start, end })), {
          status: 206,
          headers: {
            ...base,
            "Content-Range": `bytes ${start}-${end}/${total}`,
            "Content-Length": String(end - start + 1),
          },
        });
      }
      return new Response(toBody(createReadStream(filePath)), {
        status: 200,
        headers: { ...base, "Content-Length": String(total) },
      });
    });

    ipcMain.handle("laura:service-info", () => serviceInfo);
    ipcMain.handle("laura:pick-file", async (): Promise<string | null> => {
      const result = await dialog.showOpenDialog({
        properties: ["openFile"],
        filters: [
          {
            name: "Media",
            extensions: ["mp4", "mov", "mkv", "m4v", "avi", "webm", "mxf", "wav", "mp3", "aac", "flac"],
          },
          { name: "All Files", extensions: ["*"] },
        ],
      });
      return result.canceled || result.filePaths.length === 0 ? null : result.filePaths[0];
    });
    ipcMain.handle(
      "laura:save-file",
      async (_event, defaultName: string, content: string): Promise<string | null> => {
        const result = await dialog.showSaveDialog({ defaultPath: defaultName });
        if (result.canceled || !result.filePath) {
          return null;
        }
        await writeFile(result.filePath, content, "utf-8");
        return result.filePath;
      },
    );

    createWindow();
    app.on("activate", () => {
      if (BrowserWindow.getAllWindows().length === 0) {
        createWindow();
      }
    });
  })
  .catch((err) => log.error("startup error", err));

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") {
    app.quit();
  }
});

app.on("quit", () => {
  stopService?.();
});
