import { spawn } from "node:child_process";

import { log } from "./shared/log";

/**
 * Bring the local VLM daemon (ollama) up alongside the app.
 *
 * Scene reviews degrade silently when ollama is not listening: `review_scene` still
 * returns, but with `degraded: true` — a neutral hook score and one default window. Live
 * 2026-08-17: a whole production run reviewed two scenes blind, the vision_reviewer
 * re-reviewed them hoping for real analysis, and the short was cut from guesses. Nothing
 * in the pipeline is allowed to FAIL over this (the degraded path is deliberate), so
 * nothing was ever going to tell the user either. Starting the daemon with the app is the
 * cheap fix: the app already owns the backend's lifecycle, so it can own this too.
 */

const DEFAULT_HOST = "http://127.0.0.1:11434";

/** Whether *host* points at this machine — we never try to start someone else's daemon. */
function isLocalHost(host: string): boolean {
  try {
    const { hostname } = new URL(host);
    return hostname === "127.0.0.1" || hostname === "localhost" || hostname === "::1";
  } catch {
    return false;
  }
}

/** True when the backend would actually use ollama for vision (mirrors describe.py's gate). */
function ollamaIsConfigured(env: NodeJS.ProcessEnv): boolean {
  const provider = (env.LAURA_VLM_PROVIDER ?? "ollama").trim().toLowerCase();
  const model = (env.LAURA_VLM_MODEL ?? env.LAURA_VLM ?? "").trim();
  return provider === "ollama" && model.length > 0;
}

async function isReachable(host: string): Promise<boolean> {
  try {
    const res = await fetch(`${host}/api/tags`, {
      signal: AbortSignal.timeout(1500),
    });
    return res.ok;
  } catch {
    return false;
  }
}

/** Seams, so tests drive the decision without mocking modules or touching the network. */
export interface VlmDeps {
  probe: (host: string) => Promise<boolean>;
  startDaemon: (bin: string) => void;
}

function defaultDeps(): VlmDeps {
  return {
    probe: isReachable,
    startDaemon: (bin) => {
      const child = spawn(bin, ["serve"], {
        detached: true,
        stdio: "ignore",
        shell: process.platform === "win32",
      });
      child.unref();
    },
  };
}

/**
 * Start `ollama serve` when vision is configured for a local ollama that is not answering.
 *
 * Fire-and-forget by design: the daemon takes a few seconds to listen, but the first scene
 * review is minutes away, and a missing binary must never keep the app from starting — it
 * degrades exactly as before, just with a log line saying why.
 */
export async function ensureVlmBackend(
  env: NodeJS.ProcessEnv = process.env,
  deps: VlmDeps = defaultDeps(),
): Promise<void> {
  if (!ollamaIsConfigured(env)) {
    return;
  }
  const host = (env.LAURA_OLLAMA_HOST ?? DEFAULT_HOST).replace(/\/+$/, "");
  if (!isLocalHost(host)) {
    return;
  }
  if (await deps.probe(host)) {
    return;
  }
  try {
    deps.startDaemon(env.LAURA_OLLAMA_BIN ?? "ollama");
    log.info("started ollama serve for local vision analysis");
  } catch (err) {
    log.warn("ollama could not be started; scene reviews will run degraded", err);
  }
}
