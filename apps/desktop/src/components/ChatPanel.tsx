import { type ReactElement, useEffect, useRef, useState } from "react";

import type {
  AgentEvent,
  LauraClient,
  ProductionArtifactState,
  ProductionStatus,
} from "../api";
import {
  type ProductionSessionController,
  useProductionSession,
} from "../hooks/useProductionSession";

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
        Stufe {event.stage} · {event.team}
      </div>
      {event.summary.trim() !== "" && (
        <details className="mt-0.5 text-[10px] text-content-muted">
          <summary className="cursor-pointer select-none">Verlauf</summary>
          <p className="whitespace-pre-wrap break-words">{event.summary}</p>
        </details>
      )}
    </div>
  );
}

/** One streamed event rendered as a chat line. */
function EventLine({ event }: { event: AgentEvent }): ReactElement | null {
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

/* ---------------------------------------------------------------------------------------------
 * Session (v2) mode: a persistent, resumable production session (multi-turn, board-backed) as an
 * alternative to the v1 one-shot stream above. State comes entirely from `useProductionSession`
 * (Task 2) — this file only renders its phases (idle/running/done/error).
 * ------------------------------------------------------------------------------------------- */

/** Board artifact chain in display order, with a friendly short label per slot. `render_report`
 * reads as "Export" rather than its raw key — that's what it represents to the user, and the
 * board status carries no separate export id to show instead (see ProductionStatus). */
const SESSION_ARTIFACT_ORDER = [
  "storyline",
  "script",
  "voice",
  "cutlist",
  "contact_sheet",
  "render_report",
  "qa_report",
] as const;

const SESSION_ARTIFACT_LABELS: Record<(typeof SESSION_ARTIFACT_ORDER)[number], string> = {
  storyline: "storyline",
  script: "script",
  voice: "voice",
  cutlist: "cutlist",
  contact_sheet: "Bogen",
  render_report: "Export",
  qa_report: "QA",
};

/** *name* under {@link SESSION_ARTIFACT_LABELS}'s friendly label, or itself when unrecognized —
 * defensive because artifact names travelling through `restored` are wire data, not a closed
 * union at this point. */
function sessionArtifactLabel(name: string): string {
  return name in SESSION_ARTIFACT_LABELS
    ? SESSION_ARTIFACT_LABELS[name as keyof typeof SESSION_ARTIFACT_LABELS]
    : name;
}

/** Shared chip-pill styling — the plain read-only chip and the button variant that opens a
 * revert dropdown both use it, so the button never looks different from its neighbors at rest. */
const SESSION_CHIP_CLS =
  "inline-block rounded-full border border-accent/40 bg-accent/15 px-2 py-0.5 text-[10px] text-content-strong";

/** One artifact chip whose slot has archived versions: click opens a small dropdown listing
 * them ("v1", "v2", …) plus the current version for reference; picking one and confirming with
 * "Zurückdrehen" calls `onConfirm(version)`. Owns its open/selected state independently per
 * chip — simpler than a shared "which chip is open" slot on the parent, and multiple dropdowns
 * open at once is harmless. */
function RevertChip({
  text,
  title,
  archivedVersions,
  currentVersion,
  onConfirm,
}: {
  text: string;
  title?: string;
  archivedVersions: number[];
  currentVersion: number;
  onConfirm: (version: number) => void;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);

  return (
    <div className="relative">
      <button
        type="button"
        title={title}
        onClick={() => {
          setOpen((prev) => !prev);
          setSelected(null);
        }}
        className={`${SESSION_CHIP_CLS} cursor-pointer hover:bg-accent/25`}
      >
        {text}
      </button>
      {open && (
        <div className="absolute z-10 mt-1 flex flex-col gap-1 rounded border border-bezel bg-surface-1 p-1.5 text-[10px] shadow-lg">
          <div className="text-content-faint">aktuell: v{currentVersion}</div>
          <div className="flex gap-1">
            {archivedVersions.map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={selected === v}
                onClick={() => setSelected(v)}
                className={`rounded border px-1.5 py-0.5 ${
                  selected === v
                    ? "border-accent bg-accent/30 text-content-strong"
                    : "border-bezel text-content-muted hover:text-content-strong"
                }`}
              >
                v{v}
              </button>
            ))}
          </div>
          <button
            type="button"
            disabled={selected === null}
            onClick={() => {
              if (selected === null) return;
              onConfirm(selected);
              setOpen(false);
              setSelected(null);
            }}
            className="rounded bg-accent px-1.5 py-0.5 font-medium text-accent-ink disabled:opacity-40"
          >
            Zurückdrehen
          </button>
        </div>
      )}
    </div>
  );
}

