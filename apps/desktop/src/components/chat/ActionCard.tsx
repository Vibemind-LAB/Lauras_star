import { type ReactElement, useEffect, useRef, useState } from "react";

import type {
  AgentEvent,
  ChatMessage,
  LauraClient,
  ProductionBoardStatus,
  ProductionStatus,
} from "../../api";
import { parseJobError, useJobStatus } from "../../hooks/useJobStatus";
import { log } from "../../shared/log";
import { CardErrorBoundary } from "./CardErrorBoundary";
import { ContactSheetApprovalCard } from "./ContactSheetApprovalCard";
import { EventLine } from "./EventLine";
import { SceneSelectionCard } from "./SceneSelectionCard";
import { parseRevertError, SessionChips, sessionArtifactLabel } from "./SessionChips";
import { VisualSelectionCard } from "./VisualSelectionCard";

/** Same cadence the pre-chat production panel used for its board/job poll — kept in step so a
 * chat thread narrating a session feels like the rest of the app, not a separate rhythm. */
const POLL_INTERVAL_MS = 2500;

/** How many of the accumulated events show before the „alle anzeigen" expander is needed. */
const EVENT_PREVIEW_COUNT = 5;

/** The load-bearing facts of an `action` message, narrowed defensively — `content` is typed
 * `Record<string, unknown>` (see api.ts's `ChatMessage`), and its real shape comes from the
 * backend's per-tool handlers (services/local-api/src/laura/chat/executor.py):
 * `{ tool, args, refs, outcome }`, where `refs` is tool-specific (session_id/job_id for
 * start_short/start_overview/follow_up, asset_ids/job_ids for import_urls). */
interface ActionContent {
  tool: string;
  refs: Record<string, unknown>;
  outcome: string;
}

function narrowActionContent(content: Record<string, unknown>): ActionContent {
  const tool = typeof content.tool === "string" ? content.tool : "";
  const refs =
    typeof content.refs === "object" && content.refs !== null
      ? (content.refs as Record<string, unknown>)
      : {};
  const outcome = typeof content.outcome === "string" ? content.outcome : "";
  return { tool, refs, outcome };
}

/** One segment row of a `review_transcript` card's payload (`_segment_review_row` in
 * services/local-api/src/laura/chat/executor.py). */
interface ReviewSegmentRow {
  index: number;
  id: string;
  start_s: number;
  text: string;
}

/** The load-bearing facts of a `review_transcript` action card's `content.payload` — Gate A
 * (Task 5's `_review_transcript_content`): `{ confirmed_at, segments (capped server-side at 100),
 * total }`. `total` can exceed `segments.length` even below the cap, so the card computes its own
 * remainder line rather than trusting the two to match. Narrowed defensively for the same reason
 * as `narrowActionContent` — `content` is typed `Record<string, unknown>`. */
export interface ReviewTranscriptPayload {
  confirmedAt: string | null;
  segments: ReviewSegmentRow[];
  total: number;
}

// Exported (unlike the file's other narrow* helpers) so ActionCard.test.tsx can unit-test its
// fallback branches directly — a render-level assertion of `total`'s fallback is vacuous
// (`NaN - n` renders no remainder line the same as `0`, whether or not the `typeof` guard is
// there at all), so those two cases need a direct call, not a rendered card.
export function narrowReviewTranscriptPayload(content: Record<string, unknown>): ReviewTranscriptPayload {
  const payload =
    typeof content.payload === "object" && content.payload !== null
      ? (content.payload as Record<string, unknown>)
      : {};
  const confirmedAt = typeof payload.confirmed_at === "string" ? payload.confirmed_at : null;
  const rawSegments = Array.isArray(payload.segments) ? payload.segments : [];
  const segments: ReviewSegmentRow[] = rawSegments.map((raw, i) => {
    const row = typeof raw === "object" && raw !== null ? (raw as Record<string, unknown>) : {};
    return {
      index: typeof row.index === "number" ? row.index : i + 1,
      id: typeof row.id === "string" ? row.id : String(i),
      start_s: typeof row.start_s === "number" ? row.start_s : 0,
      text: typeof row.text === "string" ? row.text : "",
    };
  });
  const total = typeof payload.total === "number" ? payload.total : segments.length;
  return { confirmedAt, segments, total };
}

