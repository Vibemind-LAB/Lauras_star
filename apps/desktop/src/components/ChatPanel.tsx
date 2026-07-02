import { type ReactElement, useState } from "react";

import type { AgentEvent, LauraClient } from "../api";

export interface ChatPanelProps {
  client: LauraClient;
  /** The selected video the request runs against; input is disabled when null. */
  assetId: string | null;
  /** Called for every streamed event so the parent can refresh views on artifact/done. */
  onEvent?: (event: AgentEvent) => void;
}

interface ChatMessage {
  id: number;
  event: AgentEvent;
}

/** One streamed event rendered as a chat line. */
function EventLine({ event }: { event: AgentEvent }): ReactElement {
  switch (event.type) {
    case "stage":
      return (
        <div className="my-1 text-center text-[10px] text-content-faint">
          — Stufe {event.stage} · {event.team} —
        </div>
      );
    case "agent":
      return (
        <div className="mb-1 text-content-strong">
          <span className="font-medium">{event.agent}</span>
          {event.text ? `: ${event.text}` : ""}
        </div>
      );
    case "tool_call":
      return (
        <details className="mb-1 text-content-muted">
          <summary className="cursor-pointer select-none">
            🔧 {event.agent} → {event.tool}
          </summary>
          <pre className="whitespace-pre-wrap break-all text-[10px]">
            {JSON.stringify(event.args)}
          </pre>
        </details>
      );
    case "tool_result":
      return (
        <div className="mb-1 text-content-muted">
          ↳ {event.tool}: {event.summary}
        </div>
      );
    case "artifact":
      return <div className="mb-1 text-content-muted">＋ {event.kind}</div>;
    case "escalated":
      return (
        <div className="my-1 text-center text-[10px] text-content-muted">↑ eskaliert zu {event.to}</div>
      );
    case "done":
      return (
        <div className="mt-1 rounded bg-accent/15 px-1.5 py-1 text-content-strong">
          {event.ok ? "✓ Short fertig" : "✗ nicht geklappt"}
          {event.escalated ? " (eskaliert)" : ""}
        </div>
      );
    case "error":
      return (
        <div className="mb-1 text-status-err" role="alert">
          ⚠ {event.message}
        </div>
      );
  }
}

/**
 * Docked assistant chat: the user types a request, the agent team runs live via the streaming
 * endpoint, and each event renders as a chat line. `onEvent` lets the parent refresh the app views
 * (Timeline/Player/Scenes) as artifacts appear, so the app "fills in" alongside the chat.
 */
export function ChatPanel({ client, assetId, onEvent }: ChatPanelProps): ReactElement {
  const [input, setInput] = useState("");
  const [topic, setTopic] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [running, setRunning] = useState(false);

  const submit = (): void => {
    const text = input.trim();
    if (text === "" || assetId === null || running) return;
    setTopic(text);
    setMessages([]);
    setRunning(true);
    setInput("");
    let seq = 0;
    const append = (event: AgentEvent): void => {
      setMessages((prev) => [...prev, { id: seq++, event }]);
      onEvent?.(event);
      if (event.type === "done" || event.type === "error") setRunning(false);
    };
    void client.streamAutoShort(assetId, { topic: text }, append).catch((e: unknown) => {
      append({ type: "error", message: String(e) });
    });
  };

  return (
    <aside className="flex w-72 shrink-0 flex-col border-l border-bezel bg-surface-1">
      <header className="border-b border-bezel px-2 py-1.5 text-[11px] font-medium text-content-muted">
        🤖 Assistent
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1.5 text-[11px]">
        {topic !== null && (
          <div className="mb-1 rounded bg-accent/15 px-1.5 py-1 text-content-strong">Du: {topic}</div>
        )}
        {messages.map((m) => (
          <EventLine key={m.id} event={m.event} />
        ))}
        {assetId === null && (
          <p className="text-content-faint">Wähle ein Video, dann sag, was du willst.</p>
        )}
      </div>
      <div className="flex gap-1 border-t border-bezel p-1.5">
        <input
          aria-label="Anfrage"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
          placeholder="Mach mir einen 60s-Short über …"
          disabled={assetId === null || running}
          className="min-w-0 flex-1 rounded border border-bezel bg-surface-1 px-1.5 py-1 text-[11px] text-content-strong disabled:opacity-40"
        />
        <button
          type="button"
          onClick={submit}
          disabled={assetId === null || running || input.trim() === ""}
          className="rounded bg-accent px-2 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40"
        >
          {running ? "…" : "Los"}
        </button>
      </div>
    </aside>
  );
}