/** One chip's render data: a plain read-only pill, or (when `onRevert` is wired and the slot has
 * archived versions) a revert-capable button chip. */
type SessionChipData =
  | { key: string; kind: "plain"; text: string; title?: string }
  | {
      key: string;
      kind: "revert";
      text: string;
      title?: string;
      archivedVersions: number[];
      currentVersion: number;
      onConfirm: (version: number) => void;
    };

/** Board chips: a restored-artifacts chip when a resume brought any back, a review-count chip
 * when any exist, then one version chip per present artifact (chain order) — e.g. "♻️ 2",
 * "🎬 5", "storyline v2", "script v1". When `onRevert` is given, an artifact chip with archived
 * versions renders as a button that opens a small revert dropdown ({@link RevertChip}) instead
 * of a plain pill — chips without archived versions are never affected. */
function SessionChips({
  status,
  onRevert,
}: {
  status: ProductionStatus;
  onRevert?: (artifact: string, version: number) => void;
}): ReactElement | null {
  const chips: SessionChipData[] = [];

  // Available before the board exists too — `job` (and its `restored` list) sits outside the
  // board_ready discriminant, so a resume's restore is visible even in the queued/running window.
  const restored = status.job?.restored;
  if (restored !== undefined && restored.length > 0) {
    chips.push({
      key: "restored",
      kind: "plain",
      text: `♻️ ${restored.length}`,
      title: `Wiederhergestellt: ${restored.map(sessionArtifactLabel).join(", ")}`,
    });
  }

  // Before the board exists there is nothing else to chip — and dereferencing the board fields
  // on that shape was a live crash: every new session passes through a queued/running window in
  // which the endpoint reports only { job, board_ready: false }.
  if (status.board_ready) {
    if (status.scene_reviews.count > 0) {
      // A degraded review is one the VLM never actually produced — a board with zero visual
      // analysis used to look identical to a fully reviewed one in this very chip.
      const degraded = status.scene_reviews.degraded_count;
      chips.push({
        key: "reviews",
        kind: "plain",
        text:
          degraded > 0
            ? `🎬 ${status.scene_reviews.count} (${degraded}⚠)`
            : `🎬 ${status.scene_reviews.count}`,
        title:
          degraded > 0
            ? `${degraded} Review(s) ohne echte Bildanalyse (Szenen ${status.scene_reviews.degraded_scenes.join(", ")})`
            : undefined,
      });
    }
    for (const name of SESSION_ARTIFACT_ORDER) {
      // Defensive: a pre-Kontaktbogen backend does not send the contact_sheet key at all —
      // the type says required, the wire decides. A missing key must skip, never crash.
      const info = status.artifacts[name] as ProductionArtifactState | undefined;
      if (info === undefined || info.version === null) continue;
      const warnings: string[] = [];
      if (info.stale === true) {
        warnings.push("gehört zu einem älteren Skript (stale)");
      }
      if (info.checks_ok === false) {
        warnings.push(`Checks fehlgeschlagen: ${(info.failed_checks ?? []).join(", ")}`);
      }
      // Unknown is not current: null means the artifact predates provenance and cannot be
      // judged either way — saying nothing here would present it as proven-fresh.
      const unknown = info.stale === null ? "Provenienz unbekannt (älteres Board)" : undefined;
      const text = `${SESSION_ARTIFACT_LABELS[name]} v${info.version}${warnings.length > 0 ? " ⚠" : ""}`;
      const title = warnings.length > 0 ? warnings.join(" · ") : unknown;
      if (onRevert !== undefined && info.archived_versions.length > 0) {
        const revert = onRevert;
        const artifact = name;
        chips.push({
          key: name,
          kind: "revert",
          text,
          title,
          archivedVersions: info.archived_versions,
          currentVersion: info.version,
          onConfirm: (version) => revert(artifact, version),
        });
      } else {
        chips.push({ key: name, kind: "plain", text, title });
      }
    }
  }
  if (chips.length === 0) return null;
  return (
    <div className="mb-1 flex flex-wrap gap-1">
      {chips.map((chip) =>
        chip.kind === "plain" ? (
          <span key={chip.key} title={chip.title} className={SESSION_CHIP_CLS}>
            {chip.text}
          </span>
        ) : (
          <RevertChip
            key={chip.key}
            text={chip.text}
            title={chip.title}
            archivedVersions={chip.archivedVersions}
            currentVersion={chip.currentVersion}
            onConfirm={chip.onConfirm}
          />
        ),
      )}
    </div>
  );
}

