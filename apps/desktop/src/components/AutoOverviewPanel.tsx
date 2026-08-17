import { type ReactElement, useState } from "react";

import type { AutoOverviewResult, LauraClient } from "../api";
import { useJobStatus } from "../hooks/useJobStatus";
import { log } from "../shared/log";

/** Extracts the human-readable reason out of a `LauraClient` request error
 * (`Error("<status>: <body>")`), decoding FastAPI's `{"detail": {"reason": ...}}` shape the
 * auto-overview 422s carry. Falls back to the raw message. */
function overviewErrorText(e: unknown): string {
  const message = e instanceof Error ? e.message : String(e);
  const match = /^\d{3}:\s*([\s\S]*)$/.exec(message);
  if (match === null) return message;
  try {
    const parsed = JSON.parse(match[1]) as unknown;
    if (typeof parsed === "object" && parsed !== null && "detail" in parsed) {
      const detail = (parsed as Record<string, unknown>).detail;
      if (typeof detail === "string") return detail;
      if (typeof detail === "object" && detail !== null && "reason" in detail) {
        const reason = (detail as Record<string, unknown>).reason;
        if (typeof reason === "string") return reason;
      }
    }
  } catch {
    // Not JSON — the raw message carries the substance.
  }
  return message;
}

/** Renders the enqueued render's live state — its own component so the `useJobStatus` hook
 * only ever runs with a real job id (hooks cannot be called conditionally). */
function RenderJobLine({ client, jobId }: { client: LauraClient; jobId: string }): ReactElement {
  const { jobStatus } = useJobStatus(client, jobId);
  const status = jobStatus?.status ?? "queued";
  const done = status === "succeeded";
  return (
    <div className="text-[11px] text-content-muted" role="status">
      {done
        ? "✓ Render done — the film is in the Export tab."
        : status === "failed"
          ? "✗ Render failed — details in the Jobs panel."
          : `⚙ Render running (${status}) … the film shows up in the Export tab.`}
    </div>
  );
}

/**
 * UI entry for POST /projects/{pid}/auto-overview — the cross-video montage that shipped
 * 2026-07-31 and was reachable only via curl. Where the Shorts flow is "one video -> one
 * short", this is "whole project -> one overview": topic in, the scout picks windows across
 * SEVERAL videos, builds its own sequence (deliberately never the project's) and renders it.
 * The panel shows what was picked and why — the same honesty surface the session chips give
 * the board productions.
 */
export function AutoOverviewPanel({
  client,
  projectId,
}: {
  client: LauraClient;
  projectId: string | null;
}): ReactElement | null {
  const [topic, setTopic] = useState("");
  const [targetSeconds, setTargetSeconds] = useState(180);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AutoOverviewResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  if (projectId === null) return null;

  const start = (): void => {
    const trimmed = topic.trim();
    if (trimmed === "" || running) return;
    setRunning(true);
    setError(null);
    setResult(null);
    client
      .autoOverview(projectId, { topic: trimmed, target_seconds: targetSeconds })
      .then((r) => setResult(r))
      .catch((e: unknown) => {
        log.warn("auto-overview failed", e);
        setError(overviewErrorText(e));
      })
      .finally(() => setRunning(false));
  };

  return (
    <section
      aria-label="Auto overview"
      className="mb-3 rounded-lg border border-bezel bg-surface-1 p-3 text-xs"
    >
      <div className="mb-1 font-semibold text-content-strong">
        🎬 Auto overview — one film across several videos
      </div>
      <p className="mb-2 text-content-faint">
        Give it a topic; Laura searches the whole project for fitting moments and cuts them into a
        montage (its own sequence + render, original audio).
      </p>
      <div className="flex flex-wrap items-center gap-2">
        <input
          aria-label="Overview topic"
          value={topic}
          onChange={(e) => setTopic(e.target.value)}
          placeholder="z. B. Tour durch meine Spaces"
          className="min-w-0 flex-1 rounded border border-bezel bg-surface-0 px-2 py-1 text-content-strong"
          disabled={running}
        />
        <label className="flex items-center gap-1 text-content-muted">
          Ziel (s)
          <input
            aria-label="Target length in seconds"
            type="number"
            min={10}
            max={1800}
            value={targetSeconds}
            onChange={(e) => setTargetSeconds(Number(e.target.value) || 180)}
            className="w-16 rounded border border-bezel bg-surface-0 px-1.5 py-1 text-content-strong"
            disabled={running}
          />
        </label>
        <button
          type="button"
          onClick={start}
          disabled={running || topic.trim() === ""}
          className="rounded bg-accent px-3 py-1 font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40"
        >
          Create overview
        </button>
      </div>
      {running && (
        <div className="mt-2 animate-pulse text-content-faint">
          ⚙ Scout is picking scenes across all videos …
        </div>
      )}
      {error !== null && (
        <div className="mt-2 text-status-err" role="alert">
          ⚠ {error}
        </div>
      )}
      {result !== null && (
        <div className="mt-2 flex flex-col gap-1">
          <div className="text-content-strong">{result.rationale}</div>
          <ul className="flex flex-col gap-0.5">
            {result.clips.map((c, i) => (
              <li key={`${c.asset_id}-${c.scene_number}-${c.start_frame}`} className="text-content-muted">
                {i + 1}. <span className="text-content-strong">{c.display_name}</span> · Szene{" "}
                {c.scene_number} — <span className="italic">{c.snippet}</span>
              </li>
            ))}
          </ul>
          {result.warnings.map((w) => (
            <div key={w} className="text-status-warn">
              ⚠ {w}
            </div>
          ))}
          <RenderJobLine client={client} jobId={result.job_id} />
        </div>
      )}
    </section>
  );
}
