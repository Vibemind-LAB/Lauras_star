import { type ReactElement, useState } from "react";

import type { OpenProductionSession } from "../../api";

export interface OpenSessionsPanelProps {
  sessions: OpenProductionSession[];
  onResume: (session: OpenProductionSession) => void;
  /** Delete a production and everything it produced. Absent = no delete affordance (the panel
   * stays read-only for callers that have nothing to delete with). */
  onDelete?: (session: OpenProductionSession) => void;
}

export function OpenSessionsPanel({
  sessions,
  onResume,
  onDelete,
}: OpenSessionsPanelProps): ReactElement | null {
  // Which row is asking "really?" — the same one-click-then-confirm shape MediaSidebar uses,
  // so a delete is never one stray click away from taking a whole production with it.
  const [confirming, setConfirming] = useState<string | null>(null);

  if (sessions.length === 0) return null;
  const sorted = [...sessions].sort((left, right) =>
    right.updated_utc.localeCompare(left.updated_utc),
  );

  return (
    <section aria-label="Open productions" className="rounded border border-bezel bg-surface-1 p-1">
      <div className="mb-1 flex items-baseline justify-between text-[10px] font-semibold uppercase tracking-wide text-content-faint">
        <span>Open productions</span>
        <span aria-hidden className="tabular-nums">{sorted.length}</span>
      </div>
      {/* Capped and scrollable: unbounded, this list grew to 17 rows of five lines each and
          pushed the conversations — and with them the chat — out of the sidebar entirely. */}
      <div className="max-h-56 space-y-1 overflow-y-auto">
        {sorted.map((session) => (
          <div
            key={session.session_id}
            className="rounded border border-bezel px-1.5 py-1 text-[11px]"
          >
            <button
              type="button"
              aria-label="Resume"
              onClick={() => onResume(session)}
              className="block w-full text-left hover:opacity-90"
            >
              <span className="block font-medium text-content-strong">
                {session.brief_preview || session.asset_display_name}
              </span>
              <span className="block text-content-muted">
                {session.asset_display_name} · {session.state}
              </span>
              <span className="block text-content-faint">
                Saved {session.draft_updated_utc ?? session.updated_utc}
              </span>
              {session.stale ? (
                <span className="mt-0.5 block text-status-warn">
                  The source file or the proposal changed — check before approving.
                </span>
              ) : null}
              <span className="mt-0.5 block font-medium text-accent">Resume</span>
            </button>
            {onDelete ? (
              confirming === session.session_id ? (
                <div className="mt-1 flex items-center gap-1">
                  <button
                    type="button"
                    onClick={() => {
                      setConfirming(null);
                      onDelete(session);
                    }}
                    className="rounded bg-status-err/20 px-1.5 py-0.5 text-status-err hover:bg-status-err/30"
                  >
                    Delete production
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirming(null)}
                    className="rounded px-1.5 py-0.5 text-content-muted hover:bg-surface-2"
                  >
                    Keep
                  </button>
                  <span className="text-content-faint">the video stays</span>
                </div>
              ) : (
                <button
                  type="button"
                  aria-label={`Delete production ${session.brief_preview || session.asset_display_name}`}
                  onClick={() => setConfirming(session.session_id)}
                  className="mt-0.5 text-content-faint hover:text-status-err"
                >
                  Delete
                </button>
              )
            ) : null}
          </div>
        ))}
      </div>
    </section>
  );
}
