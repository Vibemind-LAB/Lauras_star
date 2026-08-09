import { type ReactElement, useState } from "react";

import type { ContactSheetGateStatus, LauraClient } from "../../api";

type ContactSheetClient = Pick<LauraClient, "assetFrameUrl" | "confirmContactSheet">;

export interface ContactSheetApprovalCardProps {
  gate: ContactSheetGateStatus;
  sessionId: string;
  client: ContactSheetClient;
  onConfirmed: () => Promise<void> | void;
}

export function ContactSheetApprovalCard({
  gate,
  sessionId,
  client,
  onConfirmed,
}: ContactSheetApprovalCardProps): ReactElement {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (): Promise<void> => {
    if (busy || gate.current_sheet_hash === null) return;
    setBusy(true);
    setError(null);
    try {
      await client.confirmContactSheet(sessionId, gate.current_sheet_hash);
      await onConfirmed();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Kontaktbogen konnte nicht bestätigt werden");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mt-0.5 rounded border border-bezel bg-surface-1 px-1.5 py-1 text-[11px]">
      <div className="mb-1 text-content-strong">Kontaktbogen prüfen</div>
      <div className="space-y-1">
        {gate.tiles.map((tile) => (
          <div key={tile.order} className="rounded border border-bezel px-1.5 py-1">
            <div className="font-semibold text-content-strong">
              Szene {tile.scene_number} · {tile.label}
            </div>
            {tile.src_start_frame !== null && tile.src_end_frame_exclusive !== null ? (
              <div className="text-content-muted">
                In {tile.src_start_frame} · Out {tile.src_end_frame_exclusive}
              </div>
            ) : null}
            <div className="text-content-muted">{tile.narration_excerpt}</div>
            <div className="text-content-faint">{tile.rationale}</div>
          </div>
        ))}
      </div>
      {error !== null ? (
        <div className="mt-1 text-status-err" role="alert">
          {error}
        </div>
      ) : null}
      <button
        type="button"
        disabled={busy || gate.current_sheet_hash === null}
        onClick={() => void submit()}
        className="mt-1 rounded bg-accent px-2 py-1 font-medium text-accent-ink disabled:opacity-40"
      >
        Kontaktbogen freigeben
      </button>
    </div>
  );
}