/**
 * Gate A (`review_transcript`/`correct_transcript` — both share this card shape; the executor
 * hard-codes `content.tool = "review_transcript"` for either, see `_review_transcript_content`):
 * the confirmed/unconfirmed badge, the segment list (scrollable — the backend already caps it at
 * 100, this just keeps a long-but-under-cap list from pushing the rest of the thread offscreen),
 * a remainder line when `total` exceeds what is shown, and the correction hint. Read-only —
 * corrections and confirmation both happen via chat messages routed to
 * `correct_transcript`/`confirm_transcript`, never buttons on this card.
 */
function ReviewTranscriptCard({ message }: { message: ChatMessage }): ReactElement {
  const { confirmedAt, segments, total } = narrowReviewTranscriptPayload(message.content);
  const rest = total - segments.length;

  return (
    <div className="mb-1.5 rounded-md border border-bezel bg-surface-2 px-1.5 py-1 text-[11px]">
      <div className="mb-1 flex items-center justify-between gap-1">
        <span className="font-medium text-content-strong">Transkript prüfen</span>
        {confirmedAt !== null ? (
          <span className="text-status-ok">✓ bestätigt</span>
        ) : (
          <span className="text-content-faint">unbestätigt</span>
        )}
      </div>
      <div className="mb-1 max-h-64 overflow-y-auto">
        {segments.map((seg) => (
          <div key={seg.id} className="text-content-muted">
            #{seg.index} · {seg.start_s}s · {seg.text}
          </div>
        ))}
      </div>
      {rest > 0 && <div className="mb-1 text-content-faint">… und {rest} weitere Segmente</div>}
      <div className="text-content-faint">
        Korrigieren per Nachricht: {"‚ersetze in Segment 3 …’"}
      </div>
    </div>
  );
}

/** The single job this card tracks: `refs.job_id` (start_overview) or the first entry of
 * `refs.job_ids` (import_urls — a URL import can fan out into several jobs when the URL is a
 * playlist/channel; the card follows the first one, matching its single running/done/failed
 * line). */
function firstJobId(refs: Record<string, unknown>): string | null {
  if (typeof refs.job_id === "string") return refs.job_id;
  if (Array.isArray(refs.job_ids)) {
    const first = refs.job_ids.find((entry): entry is string => typeof entry === "string");
    if (first !== undefined) return first;
  }
  return null;
}

/** The load-bearing facts of a finished production run's board status, narrowed defensively
 * (`status` is null when the one-shot final fetch itself failed). `exportId` reads off the JOB
 * (`ProductionJobState.export_id`) — the artifacts map carries no export id of its own.
 * `qaVerdict`/`qaFailedChecks` read the qa_report entry's `checks_ok`/`failed_checks` — the only
 * QA signal `ProductionArtifactState` exposes today (QaReport carries no `checks` list yet, so
 * this stays unset on the current backend; ready for the day it does, per the brief's "QA
 * verdict when present"). */
interface ProductionResult {
  exportId: string | null;
  ratioPercent: number | null;
  qaVerdict: "ship" | "revise" | null;
  qaFailedChecks: string[];
}

function narrowProductionResult(status: ProductionStatus | null): ProductionResult {
  const exportId = status?.job?.export_id ?? null;
  let ratioPercent: number | null = null;
  let qaVerdict: "ship" | "revise" | null = null;
  let qaFailedChecks: string[] = [];
  if (status !== null && status.board_ready) {
    const render = status.artifacts.render_report;
    if (typeof render?.target_ratio === "number") {
      ratioPercent = Math.round(render.target_ratio * 100);
    }
    const qa = status.artifacts.qa_report;
    if (qa?.checks_ok === true) {
      qaVerdict = "ship";
    } else if (qa?.checks_ok === false) {
      qaVerdict = "revise";
      qaFailedChecks = qa.failed_checks ?? [];
    }
  }
  return { exportId, ratioPercent, qaVerdict, qaFailedChecks };
}

