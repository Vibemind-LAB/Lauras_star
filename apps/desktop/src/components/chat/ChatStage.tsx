import { type ReactElement, useCallback, useEffect, useRef, useState } from "react";

import type {
  ChatMessage,
  ChatTurnResult,
  ConversationSummary,
  LauraClient,
  OpenProductionSession,
} from "../../api";
import { log } from "../../shared/log";
import { ChatComposer } from "./ChatComposer";
import { ChatPreview, type PreviewTarget } from "./ChatPreview";
import { ChatThread } from "./ChatThread";
import { ConversationList } from "./ConversationList";
import { ProductionSessionCard } from "./ActionCard";

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

  // approve_script and select_scenes both resume the production and carry the same
  // {session_id, job_id} refs — they must derive like the other production cards (live finding
  // 2026-08-05 for approve_script: it fell through to "none", so the finished film's
  // "▶ watch" left the preview empty; select_scenes' chat path — _handle_select_scenes in
  // services/local-api/src/laura/chat/executor.py — is the same shape for the same reason).
  if (
    tool === "start_short" ||
    tool === "follow_up" ||
    tool === "approve_script" ||
    tool === "select_scenes"
  ) {
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
  /** The top bar's currently selected project, if any — a fresh conversation is bound to it at
   *  creation time (see `onNew`) so it never starts unbound while a project is visibly selected
   *  (live incident 2026-08-07: an unbound chat couldn't recognize a loosely mentioned project
   *  name and misread it as a Google-Drive URL request). */
  projectId?: string | null;
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
export function ChatStage({ client, projectId }: ChatStageProps): ReactElement {
  const [conversations, setConversations] = useState<ConversationSummary[]>([]);
  const [openSessions, setOpenSessions] = useState<OpenProductionSession[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);
  const [resumedOrphan, setResumedOrphan] = useState<OpenProductionSession | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // True while EITHER onSend's or onDecide's turn is in flight — the composer must stay locked
  // for both, not just a text send (Finding 1: a pending approval decision is still a turn).
  const [turnInFlight, setTurnInFlight] = useState(false);
  // Set by onFocusAction's manual "▶ watch" pick; cleared when the active conversation
  // switches. While set, the default preview-derivation effect below must not clobber it with
  // its own recompute (Finding 2).
  const [manualPreview, setManualPreview] = useState(false);
  const [preview, setPreview] = useState<PreviewTarget>({ kind: "none" });
  const [error, setError] = useState<string | null>(null);

  // The latest activeId, readable synchronously from inside an already-in-flight onSend/onDecide
  // promise callback — a plain closure over `activeId` would see the value from when the turn
  // started, not whether the user has since switched conversations (Finding 3).
  const activeIdRef = useRef<string | null>(activeId);
  activeIdRef.current = activeId;

  const reloadConversations = useCallback(async (): Promise<void> => {
    try {
      setConversations(await client.listConversations());
    } catch (e) {
      setError(errorText(e));
    }
  }, [client]);

  const reloadOpenSessions = useCallback(async (): Promise<void> => {
    try {
      setOpenSessions(await client.listOpenProductionSessions());
    } catch (e) {
      setError(errorText(e));
    }
  }, [client]);

  useEffect(() => {
    void Promise.all([reloadConversations(), reloadOpenSessions()]);
    const intervalId = window.setInterval(() => {
      void reloadOpenSessions();
    }, 2500);
    return () => window.clearInterval(intervalId);
  }, [reloadConversations, reloadOpenSessions]);

  // Load the active conversation's thread whenever it changes. Cancelled-guarded: switching
  // conversations again before this settles must not let the stale response win. Also the one
  // place a conversation switch is detected, so it resets the two pieces of state that are only
  // meaningful for the conversation being left: a manual preview pick (Finding 2) and an
  // in-flight turn lock that otherwise belongs to no one once its conversation is gone (Finding 3).
  useEffect(() => {
    setManualPreview(false);
    setTurnInFlight(false);
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
  // changes. Cancelled-guarded the same way as the conversation load above. Skipped entirely
  // while `manualPreview` is set — a manual "▶ watch" pick (onFocusAction) must survive later
  // messages changes (e.g. a follow-up text turn) instead of being clobbered by this effect's own
  // recompute (Finding 2). manualPreview is cleared on conversation switch (see the load effect
  // above), so a fresh conversation still gets its default derivation.
  useEffect(() => {
    if (manualPreview) return;
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
  }, [client, messages, manualPreview]);

  const onNew = useCallback((): void => {
    setResumedOrphan(null);
    void (async () => {
      try {
        const { id } = await client.createConversation(projectId ?? undefined);
        await reloadConversations();
        setActiveId(id);
      } catch (e) {
        setError(errorText(e));
      }
    })();
  }, [client, projectId, reloadConversations]);

  const onSelectConversation = useCallback((id: string): void => {
    setResumedOrphan(null);
    setActiveId(id);
  }, []);

  const onResume = useCallback((session: OpenProductionSession): void => {
    setError(null);
    setManualPreview(false);
    if (session.conversation_id !== null) {
      setResumedOrphan(null);
      setActiveId(session.conversation_id);
      return;
    }
    setActiveId(null);
    setMessages([]);
    setResumedOrphan(session);
  }, []);

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
      // A stale error from a previous failed turn must not stain the default view of a fresh
      // one — clear it the moment a new turn starts, not just on eventual success.
      setError(null);
      setTurnInFlight(true);
      client
        .sendChatMessage(conversationId, text)
        .then((result) => {
          // Drop the result if the user has since switched conversations — a slow response for
          // A must not merge into B's thread (Finding 3). The list reload is conversation-
          // agnostic (it re-reads the whole sidebar) so it always runs on success.
          if (activeIdRef.current === conversationId) applyTurn(result);
          void reloadConversations();
        })
        .catch((e: unknown) => {
          if (activeIdRef.current === conversationId) setError(errorText(e));
        })
        .finally(() => {
          // Only reset the lock if it still belongs to this conversation's turn — the load
          // effect already reset it (to false) on switch, and a stale finally here must not
          // stomp on whatever conversation B is doing now.
          if (activeIdRef.current === conversationId) setTurnInFlight(false);
        });
    },
    [client, activeId, applyTurn, reloadConversations],
  );

  const onDecide = useCallback(
    (messageId: string, decision: "approve" | "reject"): void => {
      if (activeId === null) return;
      const conversationId = activeId;
      setError(null);
      setTurnInFlight(true);
      client
        .decideApproval(conversationId, messageId, decision)
        .then((result) => {
          if (activeIdRef.current === conversationId) applyTurn(result);
          // The backend touches updated_at on both approve and reject, which can move this
          // conversation to the top of the sidebar — reload the list like onSend does
          // (Finding 4).
          void reloadConversations();
        })
        .catch((e: unknown) => {
          if (activeIdRef.current !== conversationId) return;
          setError(errorText(e));
          // A decide failure (e.g. a 409 already-decided race) leaves the card's LOCAL state
          // stale and still clickable — the spec mandates reloading the persisted state instead
          // of trusting the optimistic pending card: "die UI lädt den Nachrichtenstand nach und
          // zeigt den echten Status". Reuse the same conversation-load path the switch effect
          // uses, guarded the same way (activeIdRef) so a slow reload for a conversation the
          // user has since left does not clobber whatever is showing now.
          void client
            .getConversation(conversationId)
            .then((conversation) => {
              if (activeIdRef.current === conversationId) setMessages(conversation.messages);
            })
            .catch((reloadError: unknown) => {
              if (activeIdRef.current === conversationId) setError(errorText(reloadError));
            });
        })
        .finally(() => {
          if (activeIdRef.current === conversationId) setTurnInFlight(false);
        });
    },
    [client, activeId, applyTurn, reloadConversations],
  );

  const onFocusAction = useCallback(
    (messageId: string): void => {
      const message = messages.find((m) => m.id === messageId);
      if (message === undefined) return;
      setManualPreview(true);
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
          onSelect={onSelectConversation}
          onNew={onNew}
          onDelete={onDelete}
          openSessions={openSessions}
          onResume={onResume}
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
        {resumedOrphan !== null ? (
          <div className="min-h-0 flex-1 overflow-y-auto p-2">
            <div className="mb-1 text-[11px] text-content-muted">
              <span className="font-medium text-content-strong">Original brief:</span>{" "}
              {resumedOrphan.brief_preview}
            </div>
            <ProductionSessionCard
              client={client}
              sessionId={resumedOrphan.session_id}
              jobId={resumedOrphan.latest_job_id}
              initialOutcome={resumedOrphan.state === "running" ? "running" : "done"}
              loadInitialStatus
            />
          </div>
        ) : (
          <>
            <ChatThread
              messages={messages}
              client={client}
              onDecide={onDecide}
              onFocusAction={onFocusAction}
            />
            <ChatComposer disabled={activeId === null || turnInFlight} onSend={onSend} />
          </>
        )}
      </section>

      <section aria-label="Preview" className="flex min-h-0 flex-col overflow-hidden bg-surface-0">
        <ChatPreview target={preview} client={client} />
      </section>
    </div>
  );
}
