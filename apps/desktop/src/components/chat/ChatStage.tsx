import { type ReactElement, useCallback, useEffect, useState } from "react";

import type { ChatMessage, ChatTurnResult, ConversationSummary, LauraClient } from "../../api";
import { log } from "../../shared/log";
import { ChatComposer } from "./ChatComposer";
import { ChatPreview, type PreviewTarget } from "./ChatPreview";
import { ChatThread } from "./ChatThread";
import { ConversationList } from "./ConversationList";

/** The load-bearing facts of an `action` message needed to derive a preview target — same
 * narrowing as `ActionCard.tsx`'s `narrowActionContent`, kept local (not shared) since the two
 * components read different subsets of the same wire shape. */
function narrowActionContent(content: Record<string, unknown>): {
  tool: string;
  refs: Record<string, unknown>;
} {
  const tool = typeof content.tool === "string" ? content.tool : "";
  const refs =
    typeof content.refs === "object" && content.refs !== null
      ? (content.refs as Record<string, unknown>)
      : {};
  return { tool, refs };
}

/** The newest `action` message in a thread (messages arrive in `seq` order), or `null` when
 * the thread has none yet. */
function newestActionMessage(messages: ChatMessage[]): ChatMessage | null {
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    if (messages[i].kind === "action") return messages[i];
  }
  return null;
}

/**
 * The preview target one `action` message points at, per the brief's rule grounded in what the
 * backend actually exposes (services/local-api/src/laura/chat/executor.py):
 * - `start_overview` hands its export id back in `refs` directly (`_handle_start_overview`) —
 *   no fetch needed.
 * - `start_short`/`follow_up` only carry a `session_id`; the export id (or a contact sheet in
 *   the meantime) only exists on the LIVE v2 board, so this reads `getProductionStatus` once —
 *   the message's own `content.outcome` is written once at append time and never updated
 *   (server-side there is no follow-up write), so it is NOT a truthful "is it done yet" signal
 *   the way a fresh board read is. Export id wins over a contact sheet once both exist, mirroring
 *   `ActionCard.tsx`'s own `narrowProductionResult`.
 * - Anything else (e.g. `import_urls`) has nothing visual to focus.
 */
async function deriveTarget(client: LauraClient, message: ChatMessage): Promise<PreviewTarget> {
  const { tool, refs } = narrowActionContent(message.content);

  if (tool === "start_overview") {
    const exportId = typeof refs.export_id === "string" ? refs.export_id : null;
    return exportId !== null ? { kind: "export", exportId } : { kind: "none" };
  }

  if (tool === "start_short" || tool === "follow_up") {
    const sessionId = typeof refs.session_id === "string" ? refs.session_id : null;
    if (sessionId === null) return { kind: "none" };
    try {
      const status = await client.getProductionStatus(sessionId);
      const exportId = status.job?.export_id ?? null;
      if (exportId !== null) return { kind: "export", exportId };
      if (status.board_ready) {
        const version = status.artifacts.contact_sheet.version;
        if (version !== null) return { kind: "contact_sheet", sessionId, version };
      }
    } catch (e) {
      log.warn("ChatStage: production status fetch failed while deriving preview", e);
    }
    return { kind: "none" };
  }

  return { kind: "none" };
}

/** Merge one turn's returned messages into the thread by id: an id already in `existing` is an
 * in-place update (an approval card the turn just decided — see `execute_import_approval`,
 * which returns the SAME message id with new content); an unseen id is appended. Re-sorted by
 * `seq` so an update-in-place turn never disturbs display order. */
function mergeMessages(existing: ChatMessage[], incoming: ChatMessage[]): ChatMessage[] {
  const byId = new Map(existing.map((m) => [m.id, m]));
  for (const m of incoming) byId.set(m.id, m);
  return Array.from(byId.values()).sort((a, b) => a.seq - b.seq);
}

function errorText(e: unknown): string {
  return e instanceof Error ? e.message : String(e);
}

export interface ChatStageProps {
  client: LauraClient;
}

/**
 * The chat-first stage: conversation list, thread, and artifact preview in one three-column
 * view (spec 2026-08-03-chat-first, Task 12 — the final assembly). Owns every piece of state
 * the child components need: the conversation list (loaded on mount, refreshed after
 * create/delete/send/decide since each of those can move a conversation to the top or add a
 * new row), the active conversation's messages (merged in from turn results rather than
 * reloaded wholesale, so an in-flight production card's own poll is never interrupted), whether
 * a turn is in flight (disables the composer), and the preview pane's target.
 */
