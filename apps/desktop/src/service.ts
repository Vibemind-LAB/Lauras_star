import { type ChildProcess, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
import path from "node:path";

import { app } from "electron";

import type { ServiceInfo } from "./shared/ipc";
import { log } from "./shared/log";

const PORT = Number(process.env.LAURA_PORT ?? "8765");

interface ServiceCommand {
  cmd: string;
  args: string[];
  cwd: string;
  useShell: boolean;
}

function devServiceDir(): string {
  if (process.env.LAURA_SERVICE_DIR) {
    return process.env.LAURA_SERVICE_DIR;
  }
  // Dev: app path is apps/desktop -> repo/services/local-api.
  return path.resolve(app.getAppPath(), "..", "..", "services", "local-api");
}

function resolveServiceCommand(): ServiceCommand {
  if (app.isPackaged) {
    // Packaged: a standalone service binary bundled as an extraResource
    // (see docs/13-packaging.md). No `uv`, no shell.
    const dir = path.join(process.resourcesPath, "service");
    const bin = process.platform === "win32" ? "laura-api.exe" : "laura-api";
    return { cmd: path.join(dir, bin), args: [], cwd: dir, useShell: false };
  }
  // Dev: run via uv. shell helps PATH-resolve `uv` on Windows.
  return {
    cmd: "uv",
    args: ["run", "laura-api"],
    cwd: devServiceDir(),
    useShell: process.platform === "win32",
  };
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

  const command = resolveServiceCommand();
  // Packaged: point the backend at the bundled ffmpeg/ffprobe (extraResource
  // "ffmpeg"; see forge.config.ts). laura/ingest/ffmpeg.py reads these env vars
  // first, falling back to PATH. Dev is left untouched and uses PATH.
  const ffmpegEnv: Record<string, string> = app.isPackaged
    ? {
        LAURA_FFMPEG: path.join(process.resourcesPath, "ffmpeg", "ffmpeg.exe"),
        LAURA_FFPROBE: path.join(process.resourcesPath, "ffmpeg", "ffprobe.exe"),
      }
    : {};
  const child: ChildProcess = spawn(command.cmd, command.args, {
    cwd: command.cwd,
    env: {
      ...process.env,
      LAURA_PORT: String(PORT),
      LAURA_TOKEN: token,
      LAURA_WORKSPACE: workspace,
      ...ffmpegEnv,
    },
    stdio: "inherit",
    shell: command.useShell,
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
