import { type ReactElement, useEffect, useState } from "react";

import type { LauraClient } from "../api";
import { useJobStatus } from "../hooks/useJobStatus";

export interface GenerateVideoPanelProps {
  client: LauraClient;
  projectId: string;
  /** Called when a generate job succeeds so the parent can refresh the asset list. */
  onGenerated?: () => void;
}

// Seconds → frames for the request. A UI approximation: the backend renders at the project's
// own frame rate; the clip length in frames is what the endpoint needs.
const FPS = 30;

/**
 * Collapsible "generate B-roll" control in the media panel: a prompt + length enqueues a
 * generate.video job, tracks it, and refreshes the asset list when it succeeds. Uses the model-free
 * stub until a real ComfyUI/LTX backend is configured (see docs/generative-comfyui.md).
 */
export function GenerateVideoPanel({
  client,
  projectId,
  onGenerated,
}: GenerateVideoPanelProps): ReactElement {
  const [prompt, setPrompt] = useState("");
  const [seconds, setSeconds] = useState(3);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { jobStatus, isRunning } = useJobStatus(client, jobId);

  useEffect(() => {
    if (jobStatus?.status === "succeeded") {
      onGenerated?.();
      setJobId(null);
      setPrompt("");
    }
  }, [jobStatus, onGenerated]);

  const submit = (): void => {
    const text = prompt.trim();
    if (text === "" || isRunning) return;
    setError(null);
    const frames = Math.max(1, Math.round(seconds * FPS));
    void client
      .generateVideo(projectId, text, frames)
      .then((r) => setJobId(r.job_id))
      .catch(() => setError("Generierung fehlgeschlagen"));
  };

  return (
    <details className="border-b border-bezel/60 pb-1.5">
      <summary className="cursor-pointer select-none text-[11px] font-medium text-content-muted">
        ✨ Generate B-roll
      </summary>
      <div className="mt-1 flex flex-col gap-1">
        <textarea
          aria-label="Prompt"
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Describe the clip …"
          rows={2}
          className="rounded border border-bezel bg-surface-1 px-1.5 py-1 text-[11px] text-content-strong"
        />
        <label className="flex items-center gap-1 text-[10px] text-content-muted">
          Length
          <input
            aria-label="Length in seconds"
            type="number"
            min={1}
            max={30}
            value={seconds}
            onChange={(e) => setSeconds(Math.max(1, Number(e.target.value) || 1))}
            className="w-12 rounded border border-bezel bg-surface-1 px-1 py-0.5 text-[11px] text-content-strong"
          />
          s
        </label>
        <button
          type="button"
          onClick={submit}
          disabled={isRunning || prompt.trim() === ""}
          className="rounded bg-accent px-2 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40"
        >
          {isRunning ? "Generiere …" : "Generieren"}
        </button>
        {error && (
          <p className="text-[10px] text-status-err" role="alert">
            {error}
          </p>
        )}
      </div>
    </details>
  );
}
