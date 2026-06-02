/// <reference types="vite/client" />

// Globals injected by @electron-forge/plugin-vite into the main process.
declare const MAIN_WINDOW_VITE_DEV_SERVER_URL: string | undefined;
declare const MAIN_WINDOW_VITE_NAME: string;

// electron-squirrel-startup ships no types.
declare module "electron-squirrel-startup" {
  const started: boolean;
  export default started;
}
