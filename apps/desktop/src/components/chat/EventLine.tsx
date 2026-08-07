import { type ReactElement, useState } from "react";

import type { AgentEvent } from "../../api";

/** Roster identity: stable icon + short label per team member (fallback: raw name). */
const AGENT_META: Record<string, { icon: string; label: string }> = {
  scout: { icon: "🔭", label: "Scout" },
  describer: { icon: "👁️", label: "Describer" },
  transcript_analyst: { icon: "📝", label: "Analyst" },
  director: { icon: "🎬", label: "Director" },
  transcript_master: { icon: "🎙️", label: "Transcript Master" },
  editor: { icon: "✂️", label: "Editor" },
  qa: { icon: "🧪", label: "QA" },
};

function agentMeta(agent: string): { icon: string; label: string } {
  return AGENT_META[agent] ?? { icon: "🤖", label: agent };
}

const ARTIFACT_LABELS: Record<string, string> = {
  roughcut: "🧱 Rough Cut gebaut",
  render: "🎞️ Render gestartet",
  export: "🎞️ Export erstellt",
  timeline: "🧱 Timeline erstellt",
  voiceover: "🎙️ Voiceover erzeugt",
};

/** Keys worth surfacing from a raw tool-result summary (a truncated Python-dict repr). */
const HIGHLIGHT_KEYS = [
  "export_id",
  "job_final_status",
  "status",
  "count",
  "total_seconds",
  "segments_checked",
  "aligned",
  "voiceover_path",
  "reason",
  "error",
] as const;

/**
 * Pull the load-bearing facts out of a tool-result summary so the chat line is scannable
 * ("export_id=… · status=ready") instead of a wall of dict text. Best-effort and pure —
 * unknown shapes just yield "".
 */
export function pickHighlights(summary: string): string {
  const picks: string[] = [];
  for (const key of HIGHLIGHT_KEYS) {
    const m = summary.match(new RegExp(`'${key}':\\s*(?:'([^']*)'|([^,}]+))`));
    const value = (m?.[1] ?? m?.[2])?.trim();
    if (value !== undefined && value !== "" && value !== "None") {
      picks.push(`${key}=${value}`);
    }
  }
  return picks.join(" · ");
}

/** Agent prose longer than this is clamped behind a "mehr anzeigen" toggle. */
const CLAMP_CHARS = 280;

function AgentBubble({ agent, text }: { agent: string; text: string }): ReactElement {
  const [expanded, setExpanded] = useState(false);
  const meta = agentMeta(agent);
  // The task echo the orchestrator sends to the team — long boilerplate, collapsed by default.
  if (agent === "user") {
    return (
      <details className="mb-1.5 rounded border border-bezel px-1.5 py-1 text-content-faint">
        <summary className="cursor-pointer select-none">📋 Auftrag ans Team</summary>
        <p className="mt-1 whitespace-pre-wrap break-words text-[10px]">{text}</p>
      </details>
    );
  }
  // A deliberate SKIP (e.g. the Transcript Master without a re-voice request) is one quiet line.
  if (text.trim().replace(/\.+$/, "").toUpperCase() === "SKIP") {
    return (
      <div className="mb-1.5 text-content-faint">
        {meta.icon} {meta.label} überspringt (kein Auftrag für ihn)
      </div>
    );
  }
  const clamped = !expanded && text.length > CLAMP_CHARS;
  const shown = clamped ? `${text.slice(0, CLAMP_CHARS).trimEnd()}…` : text;
  return (
    <div className="mb-1.5 rounded-md border border-bezel bg-surface-2 px-1.5 py-1">
      <div className="mb-0.5 font-medium text-content-strong">
        {meta.icon} {meta.label}
      </div>
      <p className="whitespace-pre-wrap break-words text-content-muted">{shown}</p>
      {text.length > CLAMP_CHARS && (
        <button
          type="button"
          onClick={() => setExpanded((v) => !v)}
          className="mt-0.5 text-[10px] text-accent hover:underline"
        >
          {expanded ? "weniger anzeigen" : "mehr anzeigen"}
        </button>
      )}
    </div>
  );
}

