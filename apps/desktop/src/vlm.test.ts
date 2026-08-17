import { describe, expect, it, vi } from "vitest";

import { type VlmDeps, ensureVlmBackend } from "./vlm";

vi.mock("./shared/log", () => ({ log: { info: vi.fn(), warn: vi.fn() } }));

function deps(reachable: boolean, startDaemon = vi.fn()): VlmDeps & { startDaemon: typeof startDaemon } {
  return { probe: async () => reachable, startDaemon };
}

function envWith(extra: Record<string, string> = {}): NodeJS.ProcessEnv {
  return { LAURA_VLM_PROVIDER: "ollama", LAURA_VLM_MODEL: "qwen2.5vl:7b", ...extra };
}

describe("ensureVlmBackend", () => {
  it("starts ollama when vision is configured and the daemon is down", async () => {
    const d = deps(false);
    await ensureVlmBackend(envWith(), d);
    expect(d.startDaemon).toHaveBeenCalledWith("ollama");
  });

  it("does nothing when the daemon already answers", async () => {
    const d = deps(true);
    await ensureVlmBackend(envWith(), d);
    expect(d.startDaemon).not.toHaveBeenCalled();
  });

  it("does nothing without a configured vision model", async () => {
    const d = deps(false);
    await ensureVlmBackend({ LAURA_VLM_PROVIDER: "ollama" }, d);
    expect(d.startDaemon).not.toHaveBeenCalled();
  });

  it("does nothing for a non-ollama vision provider", async () => {
    const d = deps(false);
    await ensureVlmBackend(envWith({ LAURA_VLM_PROVIDER: "openrouter" }), d);
    expect(d.startDaemon).not.toHaveBeenCalled();
  });

  it("never starts a daemon for a remote host — that machine is not ours to run", async () => {
    const d = deps(false);
    await ensureVlmBackend(envWith({ LAURA_OLLAMA_HOST: "http://gpu-box.local:11434" }), d);
    expect(d.startDaemon).not.toHaveBeenCalled();
  });

  it("honours an explicit binary path", async () => {
    const d = deps(false);
    await ensureVlmBackend(envWith({ LAURA_OLLAMA_BIN: "C:/tools/ollama.exe" }), d);
    expect(d.startDaemon).toHaveBeenCalledWith("C:/tools/ollama.exe");
  });

  it("survives a missing binary instead of failing the app start", async () => {
    const d = deps(
      false,
      vi.fn(() => {
        throw new Error("ENOENT");
      }),
    );
    await expect(ensureVlmBackend(envWith(), d)).resolves.toBeUndefined();
  });
});
