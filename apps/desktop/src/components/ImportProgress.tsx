import type { ReactElement } from "react";

import type { ImportStatus } from "../api";
import { formatBytes, formatEta, formatSpeed } from "../import/format";

const PHASE_LABEL: Record<ImportStatus["phase"], string> = {
  queued: "Wartet…",
  downloading: "Lädt…",
  verifying: "Prüft…",
  analyzing: "Analysiert…",
  ready: "Fertig",
  error: "Fehler",
  cancelled: "Abgebrochen",
};

/** Phases where the import is still running and can be cancelled. */
const ACTIVE_PHASES: ReadonlySet<ImportStatus["phase"]> = new Set([
  "queued",
  "downloading",
  "verifying",
  "analyzing",
]);

export function ImportProgress({
  status,
  onRetry,
  onCancel,
}: {
  status: ImportStatus;
  onRetry: () => void;
  onCancel?: () => void;
}): ReactElement | null {
  if (status.phase === "ready") return null;

  if (status.phase === "cancelled") {
    return (
      <div className="mt-1 flex items-center gap-2 text-xs text-content-muted">
        <span>{PHASE_LABEL.cancelled}</span>
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded bg-surface-2 px-2 py-0.5 text-content-strong hover:bg-surface-2"
        >
          Erneut versuchen
        </button>
      </div>
    );
  }

  if (status.phase === "error") {
    return (
      <div className="mt-1 flex items-center gap-2 text-xs text-status-err">
        <span className="truncate" title={status.error ?? undefined}>
          {status.error ?? "Import fehlgeschlagen"}
        </span>
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded bg-surface-2 px-2 py-0.5 text-content-strong hover:bg-surface-2"
        >
          Erneut versuchen
        </button>
      </div>
    );
  }

  const { downloaded_bytes: dl, total_bytes: total } = status;
  const pct = dl != null && total != null && total > 0 ? Math.floor((dl / total) * 100) : null;
  const detail = [
    pct != null ? `${pct}%` : null,
    dl != null && total != null ? `${formatBytes(dl)} / ${formatBytes(total)}` : null,
    formatSpeed(status.speed_bps),
    status.eta_seconds != null ? `ETA ${formatEta(status.eta_seconds)}` : null,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="mt-1">
      <div className="h-1.5 w-full overflow-hidden rounded bg-surface-2">
        <div
          className="h-full bg-sky-500 transition-all"
          style={{ width: pct != null ? `${pct}%` : "33%" }}
        />
      </div>
      <div className="mt-0.5 flex items-center justify-between text-[11px] text-content-faint">
        <span>
          {PHASE_LABEL[status.phase]} {detail && `· ${detail}`}
        </span>
        {ACTIVE_PHASES.has(status.phase) && onCancel != null && (
          <button
            type="button"
            onClick={onCancel}
            aria-label="Abbrechen"
            className="ml-2 shrink-0 rounded px-1.5 py-0.5 text-content-faint hover:bg-surface-2 hover:text-content-strong"
          >
            ✕
          </button>
        )}
      </div>
    </div>
  );
}