export function ChatStage({ client }: ChatStageProps): ReactElement {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [sending, setSending] = useState(false);
  const [preview, setPreview] = useState<PreviewTarget>({ kind: "none" });
  const [error, setError] = useState<string | null>(null);

  const reloadConversations = useCallback(async (): Promise<void> => {
    try {
      setConversations(await client.listConversations());
    } catch (e) {
      setError(errorText(e));
    }
  }, [client]);

  useEffect(() => {
    void reloadConversations();
  }, [reloadConversations]);

  // Load the active conversation's thread whenever it changes. Cancelled-guarded: switching
  // conversations again before this settles must not let the stale response win.
  useEffect(() => {
    if (activeId === null) {
      setMessages([]);
      return;
    }
    let cancelled = false;
    void client
      .getConversation(activeId)
      .then((conversation) => {
        if (!cancelled) setMessages(conversation.messages);
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(errorText(e));
      });
    return () => {
      cancelled = true;
    };
  }, [client, activeId]);

  // Default preview target: recompute from the newest action message whenever the thread
  // changes. Cancelled-guarded the same way as the conversation load above.
  useEffect(() => {
    const action = newestActionMessage(messages);
    if (action === null) {
      setPreview({ kind: "none" });
      return;
    }
    let cancelled = false;
    void deriveTarget(client, action).then((target) => {
      if (!cancelled) setPreview(target);
    });
    return () => {
      cancelled = true;
    };
  }, [client, messages]);

  const onNew = useCallback((): void => {
    void (async () => {
      try {
        const { id } = await client.createConversation();
        await reloadConversations();
        setActiveId(id);
      } catch (e) {
        setError(errorText(e));
      }
    })();
  }, [client, reloadConversations]);

  const onDelete = useCallback(
    (id: string): void => {
      void (async () => {
        try {
          await client.deleteConversation(id);
          if (activeId === id) setActiveId(null);
          await reloadConversations();
        } catch (e) {
          setError(errorText(e));
        }
      })();
    },
    [client, activeId, reloadConversations],
  );

  const applyTurn = useCallback((result: ChatTurnResult): void => {
    setMessages((prev) => mergeMessages(prev, result.messages));
  }, []);

  const onSend = useCallback(
    (text: string): void => {
      if (activeId === null) return;
      const conversationId = activeId;
      setSending(true);
      client
        .sendChatMessage(conversationId, text)
        .then((result) => {
          applyTurn(result);
          void reloadConversations();
        })
        .catch((e: unknown) => setError(errorText(e)))
        .finally(() => setSending(false));
    },
    [client, activeId, applyTurn, reloadConversations],
  );

  const onDecide = useCallback(
    (messageId: string, decision: "approve" | "reject"): void => {
      if (activeId === null) return;
      client
        .decideApproval(activeId, messageId, decision)
        .then((result) => applyTurn(result))
        .catch((e: unknown) => setError(errorText(e)));
    },
    [client, activeId, applyTurn],
  );

  const onFocusAction = useCallback(
    (messageId: string): void => {
      const message = messages.find((m) => m.id === messageId);
      if (message === undefined) return;
      void deriveTarget(client, message).then((target) => setPreview(target));
    },
    [client, messages],
  );

  return (
    <div className="grid min-h-0 flex-1 grid-cols-[220px_1fr_380px] gap-px overflow-hidden bg-bezel">
      <aside aria-label="Unterhaltungen" className="flex min-h-0 flex-col overflow-hidden bg-surface-0">
        <ConversationList
          items={conversations}
          activeId={activeId}
          onSelect={setActiveId}
          onNew={onNew}
          onDelete={onDelete}
        />
      </aside>

      <section aria-label="Chat" className="flex min-h-0 flex-col overflow-hidden bg-surface-0">
        {error !== null && (
          <div
            role="alert"
            className="border-b border-status-err/50 bg-status-err/10 px-2 py-1 text-[11px] text-status-err"
          >
            {error}
          </div>
        )}
        <ChatThread
          messages={messages}
          client={client}
          onDecide={onDecide}
          onFocusAction={onFocusAction}
        />
        <ChatComposer disabled={activeId === null || sending} onSend={onSend} />
      </section>

      <section aria-label="Vorschau" className="flex min-h-0 flex-col overflow-hidden bg-surface-0">
        <ChatPreview target={preview} client={client} />
      </section>
    </div>
  );
}