/** The load-bearing facts of a terminal session job's `result_json`, narrowed defensively —
 * the hook only guarantees `unknown` (an opaque, agent-produced payload). Unrecognized shapes or
 * mistyped fields fall back to safe defaults instead of throwing. */
interface SessionResultInfo {
  ok: boolean;
  weak: boolean;
  exportId: string | null;
}

function narrowSessionResult(value: unknown): SessionResultInfo {
  const record =
    typeof value === "object" && value !== null ? (value as Record<string, unknown>) : {};
  return {
    ok: typeof record.ok === "boolean" ? record.ok : false,
    weak: typeof record.weak === "boolean" ? record.weak : false,
    exportId: typeof record.export_id === "string" ? record.export_id : null,
  };
}

/** Done-phase result card — same tone language as v1's DoneCard (ok/weak/failed), plus the
 * export id when the terminal job result carries one. */
function SessionCard({ jobResult }: { jobResult: unknown }): ReactElement {
  const info = narrowSessionResult(jobResult);
  const tone = !info.ok
    ? { cls: "border-status-err text-status-err", text: "✗ Nicht geklappt" }
    : info.weak
      ? { cls: "border-status-warn text-status-warn", text: "⚠ Fertig — QA meldet Schwächen" }
      : { cls: "border-status-ok text-status-ok", text: "✓ Session fertig" };
  return (
    <div className={`mt-1 rounded-md border bg-surface-2 px-1.5 py-1 ${tone.cls}`}>
      <div className="font-medium">{tone.text}</div>
      {info.exportId !== null && (
        <div className="text-[10px] text-content-faint">Export: {info.exportId}</div>
      )}
    </div>
  );
}

/** Shared input/button look, matching the v1 input row exactly. */
const SESSION_INPUT_CLS =
  "min-w-0 flex-1 rounded border border-bezel bg-surface-1 px-1.5 py-1 text-[11px] text-content-strong disabled:opacity-40";
const SESSION_BUTTON_CLS =
  "rounded bg-accent px-2 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40";

/** Extracts an HTTP status code and a human-readable detail from a `LauraClient` request error
 * (`Error("<status>: <body>")` — see api.ts's `request()`), decoding a FastAPI `{"detail": "..."}`
 * body when present, since that's how the revert endpoint's 409/422 responses are shaped. */
function parseRevertError(e: unknown): { code: number | null; detail: string } {
  const message = e instanceof Error ? e.message : String(e);
  const match = message.match(/^(\d{3}):\s*([\s\S]*)$/);
  if (match === null) return { code: null, detail: message };
  const code = Number(match[1]);
  const body = match[2];
  try {
    const parsed = JSON.parse(body) as unknown;
    if (typeof parsed === "object" && parsed !== null && "detail" in parsed) {
      const detail = (parsed as Record<string, unknown>).detail;
      if (typeof detail === "string") return { code, detail };
    }
  } catch {
    // Not JSON — fall through to the raw body text.
  }
  return { code, detail: body };
}

/**
 * Session (v2) body: idle task input + start; running spinner (resume_point) + board chips; done
 * result card + a follow-up input (sendMessage); error message + reset. Owns only the current
 * draft text plus a revert-in-flight snapshot — everything else comes from `session` (driven by
 * useProductionSession, exercised in tests via a mocked client, never by mocking the hook itself).
 */
