import { contextBridge, ipcRenderer } from "electron";

import type { ServiceInfo } from "./shared/ipc";

// The ONLY surface exposed to the renderer. No direct ipcRenderer, fs, or Node.
const bridge = {
  getServiceInfo: (): Promise<ServiceInfo | null> => ipcRenderer.invoke("laura:service-info"),
  pickMediaFile: (): Promise<string | null> => ipcRenderer.invoke("laura:pick-file"),
  saveTextFile: (defaultName: string, content: string): Promise<string | null> =>
    ipcRenderer.invoke("laura:save-file", defaultName, content),
};

contextBridge.exposeInMainWorld("laura", bridge);

export type LauraBridge = typeof bridge;