/** One line of a pending `script_gate`'s script, exactly `ProductionBoardStatus`'s
 * `script_lines` element type (Task 11, api.ts) — reused rather than redeclared. */
type ScriptGateLine = NonNullable<ProductionBoardStatus["script_lines"]>[number];

/** Gate B (script checkpoint, Task 7/11): the script's lines when this session's board has the
 * gate enabled and the script is still awaiting approval — `null` when there is nothing to show
 * (no board yet, gate off, already approved). Prefers the server-computed `script_gate.pending`
 * flag; falls back to `enabled && !approved` if a future payload ever omits it, the same
 * defensive-narrowing posture as `narrowProductionResult` above. */
function narrowPendingScript(status: ProductionStatus | null): ScriptGateLine[] | null {
  if (status === null || !status.board_ready) return null;
  const gate = status.script_gate;
  if (gate === undefined) return null;
  const pending =
    typeof gate.pending === "boolean" ? gate.pending : gate.enabled && !gate.approved;
  if (!pending) return null;
  return status.script_lines ?? [];
}

/** Gate S (scene checkpoint, GS1/GS3-Payload): the candidate proposal plus the asset id its
 * thumbnails read from, when this session's board has the gate enabled and no pick has been
 * confirmed yet — `null` when there is nothing to show. Bundled together (rather than reading
 * `status.meta.asset_id` separately at the call site) so the two can never end up read from two
 * different `status` snapshots. Gate S runs BEFORE Gate B in the pipeline (scene selection ->
 * storyline -> script), so its pending check takes priority at the render site below. */
function narrowPendingSceneGate(
  status: ProductionStatus | null,
): { gate: NonNullable<ProductionBoardStatus["scene_gate"]>; assetId: string } | null {
  if (status === null || !status.board_ready) return null;
  const gate = status.scene_gate;
  if (gate === undefined || !gate.pending) return null;
  return { gate, assetId: status.meta.asset_id };
}

/** Visual-only recut checkpoint. A pending request without a proposal identity cannot be
 * confirmed safely, so malformed/in-progress payloads do not render an actionable card. */
function narrowPendingVisualGate(
  status: ProductionStatus | null,
): {
  gate: NonNullable<ProductionBoardStatus["visual_selection_gate"]>;
  assetId: string;
} | null {
  if (status === null || !status.board_ready) return null;
  const gate = status.visual_selection_gate;
  if (
    gate === undefined ||
    !gate.pending ||
    typeof gate.proposal_id !== "string" ||
    gate.beats.length === 0
  ) {
    return null;
  }
  return { gate, assetId: status.meta.asset_id };
}

/** Contact-sheet checkpoint. The card approves only a concrete content hash; the PNG itself
 * remains in the normal `ChatPreview` lane. */
function narrowPendingContactSheetGate(
  status: ProductionStatus | null,
): NonNullable<ProductionBoardStatus["contact_sheet_gate"]> | null {
  if (status === null || !status.board_ready) return null;
  const gate = status.contact_sheet_gate;
  if (gate === undefined || !gate.pending || typeof gate.current_sheet_hash !== "string") {
    return null;
  }
  return gate;
}

/** Statuses `useJobStatus` (and the backend jobs runner) consider terminal-non-success:
 * `failed` outright, or `cancelled` — a job someone/something killed never writes its own
 * "done" event line either, so it must finalize the card the same way a `failed` job does
 * (see the job-status backstop below). */
const JOB_TERMINAL_FAILURE = new Set(["failed", "cancelled"]);

