import { type ReactElement, useCallback, useEffect, useState } from "react";

import { type Asset, type LauraClient, type ShortsCandidate } from "../api";
import { useJobStatus } from "../hooks/useJobStatus";
import { useShortsCandidates } from "../hooks/useShortsCandidates";
import { log } from "../shared/log";
import { framesToTimecode } from "../shared/timecode";
import { AutoOverviewPanel } from "./AutoOverviewPanel";
import { Player } from "./Player";

function rateNum(asset: Asset): number {
  return asset.rate_num ?? 30;
}

function rateDen(asset: Asset): number {
  return asset.rate_den ?? 1;
}

/** Top-3 score_breakdown keys sorted by absolute value descending. */
function topBreakdownKeys(breakdown: Record<string, number>): string[] {
  return Object.entries(breakdown)
    .sort((a, b) => Math.abs(b[1]) - Math.abs(a[1]))
    .slice(0, 3)
    .map(([k]) => k);
}

function ShortsCandidateRow({
  candidate,
  asset,
  onSeek,
}: {
  candidate: ShortsCandidate;
  asset: Asset;
  onSeek: (frame: number) => void;
}): ReactElement {
  const [expanded, setExpanded] = useState(false);

  const rn = rateNum(asset);
  const rd = rateDen(asset);
  const startTc = framesToTimecode(candidate.start_frame, rn, rd);
  const endTc = framesToTimecode(candidate.end_frame_exclusive, rn, rd);
  const durationFrames = candidate.end_frame_exclusive - candidate.start_frame;
  const durationTc = framesToTimecode(durationFrames, rn, rd);

  const topKeys = candidate.score_breakdown ? topBreakdownKeys(candidate.score_breakdown) : [];

  return (
    <li
      className="flex cursor-pointer flex-col gap-1 rounded px-3 py-2 hover:bg-surface-2/20"
      onClick={() => onSeek(candidate.start_frame)}
    >
      <div className="flex items-center gap-3">
        {/* Time range + duration */}
        <span className="tabular-nums text-xs text-content-strong">
          {startTc} – {endTc}
          <span className="ml-1 text-content-faint">({durationTc})</span>
        </span>

        {/* Score */}
        <span className="ml-auto shrink-0 text-xs font-medium text-content-strong">
          {candidate.score.toFixed(2)}
        </span>

        {/* QA badge */}
        {candidate.qa_passed ? (
          <span
            className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium text-status-ok"
            title="Alle QA-Checks bestanden"
          >
            QA ok
          </span>
        ) : (
          <span
            className="shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium text-status-err"
            title={candidate.qa_issues.join(" · ")}
          >
            QA fehler
          </span>
        )}

        {/* Explainability toggle */}
        {topKeys.length > 0 && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setExpanded((v) => !v);
            }}
            className="shrink-0 text-[10px] text-content-faint hover:text-content-muted"
            title="Score-Erklärung ein-/ausklappen"
          >
            {expanded ? "▲" : "▼"}
          </button>
        )}
      </div>

      {/* Rejected notice */}
      {candidate.rejected && (
        <span className="text-[10px] text-status-warn">
          Verworfen{candidate.reject_reason ? `: ${candidate.reject_reason}` : ""}
        </span>
      )}

      {/* Explainability */}
      {expanded && candidate.score_breakdown && (
        <ul className="ml-1 space-y-0.5">
          {topKeys.map((key) => (
            <li key={key} className="flex gap-2 text-[10px] text-content-faint">
              <span className="font-mono">{key}</span>
              <span>{(candidate.score_breakdown![key] ?? 0).toFixed(3)}</span>
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}

export function ShortsView({
  client,
  asset,
  projectId = null,
  seek,
  currentFrame,
  onSeek,
  onFrame,
}: {
  client: LauraClient;
  asset: Asset | null;
  /** Enables the project-wide Auto-Übersicht entry; without it the panel stays hidden. */
  projectId?: string | null;
  seek: { frame: number } | null;
  currentFrame: number;
  onSeek: (frame: number) => void;
  onFrame: (frame: number) => void;
}): ReactElement {
  const { candidates, loading, error, reload } = useShortsCandidates(
    client,
    asset?.id ?? null,
  );
  const [jobId, setJobId] = useState<string | null>(null);
  const [extractError, setExtractError] = useState<string | null>(null);

  const { jobStatus, isRunning } = useJobStatus(client, jobId);

  // When the job reaches a terminal state, clear it and (on success) reload candidates.
  // Running this in a useEffect (not inline during render) avoids double-firing under
  // React StrictMode and prevents calling setState + async reload during render.
  // On failure we capture the error into extractError so it persists after jobId is cleared
  // (useJobStatus resets jobStatus to null as soon as jobId becomes null).
  useEffect(() => {
    if (!jobId || !jobStatus) return;
    if (jobStatus.status === "succeeded") {
      setJobId(null);
      void reload();
    } else if (jobStatus.status === "failed") {
      setExtractError("Job fehlgeschlagen");
      setJobId(null);
    } else if (jobStatus.status === "cancelled") {
      setJobId(null);
    }
  }, [jobId, jobStatus, reload]);

  const onExtract = useCallback(async () => {
    if (!asset) return;
    setExtractError(null);
    try {
      const res = await client.extractShorts(asset.id);
      setJobId(res.job_id);
    } catch (e) {
      log.error("extractShorts failed", e);
      setExtractError(String(e));
    }
  }, [client, asset]);

  if (!asset) {
    return (
      <div className="flex min-h-0 flex-1 flex-col p-2">
        <AutoOverviewPanel client={client} projectId={projectId} />
        <div className="flex flex-1 items-center justify-center text-sm text-content-faint">
          Wähle ein Asset (in Import), um Shorts zu analysieren.
        </div>
      </div>
    );
  }

  const sortedCandidates = [...candidates].sort(
    (a, b) => a.order_index - b.order_index,
  );

  // Only show a non-terminal status label while the job is actively running.
  // Terminal states are handled via extractError (failure) or candidate reload (success).
  const jobLabel =
    jobStatus !== null && jobId !== null ? jobStatus.status : null;

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="px-2 pt-2">
        <AutoOverviewPanel client={client} projectId={projectId} />
      </div>
      {/* Player */}
      <div className="flex min-h-0 flex-1 bg-surface-2/20 p-2">
        <Player asset={asset} seekTo={seek} onFrame={onFrame} />
      </div>

      {/* Control bar */}
      <div className="flex items-center gap-3 border-t border-bezel px-3 py-2">
        <button
          type="button"
          onClick={() => void onExtract()}
          disabled={isRunning}
          className="rounded bg-accent px-3 py-1 text-xs text-white disabled:opacity-40"
        >
          {isRunning ? "Extrahiere…" : "Shorts extrahieren"}
        </button>
        {jobLabel && (
          <span className="text-xs text-content-muted">{jobLabel}</span>
        )}
        {extractError !== null && (
          <span className="text-xs text-status-err">{extractError}</span>
        )}
        <span className="text-[11px] text-content-faint">Frame {currentFrame}</span>
      </div>

      {/* Candidate list */}
      <div className="min-h-0 flex-1 overflow-auto border-t border-bezel">
        {loading && (
          <div className="px-3 py-2 text-xs text-content-faint">Lade…</div>
        )}
        {!loading && error && (
          <div className="px-3 py-2 text-xs text-status-err">{error}</div>
        )}
        {!loading && !error && sortedCandidates.length === 0 && (
          <div className="px-3 py-4 text-center text-xs text-content-faint">
            Noch keine Kandidaten — „Shorts extrahieren" klicken.
          </div>
        )}
        {!loading && !error && sortedCandidates.length > 0 && (
          <ul className="space-y-px py-1">
            {sortedCandidates.map((c) => (
              <ShortsCandidateRow
                key={c.id}
                candidate={c}
                asset={asset}
                onSeek={onSeek}
              />
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
