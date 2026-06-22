import { type ReactElement } from "react";

import { type LauraClient, type SmoothnessLabel } from "../api";
import { useTransitionReview } from "../hooks/useTransitionReview";

function scoreColor(smoothness: number): string {
  if (smoothness >= 0.7) return "bg-status-ok/20 text-status-ok";
  if (smoothness >= 0.4) return "bg-status-warn/20 text-status-warn";
  return "bg-red-600/30 text-red-300";
}

const LABEL_DE: Record<SmoothnessLabel, string> = {
  smooth: "flüssig",
  jump_cut: "Jump-Cut",
  hard_jolt: "harter Sprung",
  motion_break: "Bewegungsbruch",
};

/**
 * "Übergänge prüfen" — runs the local VLM transition-smoothness review for a timeline and lists
 * each cut with a score, label, reason and a one-click fix (crossfade for jump cuts, resnap for
 * jolts). The crossfade is visible in the export/render, not the live preview.
 */
export function TransitionReviewPanel({
  client,
  timelineId,
}: {
  client: LauraClient;
  timelineId: string | null;
}): ReactElement {
  const { verdicts, loading, error, run, apply } = useTransitionReview(client, timelineId);
  const hasTransitionFix = verdicts.some((v) => v.suggested_fix.kind === "transition");

  return (
    <div className="border-t border-bezel bg-surface-1 px-3 py-2 text-xs">
      <div className="mb-2 flex items-center justify-between">
        <span className="font-medium text-content-muted">Übergänge prüfen</span>
        <button
          type="button"
          disabled={loading || timelineId == null}
          onClick={() => void run()}
          className="rounded bg-sky-600/20 px-2 py-0.5 text-sky-300 hover:bg-sky-600/30 disabled:opacity-50"
        >
          {loading ? "läuft…" : "Prüfen"}
        </button>
      </div>

      {error && <p className="mb-1 text-red-400">{error}</p>}

      {verdicts.length === 0 && !loading && (
        <p className="text-content-faint">
          Noch keine Bewertung. „Prüfen" startet das lokale Modell (einmaliger Download beim
          ersten Lauf).
        </p>
      )}

      <ul className="flex flex-col gap-1">
        {verdicts.map((v) => (
          <li
            key={`${v.boundary_seq_frame}-${v.src_out_a}`}
            className="flex items-center justify-between gap-2 rounded bg-surface-0/60 px-2 py-1"
          >
            <span className={`rounded px-1 tabular-nums ${scoreColor(v.smoothness)}`}>
              {Math.round(v.smoothness * 100)}
            </span>
            <span className="flex-1 truncate text-content-muted" title={v.reason}>
              {LABEL_DE[v.label] ?? v.label}
            </span>
            {v.suggested_fix.kind === "none" ? (
              <span className="px-2 py-0.5 text-content-faint">ok</span>
            ) : (
              <button
                type="button"
                onClick={() => void apply(v)}
                className="rounded bg-accent/20 px-2 py-0.5 text-accent hover:bg-accent/30"
              >
                {v.suggested_fix.kind === "transition" ? "Blende" : "Resnap"}
              </button>
            )}
          </li>
        ))}
      </ul>

      {hasTransitionFix && (
        <p className="mt-1 text-[10px] text-content-faint">
          Crossfade erscheint im Export/Render, nicht in der Live-Vorschau.
        </p>
      )}
    </div>
  );
}