function SessionPanel({
  session,
  assetId,
  client,
}: {
  session: ProductionSessionController;
  assetId: string | null;
  client: LauraClient;
}): ReactElement {
  const { state } = session;
  const [draft, setDraft] = useState("");
  // Optimistic board snapshot from the most recent revert response, shown immediately instead of
  // waiting up to POLL_INTERVAL_MS for the next tick. Dropped as soon as the hook's own `status`
  // changes (a fresh poll landed) so a real update can never be shadowed by a stale override.
  const [revertStatus, setRevertStatus] = useState<ProductionStatus | null>(null);
  const [revertHint, setRevertHint] = useState<string | null>(null);
  const effectiveStatus = revertStatus ?? state.status;
  // Chips (and the transient revert hint) are relevant from the moment a board exists through
  // both terminal outcomes — a failed run can still carry a board with archived versions worth
  // reverting from. Only "idle" (no session yet) has nothing to show.
  const chipsPhase =
    state.phase === "running" || state.phase === "done" || state.phase === "error";

  // Both the optimistic revertStatus snapshot and the transient revert hint stop being
  // trustworthy the moment either a fresh poll result lands (`state.status` changes) or a new
  // message/run starts (`state.jobId` changes immediately in start()/sendMessage(), before that
  // run's first poll has even landed) — otherwise a stale "♻️ Wiederhergestellt: …" hint from an
  // earlier revert can resurface and persist through a run it no longer describes, now that chips
  // (and this hint) render across "running" too.
  useEffect(() => {
    setRevertStatus(null);
    setRevertHint(null);
  }, [state.status, state.jobId]);

  const handleRevert = (artifact: string, version: number): void => {
    const sessionId = state.sessionId;
    if (sessionId === null) return;
    setRevertHint(null);
    void client
      .revertProduction(sessionId, artifact, version)
      .then((response) => {
        setRevertStatus(response.status);
        if (response.restored.length > 0) {
          setRevertHint(
            `♻️ Wiederhergestellt: ${response.restored.map(sessionArtifactLabel).join(", ")}`,
          );
        }
      })
      .catch((e: unknown) => {
        const { code, detail } = parseRevertError(e);
        setRevertHint(code === 409 ? "Lauf aktiv — warte, bis der Job fertig ist" : detail);
      });
  };

  const handleStart = (): void => {
    const text = draft.trim();
    if (text === "" || assetId === null) return;
    setDraft("");
    void session.start(text);
  };

  const handleSend = (): void => {
    const text = draft.trim();
    if (text === "") return;
    setDraft("");
    void session.sendMessage(text);
  };

  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-1.5 text-[11px]">
        {state.phase === "idle" && (
          <p className="text-content-faint">
            {assetId === null
              ? "Wähle ein Video, dann beschreib den Auftrag."
              : "Beschreib den Auftrag — die Session merkt sich den Fortschritt über mehrere Nachrichten."}
          </p>
        )}
        {state.phase === "running" && (
          <div className="mb-1 animate-pulse text-content-faint">
            {effectiveStatus !== null && effectiveStatus.board_ready
              ? `⚙ ${effectiveStatus.resume_point} …`
              : effectiveStatus !== null && effectiveStatus.job !== null
                ? `⚙ ${effectiveStatus.job.status} …`
                : "läuft …"}
          </div>
        )}
        {/* Chips render across running + finished (done/error) phases so the chain stays visible
            throughout — but the revert endpoint 409s on a queued/running job (see
            2026-07-21-revert-ui-design.md), so `onRevert` is wired only once the run has actually
            landed. Passing `undefined` during "running" makes SessionChips/RevertChip fall back
            to a plain, non-interactive pill — the UI never offers an action the API would refuse. */}
        {chipsPhase && effectiveStatus !== null && (
          <SessionChips
            status={effectiveStatus}
            onRevert={state.phase === "running" ? undefined : handleRevert}
          />
        )}
        {chipsPhase && revertHint !== null && (
          <div className="mb-1 text-content-faint" role="status">
            {revertHint}
          </div>
        )}
        {state.phase === "done" && <SessionCard jobResult={state.jobResult} />}
        {state.phase === "error" && (
          <div className="mb-1 text-status-err" role="alert">
            ⚠ {state.error}
          </div>
        )}
      </div>
      <div className="flex gap-1 border-t border-bezel p-1.5">
        {state.phase === "idle" && (
          <>
            <input
              aria-label="Sitzungsauftrag"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleStart();
              }}
              placeholder="Mach mir einen 60s-Short über …"
              disabled={assetId === null}
              className={SESSION_INPUT_CLS}
            />
            <button
              type="button"
              onClick={handleStart}
              disabled={assetId === null || draft.trim() === ""}
              className={SESSION_BUTTON_CLS}
            >
              Start
            </button>
          </>
        )}
        {state.phase === "done" && (
          <>
            <input
              aria-label="Folgeanfrage"
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleSend();
              }}
              placeholder="z. B. Kapitel 2 andere Szene — oder: zurück zu storyline v1"
              className={SESSION_INPUT_CLS}
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={draft.trim() === ""}
              className={SESSION_BUTTON_CLS}
            >
              Senden
            </button>
          </>
        )}
        {state.phase === "error" && (
          <button
            type="button"
            onClick={session.reset}
            className="ml-auto rounded border border-bezel px-2 py-1 text-[11px] font-medium text-content-muted hover:text-content-strong"
          >
            Zurücksetzen
          </button>
        )}
      </div>
    </>
  );
}

