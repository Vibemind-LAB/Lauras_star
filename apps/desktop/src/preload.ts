import { contextBridge, ipcRenderer, webUtils } from "electron";

import type { ServiceInfo } from "./shared/ipc";

// The ONLY surface exposed to the renderer. No direct ipcRenderer, fs, or Node.
const bridge = {
  getServiceInfo: (): Promise<ServiceInfo | null> => ipcRenderer.invoke("laura:service-info"),
  pickMediaFile: (): Promise<string | null> => ipcRenderer.invoke("laura:pick-file"),
  saveTextFile: (defaultName: string, content: string): Promise<string | null> =>
    ipcRenderer.invoke("laura:save-file", defaultName, content),
  pathForFile: (file: File): string => webUtils.getPathForFile(file),
  pickMediaFiles: (): Promise<string[]> => ipcRenderer.invoke("laura:pick-files"),
  pickFolder: (): Promise<string | null> => ipcRenderer.invoke("laura:pick-folder"),
  listMediaInFolder: (folder: string): Promise<string[]> =>
    ipcRenderer.invoke("laura:list-media-in-folder", folder),
};

contextBridge.exposeInMainWorld("laura", bridge);

export type LauraBridge = typeof bridge;
