import { type ReactElement } from "react";

import type { ChatMessage } from "../../api";

/** The load-bearing facts of an `approval_request` message, narrowed defensively — `content` is
 * typed `Record<string, unknown>` (see api.ts's `ChatMessage`), and its real shape comes from
 * the backend's `_handle_propose_import` (services/local-api/src/laura/chat/executor.py):
 * `{ action_type, payload: { urls, project_id }, status, decided_at, result }`. */
interface ApprovalContent {
  urls: string[];
  status: string;
}

function narrowApprovalContent(content: Record<string, unknown>): ApprovalContent {
  const payload =
    typeof content.payload === "object" && content.payload !== null
      ? (content.payload as Record<string, unknown>)
      : {};
  const rawUrls = payload.urls;
  const urls = Array.isArray(rawUrls)
    ? rawUrls.filter((u): u is string => typeof u === "string")
    : [];
  const status = typeof content.status === "string" ? content.status : "pending";
  return { urls, status };
}

export interface ApprovalCardProps {
  message: ChatMessage;
  onDecide: (decision: "approve" | "reject") => void;
}

/**
 * One `approval_request` message rendered as a thread card: the proposed URLs plus
 * „Freigeben"/„Ablehnen" while the card is still `pending`. Any other status is the PERSISTED
 * decision: a read-only line, no buttons — re-deciding an already-decided card is exactly what
 * the backend 409s on. `approved` gets its own honest line (rather than reading as `rejected`):
 * it is a stray status left behind when `execute_import_approval` crashes between its two
 * writes (card flipped to "approved", then the import machinery itself fails before the card
 * reaches "executed") — the user DID approve, execution just never finished.
 */
export function ApprovalCard({ message, onDecide }: ApprovalCardProps): ReactElement {
  const { urls, status } = narrowApprovalContent(message.content);
  const pending = status === "pending";

  return (
    <div className="mb-1.5 rounded-md border border-bezel bg-surface-2 px-1.5 py-1 text-[11px]">
      <div className="mb-1 font-medium text-content-strong">Import bestätigen</div>
      <ul className="mb-1 flex flex-col gap-0.5">
        {urls.map((url) => (
          <li key={url} className="truncate text-content-muted" title={url}>
            {url}
          </li>
        ))}
      </ul>
      {pending ? (
        <div className="flex gap-1">
          <button
            type="button"
            onClick={() => onDecide("approve")}
            className="rounded bg-accent px-2 py-0.5 font-medium text-accent-ink hover:bg-accent-glow"
          >
            Freigeben
          </button>
          <button
            type="button"
            onClick={() => onDecide("reject")}
            className="rounded border border-bezel px-2 py-0.5 text-content-muted hover:text-content-strong"
          >
            Ablehnen
          </button>
        </div>
      ) : status === "approved" ? (
        <div className="text-status-warn">⏳ freigegeben — Ausführung unterbrochen</div>
      ) : (
        <div className={status === "executed" ? "text-status-ok" : "text-status-err"}>
          {status === "executed" ? "✓ freigegeben & ausgeführt" : "✗ abgelehnt"}
        </div>
      )}
    </div>
  );
}
