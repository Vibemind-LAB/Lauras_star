import { type ReactElement, useState } from "react";

import type { ConversationSummary, OpenProductionSession } from "../../api";
import { OpenSessionsPanel } from "./OpenSessionsPanel";

export interface ConversationListProps {
  items: ConversationSummary[];
  activeId: string | null;
  onSelect: (id: string) => void;
  onNew: () => void;
  onDelete: (id: string) => void;
  openSessions?: OpenProductionSession[];
  onResume?: (session: OpenProductionSession) => void;
}

const rowBase =
  "flex items-center gap-1.5 rounded px-1.5 py-1 text-left text-[11px] transition cursor-pointer select-none";
const rowActive = "bg-accent/20 ring-1 ring-accent/40";
const rowInactive = "hover:bg-surface-2";

/**
 * One row of the sidebar: the conversation title plus a delete affordance. Delete is a two-step
 * inline confirm (no `window.confirm` — MediaSidebar's asset row uses that, but a bare browser
 * dialog can't be styled or tested the same way threads elsewhere in this app are, and per the
 * brief a dedicated confirm control fits the sidebar's existing row idiom better): the first
 * click on „×" swaps the row's trailing control for "Really delete?" / „Abbrechen"; only the
 * former fires `onDelete`. Both delete-related clicks stop propagation so they never also select
 * the row underneath, mirroring MediaSidebar's `onDelete` button.
 */
function ConversationRow({
  item,
  isActive,
  onSelect,
  onDelete,
}: {
  item: ConversationSummary;
  isActive: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
}): ReactElement {
  const [confirming, setConfirming] = useState(false);

  return (
    <div
      role="button"
      tabIndex={0}
      onClick={() => onSelect(item.id)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") onSelect(item.id);
      }}
      aria-pressed={isActive}
      className={`${rowBase} ${isActive ? rowActive : rowInactive}`}
    >
      <span className="min-w-0 flex-1 truncate text-content-strong" title={item.title}>
        {item.title}
      </span>
      {confirming ? (
        <span className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              onDelete(item.id);
              setConfirming(false);
            }}
            className="rounded px-1 py-0.5 text-[10px] font-medium text-status-err hover:bg-red-600/40"
          >
            Really delete?
          </button>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation();
              setConfirming(false);
            }}
            className="rounded px-1 py-0.5 text-[10px] text-content-faint hover:text-content-strong"
          >
            Cancel
          </button>
        </span>
      ) : (
        <button
          type="button"
          title="Delete conversation"
          aria-label="Delete conversation"
          onClick={(e) => {
            e.stopPropagation();
            setConfirming(true);
          }}
          className="shrink-0 rounded px-1 text-sm text-content-faint hover:bg-red-600/40 hover:text-red-200"
        >
          ×
        </button>
      )}
    </div>
  );
}

/**
 * The chat sidebar's conversation list: a „Neuer Chat" button above the rows, newest-touched
 * first (the order `items` already arrives in — `listConversations()` is backend-ordered, see
 * api.ts). Empty state matches the brief's copy verbatim.
 */
export function ConversationList({
  items,
  activeId,
  onSelect,
  onNew,
  onDelete,
  openSessions = [],
  onResume = () => undefined,
}: ConversationListProps): ReactElement {
  return (
    <div className="flex min-h-0 flex-1 flex-col gap-1 p-1.5">
      <OpenSessionsPanel sessions={openSessions} onResume={onResume} />
      <button
        type="button"
        onClick={onNew}
        className="rounded border border-bezel px-2 py-1 text-[11px] font-medium text-content-strong hover:bg-surface-2"
      >
        New chat
      </button>
      <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
        {items.length === 0 ? (
          <p className="px-1 py-2 text-[11px] text-content-faint">No conversations yet</p>
        ) : (
          items.map((item) => (
            <ConversationRow
              key={item.id}
              item={item}
              isActive={item.id === activeId}
              onSelect={onSelect}
              onDelete={onDelete}
            />
          ))
        )}
      </div>
    </div>
  );
}