/**
 * `start_short` / `follow_up`: narrates the live run via the production session's event log
 * (`GET /production/{sessionId}/events`, polled every {@link POLL_INTERVAL_MS}) — the last
 * {@link EVENT_PREVIEW_COUNT} lines via the re-exported `EventLine`, with an „alle anzeigen"
 * expander for the rest.
 *
 * The events reader always serves the NEWEST run log for the session, which two backstops
 * guard against here (via `jobId` — `refs.job_id`, written by the executor's
 * `_handle_start_short`/`_handle_start_overview`/`_handle_follow_up`):
 * - A follow-up's first poll can land on the PREVIOUS run's log, whose last line is already
 *   `{"type":"done"}` — the events effect defers finalizing on `done` to the job-status effect
 *   below instead of trusting it outright, so a stale done never terminally shows a stale result.
 * - A dead/killed job never writes `done` at all — the job-status effect finalizes as FAILED
 *   the moment the tracked job itself reports `failed`/`cancelled`, independent of whether the
 *   events log ever said anything, so the card stops spinning „⚙ läuft …" forever.
 * A `null` jobId (an old message from before this backstop existed, or a tool that never wrote
 * one) behaves exactly as before: `done` finalizes immediately, no job cross-check.
 */
function ProductionActionCard({
  client,
  sessionId,
  jobId,
  initialOutcome,
  onFocus,
}: {
  client: LauraClient;
  sessionId: string;
  jobId: string | null;
  initialOutcome: string;
  onFocus?: () => void;
}): ReactElement {
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [phase, setPhase] = useState<"running" | "done" | "failed">(
    initialOutcome === "running" ? "running" : "done",
  );
  const [showAll, setShowAll] = useState(false);
  const [status, setStatus] = useState<ProductionStatus | null>(null);
  // The job this card is currently tracking. Starts as the prop (`refs.job_id`), but any
  // persisted gate confirm (see `refreshAfterConfirm` below) can hand it a NEW job id — the
  // resume the confirm itself enqueued — so the card keeps narrating live.
  const [activeJobId, setActiveJobId] = useState<string | null>(jobId);
  // Set once the events reader itself has returned a `done` batch — see the module doc above.
  // Only meaningful when `activeJobId` is non-null; the null-jobId path finalizes straight off
  // the events poll and never reads this.
  const [eventsDone, setEventsDone] = useState(false);
  // The events cursor lives in a ref, not state: it must be read/written synchronously across
  // poll ticks without waiting on a re-render, the same reason useProductionSession keeps its
  // generation token in a ref rather than state.
  const cursorRef = useRef(0);
  const tickInFlightRef = useRef(false);
  // Guards the job-status effect's async success finalization against firing twice (e.g. a
  // second jobStatus update landing while the first getProductionStatus fetch is still in
  // flight) — the same overlap the events poll's tickInFlightRef guards against.
  const finalizingRef = useRef(false);
  // Optimistic board snapshot from the most recent revert response, shown immediately instead of
  // waiting for the next status fetch to land — mirrors the same pattern the pre-chat SessionPanel
  // used (see git history: ChatPanel.tsx's `SessionPanel`, migrated here). Dropped as soon as a
  // fresh `status` (a real fetch) or a new tracked job arrives, so a real update can never be
  // shadowed by a stale override.
  const [revertStatus, setRevertStatus] = useState<ProductionStatus | null>(null);
  const [revertHint, setRevertHint] = useState<string | null>(null);
  const effectiveStatus = revertStatus ?? status;

  useEffect(() => {
    setRevertStatus(null);
    setRevertHint(null);
  }, [status, activeJobId]);

  const handleRevert = (artifact: string, version: number): void => {
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

  // Independent job-status poll (same `useJobStatus` every other job-backed card in this file
  // uses): self-stops once the job reaches a terminal status, and keeps its last known value
  // around afterward (never re-nulled) so the failed-render below still has a reason to show.
  const { jobStatus } = useJobStatus(client, activeJobId);

  // Shared gate refresh: always replaces the board snapshot (so a confirmed card disappears)
  // and resumes live narration when that confirmation enqueued a queued/running job.
  const refreshAfterConfirm = async (): Promise<void> => {
    let fresh: ProductionStatus | null = null;
    try {
      fresh = await client.getProductionStatus(sessionId);
    } catch (e) {
      log.warn("ActionCard: status refresh after gate confirm failed", e);
      return;
    }
    setStatus(fresh);
    const freshJob = fresh !== null && fresh.board_ready ? fresh.job : null;
    if (freshJob !== null && (freshJob.status === "queued" || freshJob.status === "running")) {
      finalizingRef.current = false;
      cursorRef.current = 0;
      setEvents([]);
      setEventsDone(false);
      setActiveJobId(freshJob.id);
      setPhase("running");
    }
  };

  useEffect(() => {
    if (phase !== "running") return;
    let cancelled = false;

    const poll = async (): Promise<void> => {
      // Guards against an overlapping tick: a slow response still in flight when the next
      // interval fires must not race a second request against the same cursor.
      if (tickInFlightRef.current) return;
      tickInFlightRef.current = true;
      try {
        const batch = await client.getProductionEvents(sessionId, cursorRef.current);
        if (cancelled) return;
        cursorRef.current = batch.next;
        setEvents((prev) => [...prev, ...batch.events]);
        if (batch.done) {
          if (activeJobId === null) {
            window.clearInterval(intervalId);
            let finalStatus: ProductionStatus | null = null;
            try {
              finalStatus = await client.getProductionStatus(sessionId);
            } catch (e) {
              log.warn("ActionCard: final production status fetch failed", e);
            }
            if (!cancelled) {
              setStatus(finalStatus);
              setPhase("done");
            }
          } else {
            // Stop re-polling an events log that already said it's done — but let the
            // job-status effect below decide whether the card actually finalizes.
            window.clearInterval(intervalId);
            if (!cancelled) setEventsDone(true);
          }
        }
      } catch (e) {
        if (!cancelled) log.warn("ActionCard: production events poll failed, retrying", e);
      } finally {
        tickInFlightRef.current = false;
      }
    };

    const intervalId = window.setInterval(() => {
      void poll();
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(intervalId);
    };
  }, [client, sessionId, phase, activeJobId]);

  // The job-status backstop: a failure finalizes unconditionally (fixes the dead-job case); a
  // success only finalizes once the events log has ALSO said done (fixes the stale-log case) —
  // a job can flip to "succeeded" before its own run's events poll has caught up.
  useEffect(() => {
    if (phase !== "running" || activeJobId === null || jobStatus === null) return;
    if (JOB_TERMINAL_FAILURE.has(jobStatus.status)) {
      setPhase("failed");
      return;
    }
    if (jobStatus.status === "succeeded" && eventsDone) {
      if (finalizingRef.current) return;
      finalizingRef.current = true;
      void (async () => {
        let finalStatus: ProductionStatus | null = null;
        try {
          finalStatus = await client.getProductionStatus(sessionId);
        } catch (e) {
          log.warn("ActionCard: final production status fetch failed", e);
        }
        setStatus(finalStatus);
        setPhase("done");
      })();
    }
  }, [client, sessionId, activeJobId, jobStatus, phase, eventsDone]);

  const shown = showAll ? events : events.slice(-EVENT_PREVIEW_COUNT);
  const result = narrowProductionResult(status);
  const pendingVisualGate = narrowPendingVisualGate(status);
  const pendingSceneGate = narrowPendingSceneGate(status);
  const pendingScript = narrowPendingScript(status);
  const pendingContactSheetGate = narrowPendingContactSheetGate(status);
  const failReason = jobStatus !== null ? parseJobError(jobStatus) ?? "unbekannter Fehler" : "unbekannter Fehler";

  return (
    <div className="mb-1.5 rounded-md border border-bezel bg-surface-2 px-1.5 py-1 text-[11px]">
      {/* Each line individually guarded: the original white-screen came from exactly one
       * defective `done` event line (see CardErrorBoundary) — one bad event must not take the
       * card's status line (or the app) with it. Slim line fallback, not the card-shaped
       * default, since these render inside this card's own frame already. */}
      {shown.map((event, i) => (
        <CardErrorBoundary
          key={i}
          fallback={
            <div className="mb-1 text-content-faint">
              ⚠ Diese Zeile konnte nicht angezeigt werden.
            </div>
          }
        >
          <EventLine event={event} />
        </CardErrorBoundary>
      ))}
      {!showAll && events.length > EVENT_PREVIEW_COUNT && (
        <button
          type="button"
          onClick={() => setShowAll(true)}
          className="mb-1 block text-[10px] text-accent hover:underline"
        >
          alle anzeigen
        </button>
      )}
      {/* Board chips (artifact chain versions, staleness/checks warnings, scene-review count,
       * restored-on-resume count) — shown as soon as a board snapshot exists, which can happen
       * mid-"running" too (e.g. right after a Gate-S confirm's refreshAfterConfirm re-fetches
       * status before flipping back to running). The revert dropdown is wired only once the run
       * has actually landed — the endpoint 409s on a queued/running job. */}
      {effectiveStatus !== null && (
        <SessionChips
          status={effectiveStatus}
          onRevert={phase === "running" ? undefined : handleRevert}
        />
      )}
      {revertHint !== null && (
        <div className="mb-1 text-content-faint" role="status">
          {revertHint}
        </div>
      )}
      {phase === "running" && (
        <div className="animate-pulse text-content-faint" role="status">
          ⚙ läuft …
        </div>
      )}
      {phase === "failed" && (
        <div className="mt-0.5 text-status-err" role="alert">
          ✗ fehlgeschlagen: {failReason}
        </div>
      )}
      {phase === "done" &&
        (pendingVisualGate !== null ? (
          <VisualSelectionCard
            gate={pendingVisualGate.gate}
            assetId={pendingVisualGate.assetId}
            sessionId={sessionId}
            client={client}
            onConfirmed={refreshAfterConfirm}
          />
        ) : pendingSceneGate !== null ? (
          <SceneSelectionCard
            gate={pendingSceneGate.gate}
            assetId={pendingSceneGate.assetId}
            sessionId={sessionId}
            client={client}
            onConfirmed={() => void refreshAfterConfirm()}
          />
        ) : pendingScript !== null ? (
          <div className="mt-0.5 rounded border border-bezel bg-surface-1 px-1.5 py-1">
            <div className="text-content-strong">📝 Sprechertext wartet auf Freigabe</div>
            {pendingScript.map((line) => (
              <div key={`${line.chapter}-${line.scene_number}`} className="mt-0.5 text-content-muted">
                Kapitel {line.chapter} · Szene {line.scene_number} · {line.text}
              </div>
            ))}
            <div className="mt-0.5 text-content-faint">
              Antworte {"‚Script freigeben’"} oder nenne Änderungen.
            </div>
          </div>
        ) : pendingContactSheetGate !== null ? (
          <ContactSheetApprovalCard
            gate={pendingContactSheetGate}
            sessionId={sessionId}
            client={client}
            onConfirmed={refreshAfterConfirm}
          />
        ) : result.exportId !== null ? (
          <div className="mt-0.5 rounded border border-bezel bg-surface-1 px-1.5 py-1">
            <div className="text-content-strong">
              Export: {result.exportId}
              {result.ratioPercent !== null && ` · ${result.ratioPercent}%`}
            </div>
            {result.qaVerdict !== null && (
              <div
                className={result.qaVerdict === "ship" ? "text-status-ok" : "text-status-warn"}
              >
                QA:{" "}
                {result.qaVerdict === "ship"
                  ? "✓ ship"
                  : `⚠ revise (${result.qaFailedChecks.join(", ")})`}
              </div>
            )}
            <button
              type="button"
              onClick={onFocus}
              className="mt-0.5 text-accent hover:underline"
            >
              ▶ ansehen
            </button>
            <div className="mt-0.5 text-content-faint">
              Weiter anpassen: sag z. B. ‚mach den Hook kürzer' — oder frag einfach.
            </div>
          </div>
        ) : (
          <div className="mt-0.5 text-content-faint">Kein Export erzeugt.</div>
        ))}
    </div>
  );
}

/**
 * `start_overview` / `import_urls`: a single tracked job, polled via the existing
 * `useJobStatus` hook (same 1500 ms cadence every other job-backed panel in the app uses).
 */
function JobActionCard({ client, jobId }: { client: LauraClient; jobId: string }): ReactElement {
  const { jobStatus, error, isRunning } = useJobStatus(client, jobId);
  if (isRunning) {
    return (
      <div className="text-content-faint animate-pulse" role="status">
        ⚙ läuft
      </div>
    );
  }
  if (jobStatus?.status === "succeeded") {
    return <div className="text-status-ok">✓ fertig</div>;
  }
  return (
    <div className="text-status-err" role="alert">
      ✗ fehlgeschlagen: {error ?? "unbekannter Fehler"}
    </div>
  );
}

/**
 * Gate S's chat path (`select_scenes` — the user picking scenes via a message like "nimm 2 und
 * 5 statt 4" instead of the card's tiles, `_handle_select_scenes` in
 * services/local-api/src/laura/chat/executor.py): a simple confirmation line, not the live
 * event narration `ProductionActionCard` gives `start_short`/`follow_up`/`approve_script` — the
 * executor already appended the full sentence ("Szenen übernommen: […]. Die Produktion läuft mit
 * deiner Auswahl weiter." or the idempotent-repeat variant) as its own preceding text message,
 * so this card only needs to mark whether the resume it kicked off is still going. */
function SelectScenesLine({ outcome }: { outcome: string }): ReactElement {
  if (outcome === "running") {
    return (
      <div className="animate-pulse text-content-faint" role="status">
        ⚙ Auswahl wird angewendet …
      </div>
    );
  }
  return <div className="text-status-ok">✓ Auswahl übernommen</div>;
}

/** Fallback for a tool this card does not (yet) know how to narrate — never crashes on an
 * unrecognized shape, just shows the raw tool name (or a generic label when even that is
 * missing). */
function UnknownActionLine({ tool }: { tool: string }): ReactElement {
  return <div className="text-content-faint">{tool !== "" ? tool : "Aktion"}</div>;
}

export interface ActionCardProps {
  message: ChatMessage;
  client: LauraClient;
  /** Focus the just-created preview (Task 11's player) — wired once that lands. */
  onFocus?: () => void;
}

/**
 * One `action` message rendered as a thread card. Dispatches on `content.tool`: the production
 * tools (`start_short`/`follow_up` plus the three approval resumes — all carry the SAME
 * `refs.session_id`/`refs.job_id` contract in `services/local-api/src/laura/chat/executor.py`)
 * narrate live via the session's event log
 * ({@link ProductionActionCard}); the one-shot job tools (`start_overview`/`import_urls`) show a
 * plain running/done/failed line ({@link JobActionCard}); Gate A's `review_transcript` (also
 * emitted by `correct_transcript`/`confirm_transcript` — the executor hard-codes the same tool
 * name for all three) renders the segment list read-only ({@link ReviewTranscriptCard}); Gate S's
 * chat path `select_scenes` renders a plain confirmation line ({@link SelectScenesLine}) — the
 * card itself only needs to be recognized, not to fall into {@link UnknownActionLine}.
 */
export function ActionCard({ message, client, onFocus }: ActionCardProps): ReactElement {
  const { tool, refs, outcome } = narrowActionContent(message.content);

  if (
    tool === "start_short" ||
    tool === "follow_up" ||
    tool === "approve_script" ||
    tool === "select_visuals" ||
    tool === "approve_contact_sheet"
  ) {
    const sessionId = typeof refs.session_id === "string" ? refs.session_id : null;
    if (sessionId === null) return <UnknownActionLine tool={tool} />;
    const jobId = typeof refs.job_id === "string" ? refs.job_id : null;
    return (
      <ProductionActionCard
        client={client}
        sessionId={sessionId}
        jobId={jobId}
        initialOutcome={outcome}
        onFocus={onFocus}
      />
    );
  }

  if (tool === "select_scenes") {
    return <SelectScenesLine outcome={outcome} />;
  }

  if (tool === "start_overview" || tool === "import_urls") {
    const jobId = firstJobId(refs);
    if (jobId === null) return <UnknownActionLine tool={tool} />;
    return <JobActionCard client={client} jobId={jobId} />;
  }

  if (tool === "review_transcript") {
    return <ReviewTranscriptCard message={message} />;
  }

  return <UnknownActionLine tool={tool} />;
}
