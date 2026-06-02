// Types shared between the main process, preload, and renderer.

export interface ServiceInfo {
  /** Loopback base URL of the local API, e.g. http://127.0.0.1:8765 */
  baseUrl: string;
  /** Per-session token the renderer must send as X-Laura-Token. */
  token: string;
}