function ToolResultLine({
  tool,
  ok,
  summary,
}: {
  tool: string;
  ok: boolean;
  summary: string;
}): ReactElement {
  const facts = pickHighlights(summary);
  return (
    <details className="mb-1 pl-2 text-content-muted">
      <summary className="cursor-pointer select-none">
        <span className={ok ? "text-status-ok" : "text-status-err"}>{ok ? "✓" : "✗"}</span>{" "}
        <span className="text-content-faint">{tool}</span>
        {facts !== "" && <span> · {facts}</span>}
      </summary>
      <pre className="mt-0.5 whitespace-pre-wrap break-all rounded bg-surface-2 p-1 text-[10px]">
        {summary}
      </pre>
    </details>
  );
}

function DoneCard({
  event,
}: {
  event: Extract<AgentEvent, { type: "done" }>;
}): ReactElement {
  const tone = !event.ok
    ? { cls: "border-status-err text-status-err", text: "✗ Nicht geklappt" }
    : event.weak
      ? { cls: "border-status-warn text-status-warn", text: "⚠ Fertig — QA meldet Schwächen" }
      : { cls: "border-status-ok text-status-ok", text: "✓ Short fertig" };
  return (
    <div className={`mt-1 rounded-md border bg-surface-2 px-1.5 py-1 ${tone.cls}`}>
      <div className="font-medium">
        {tone.text}
        {event.escalated ? " · eskaliert" : ""}
      </div>
      <div className="text-[10px] text-content-faint">
        Stufe {event.stage}
        {event.team !== undefined && event.team !== "" ? ` · ${event.team}` : ""}
      </div>
      {(event.summary ?? "").trim() !== "" && (
        <details className="mt-0.5 text-[10px] text-content-muted">
          <summary className="cursor-pointer select-none">Verlauf</summary>
          <p className="whitespace-pre-wrap break-words">{event.summary}</p>
        </details>
      )}
    </div>
  );
}

/** One streamed event rendered as a chat line. */
export function EventLine({ event }: { event: AgentEvent }): ReactElement | null {
  switch (event.type) {
    case "stage":
      return (
        <div className="my-1.5 text-center text-[10px] uppercase tracking-wide text-content-faint">
          — Stufe {event.stage} · {event.team} —
        </div>
      );
    case "agent":
      if (event.text === undefined || event.text.trim() === "") return null;
      return <AgentBubble agent={event.agent} text={event.text} />;
    case "tool_call": {
      const meta = agentMeta(event.agent);
      const args = JSON.stringify(event.args);
      const hasArgs = args !== "{}" && args !== "null";
      const line = (
        <>
          <span className="text-content-faint">{meta.icon}</span> {meta.label}{" "}
          <span className="text-content-faint">ruft</span>{" "}
          <span className="font-medium">{event.tool}</span>
        </>
      );
      if (!hasArgs) return <div className="mb-1 pl-2 text-content-muted">{line}</div>;
      return (
        <details className="mb-1 pl-2 text-content-muted">
          <summary className="cursor-pointer select-none">{line}</summary>
          <pre className="mt-0.5 whitespace-pre-wrap break-all rounded bg-surface-2 p-1 text-[10px]">
            {JSON.stringify(event.args, null, 1)}
          </pre>
        </details>
      );
    }
    case "tool_result":
      return <ToolResultLine tool={event.tool} ok={event.ok} summary={event.summary} />;
    case "artifact":
      return (
        <div className="mb-1 inline-block rounded-full border border-accent/40 bg-accent/15 px-2 py-0.5 text-[10px] text-content-strong">
          {ARTIFACT_LABELS[event.kind] ?? `＋ ${event.kind}`}
        </div>
      );
    case "escalated":
      return (
        <div className="my-1 text-center text-[10px] text-status-warn">
          ↑ eskaliert zu {event.to}
        </div>
      );
    case "done":
      return <DoneCard event={event} />;
    case "error":
      return (
        <div className="mb-1 text-status-err" role="alert">
          ⚠ {event.message}
        </div>
      );
  }
}
