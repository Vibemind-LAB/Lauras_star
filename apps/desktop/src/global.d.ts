import type { LauraBridge } from "./preload";

declare global {
  interface Window {
    laura: LauraBridge;
  }
}

export {};
