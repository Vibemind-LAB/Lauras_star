import type { ReactElement } from "react";

import type { OpenProductionSession } from "../../api";

export interface OpenSessionsPanelProps {
  sessions: OpenProductionSession[];
  onResume: (session: OpenProductionSession) => void;
}

export function OpenSessionsPanel({
  sessions,
  onResume,
}: OpenSessionsPanelProps): ReactElement | null {
  if (sessions.length === 0) return null;
  const sorted = [...sessions].sort((left, right) =>
    right.updated_utc.localeCompare(left.updated_utc),
  );

  return (
    <section aria-label="Offene Produktionen" className="rounded border border-bezel bg-surface-1 p-1">
      <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-content-faint">
        Offene Produktionen
      </div>
      <div className="space-y-1">
        {sorted.map((session) => (
          <button
            key={session.session_id}
            type="button"
            aria-label="Fortsetzen"
            onClick={() => onResume(session)}
            className="block w-full rounded border border-bezel px-1.5 py-1 text-left text-[11px] hover:bg-surface-2"
          >
            <span className="block font-medium text-content-strong">
              {session.brief_preview || session.asset_display_name}
            </span>
            <span className="block text-content-muted">
              {session.asset_display_name} · {session.state}
            </span>
            <span className="block text-content-faint">
              Gespeichert {session.draft_updated_utc ?? session.updated_utc}
            </span>
            {session.stale ? (
              <span className="mt-0.5 block text-status-warn">
                Quelldatei oder Vorschlag hat sich geändert — vor Freigabe prüfen.
              </span>
            ) : null}
            <span className="mt-0.5 block font-medium text-accent">Fortsetzen</span>
          </button>
        ))}
      </div>
    </section>
  );
}
