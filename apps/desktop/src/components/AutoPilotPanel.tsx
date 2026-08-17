import { type ReactElement, useState } from "react";

import type { LauraClient } from "../api";

export interface AutoPilotPanelProps {
  client: LauraClient;
  assetId: string;
  /** Called after the pilot finishes so the parent can refresh assets / rough-cut. */
  onChanged?: () => void;
  /** Delay between re-checks while an async step (analysis/render) runs. */
  pollMs?: number;
}

const TERMINAL = new Set(["done", "target_reached", "error", "max_steps"]);
const MAX_ITERS = 40;

/**
 * Drives the selected asset through the pipeline via `/auto-pilot`. Each call advances
 * synchronously as far as it can and enqueues the next async step, so this re-calls until the
 * pilot reaches a terminal state. "→ Rough-Cut" stops before render; "→ Export" drives to a
 * finished export.
 */
export function AutoPilotPanel({
  client,
  assetId,
  onChanged,
  pollMs = 2000,
}: AutoPilotPanelProps): ReactElement {
  const [status, setStatus] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const run = async (target: "roughcut" | "render"): Promise<void> => {
    setRunning(true);
    setStatus("running …");
    try {
      for (let i = 0; i < MAX_ITERS; i++) {
        const result = await client.autoPilot(assetId, target);
        setStatus(result.status);
        if (TERMINAL.has(result.status)) break;
        await new Promise((resolve) => setTimeout(resolve, pollMs));
      }
      onChanged?.();
    } catch {
      setStatus("error");
    } finally {
      setRunning(false);
    }
  };

  return (
    <details className="border-b border-bezel/60 pb-1.5">
      <summary className="cursor-pointer select-none text-[11px] font-medium text-content-muted">
        🤖 Auto-Pilot
      </summary>
      <div className="mt-1 flex flex-wrap items-center gap-1">
        <button
          type="button"
          onClick={() => void run("roughcut")}
          disabled={running}
          className="rounded border border-bezel bg-surface-1 px-2 py-1 text-[11px] text-content-strong hover:border-accent disabled:opacity-40"
        >
          → Rough-Cut
        </button>
        <button
          type="button"
          onClick={() => void run("render")}
          disabled={running}
          className="rounded bg-accent px-2 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40"
        >
          → Export
        </button>
        {status && <span className="text-[10px] text-content-muted">{status}</span>}
      </div>
    </details>
  );
}
