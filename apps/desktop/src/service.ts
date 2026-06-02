import { type ChildProcess, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";

import { app } from "electron";

import type { ServiceInfo } from "./shared/ipc";
import { log } from "./shared/log";

const PORT = Number(process.env.LAURA_PORT ?? "8765");

function serviceDir(): string {
  if (process.env.LAURA_SERVICE_DIR) {
    return process.env.LAURA_SERVICE_DIR;
  }
  // Dev: app path is apps/desktop -> repo/services/local-api.
  // Packaged distribution bundles a built service instead (Portion 10).
  return path.resolve(app.getAppPath(), "..", "..", "services", "local-api");
}

async function waitForHealth(baseUrl: string, token: string, timeoutMs = 30000): Promise<void> {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    try {
      const res = await fetch(`${baseUrl}/healthz`, { headers: { "X-Laura-Token": token } });
      if (res.ok) {
        return;
      }
    } catch {
      // not up yet — keep polling
    }
    await new Promise((resolve) => setTimeout(resolve, 300));
  }
  throw new Error("local service did not become healthy in time");
}

/**
 * Spawn the local Python API as a child process (dev: `uv run laura-api`), wait
 * for it to be healthy, and return its connection info plus a stop handle.
 */
export async function startService(): Promise<{ info: ServiceInfo; stop: () => void }> {
  const token = randomUUID();
  const baseUrl = `http://127.0.0.1:${PORT}`;
  const workspace = path.join(app.getPath("userData"), "workspace");

  const child: ChildProcess = spawn("uv", ["run", "laura-api"], {
    cwd: serviceDir(),
    env: {
      ...process.env,
      LAURA_PORT: String(PORT),
      LAURA_TOKEN: token,
      LAURA_WORKSPACE: workspace,
    },
    stdio: "inherit",
    shell: process.platform === "win32", // help PATH-resolve `uv` on Windows
  });
  child.on("exit", (code) => log.warn("local service exited", code));

  await waitForHealth(baseUrl, token);
  log.info("local service healthy", baseUrl);

  const stop = (): void => {
    try {
      child.kill();
    } catch {
      // already gone
    }
  };
  return { info: { baseUrl, token }, stop };
}