/**
 * Docked assistant chat: the user types a request, the agent team runs live via the streaming
 * endpoint, and each event renders as a chat line. `onEvent` lets the parent refresh the app views
 * (Timeline/Player/Scenes) as artifacts appear, so the app "fills in" alongside the chat.
 */
export function ChatPanel({ client, assetId, onEvent }: ChatPanelProps): ReactElement {
  const [mode, setMode] = useState<"v1" | "v2">("v1");
  const [input, setInput] = useState("");
  const [topic, setTopic] = useState<string | null>(null);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [running, setRunning] = useState(false);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const session = useProductionSession(client, assetId);

  // Follow the live run: keep the newest event in view unless the user scrolled up to read.
  useEffect(() => {
    const el = scrollRef.current;
    if (el === null) return;
    const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 80;
    if (running || nearBottom) el.scrollTop = el.scrollHeight;
  }, [messages, running]);

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
    void client
      .streamAutoShort(assetId, { topic: text }, append)
      .catch((e: unknown) => {
        append({ type: "error", message: String(e) });
      })
      .finally(() => setRunning(false)); // safety net: reset even if the stream ends abruptly
  };

  return (
    <aside className="flex w-80 shrink-0 flex-col border-l border-bezel bg-surface-1">
      <header className="border-b border-bezel px-2 py-1.5 text-[11px] font-medium text-content-muted">
        <div className="flex items-center justify-between gap-2">
          <span>🤖 Assistent</span>
          <div className="flex gap-0.5 rounded border border-bezel p-0.5">
            <button
              type="button"
              onClick={() => setMode("v1")}
              aria-pressed={mode === "v1"}
              className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                mode === "v1"
                  ? "bg-accent text-accent-ink"
                  : "text-content-faint hover:text-content-muted"
              }`}
            >
              Stream (v1)
            </button>
            <button
              type="button"
              onClick={() => setMode("v2")}
              aria-pressed={mode === "v2"}
              className={`rounded px-1.5 py-0.5 text-[10px] font-medium ${
                mode === "v2"
                  ? "bg-accent text-accent-ink"
                  : "text-content-faint hover:text-content-muted"
              }`}
            >
              Session (v2)
            </button>
          </div>
        </div>
      </header>
      {mode === "v1" ? (
        <>
          <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-2 py-1.5 text-[11px]">
            {topic !== null && (
              <div className="mb-1.5 rounded-md bg-accent/15 px-1.5 py-1 text-content-strong">
                <span className="font-medium">Du:</span> {topic}
              </div>
            )}
            {messages.map((m) => (
              <EventLine key={m.id} event={m.event} />
            ))}
            {running && (
              <div className="mb-1 animate-pulse text-content-faint">⏳ Agenten arbeiten …</div>
            )}
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
        </>
      ) : (
        <SessionPanel session={session} assetId={assetId} client={client} />
      )}
    </aside>
  );
}
