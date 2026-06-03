import { readdir, writeFile } from "node:fs/promises";
import path from "node:path";

import { BrowserWindow, app, dialog, ipcMain, shell } from "electron";
import started from "electron-squirrel-startup";

import { startService } from "./service";
import type { ServiceInfo } from "./shared/ipc";
import { log } from "./shared/log";

const MEDIA_EXTS = new Set([
  ".mp4", ".mov", ".mkv", ".m4v", ".avi", ".webm", ".mxf", ".mpg", ".mpeg",
  ".wav", ".aif", ".aiff", ".flac", ".mp3", ".m4a", ".aac",
]);

// Quit early if launched by the Squirrel installer (Windows).
if (started) {
  app.quit();
}

let serviceInfo: ServiceInfo | null = null;
let stopService: (() => void) | null = null;

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
