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
};

export function ImportProgress({
  status,
  onRetry,
}: {
  status: ImportStatus;
  onRetry: () => void;
}): ReactElement | null {
  if (status.phase === "ready") return null;

  if (status.phase === "error") {
    return (
      <div className="mt-1 flex items-center gap-2 text-xs text-red-400">
        <span className="truncate" title={status.error ?? undefined}>
          {status.error ?? "Import fehlgeschlagen"}
        </span>
        <button
          type="button"
          onClick={onRetry}
          className="shrink-0 rounded bg-slate-700 px-2 py-0.5 text-slate-100 hover:bg-slate-600"
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
      <div className="h-1.5 w-full overflow-hidden rounded bg-slate-800">
        <div
          className="h-full bg-sky-500 transition-all"
          style={{ width: pct != null ? `${pct}%` : "33%" }}
        />
      </div>
      <div className="mt-0.5 text-[11px] text-slate-500">
        {PHASE_LABEL[status.phase]} {detail && `· ${detail}`}
      </div>
    </div>
  );
}
