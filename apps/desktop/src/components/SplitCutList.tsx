import { type ReactElement, useCallback, useState } from "react";

import { type AcceptedSplit, type LauraClient, type SplitCut } from "../api";
import { log } from "../shared/log";

/** The recommended (non-hard) L/J splits, in source order, so the list shows only actionable cuts. */
function recommended(splitCuts: SplitCut[]): SplitCut[] {
  return splitCuts.filter((sc) => sc.kind !== "hard").sort((a, b) => a.seq_cut - b.seq_cut);
}

function kindLabel(kind: SplitCut["kind"]): string {
  if (kind === "L") return "L-Cut";
  if (kind === "J") return "J-Cut";
  return "Hard";
}

/**
 * The „Übernehmen" list for recommended L/J split edits. Each recommended cut shows its kind (J/L),
 * the picture/sound frames + offset, and a per-row „Übernehmen" / „Zurücknehmen" toggle, plus an
 * „Alle übernehmen" action. Accepting POSTs the FULL accepted set (the wire source of truth) via
 * {@link LauraClient.acceptSplitCuts}; taking one back re-posts the set without that entry. The
 * confirmed (stored) set returned by the backend drives the applied badges.
 *
 * HONEST FRAMING: accepting a split does NOT change the internal hard-cut editing timeline — the
 * L/J is applied only in the OTIO source of truth and the exported NLE project (Premiere/FCP/…).
 * Full 2-lane editing on the internal timeline is a deferred step, hence the caption below.
 */
export function SplitCutList({
  client,
  projectId,
  timelineId,
  splitCuts,
}: {
  client: LauraClient;
  projectId: string | null;
  timelineId: string | null;
  splitCuts: SplitCut[];
}): ReactElement | null {
  // The currently-applied offsets keyed by seq_cut — confirmed by the backend after each post.
  const [applied, setApplied] = useState<Map<number, number>>(new Map());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const recs = recommended(splitCuts);

  const post = useCallback(
    async (accepted: AcceptedSplit[]): Promise<void> => {
      if (!projectId || !timelineId) return;
      setBusy(true);
      setError(null);
      try {
        const res = await client.acceptSplitCuts(projectId, timelineId, accepted);
        // Trust the backend's confirmed set (hard offsets dropped) over our optimistic intent.
        setApplied(new Map(res.accepted.map((a) => [a.seqCut, a.offset])));
      } catch (e) {
        log.error("accept split cuts failed", e);
        setError(String(e));
      } finally {
        setBusy(false);
      }
    },
    [client, projectId, timelineId],
  );

  // Toggle one cut: add it to the applied set, or remove it (re-post the rest = „Zurücknehmen").
  const toggle = useCallback(
    (sc: SplitCut): void => {
      const next = new Map(applied);
      if (next.has(sc.seq_cut)) next.delete(sc.seq_cut);
      else next.set(sc.seq_cut, sc.offset);
      void post([...next].map(([seqCut, offset]) => ({ seqCut, offset })));
    },
    [applied, post],
  );

  const acceptAll = useCallback((): void => {
    void post(recs.map((sc) => ({ seqCut: sc.seq_cut, offset: sc.offset })));
  }, [post, recs]);

  if (recs.length === 0) return null;

  return (
    <div className="w-64 rounded-md border border-bezel bg-surface-0 p-2" data-testid="split-cut-list">
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[10px] uppercase tracking-wide text-content-faint">Split-Cuts (L/J)</span>
        <button
          type="button"
          onClick={acceptAll}
          disabled={busy || !projectId || !timelineId}
          className="rounded bg-accent px-2 py-0.5 text-[10px] text-white disabled:opacity-40"
        >
          Alle übernehmen
        </button>
      </div>
      <ul className="space-y-1">
        {recs.map((sc) => {
          const on = applied.has(sc.seq_cut);
          return (
            <li
              key={sc.seq_cut}
              className="flex items-center justify-between gap-2 text-[10px] text-content-muted"
              data-testid={`split-cut-${sc.seq_cut}`}
            >
              <span className="min-w-0 truncate">
                <span className="font-medium text-content-muted">{kindLabel(sc.kind)}</span>{" "}
                Bild {sc.video_frame} · Ton {sc.audio_frame} ({sc.offset > 0 ? "+" : ""}
                {sc.offset})
              </span>
              {on ? (
                <span className="flex shrink-0 items-center gap-1">
                  <span
                    className="rounded bg-status-ok/20 px-1.5 py-0.5 text-status-ok"
                    data-testid={`split-cut-applied-${sc.seq_cut}`}
                  >
                    {kindLabel(sc.kind)} aktiv
                  </span>
                  <button
                    type="button"
                    onClick={() => toggle(sc)}
                    disabled={busy}
                    className="rounded border border-bezel px-1.5 py-0.5 text-content-muted disabled:opacity-40"
                  >
                    Zurücknehmen
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => toggle(sc)}
                  disabled={busy || !projectId || !timelineId}
                  className="shrink-0 rounded bg-accent px-2 py-0.5 text-white disabled:opacity-40"
                >
                  Übernehmen
                </button>
              )}
            </li>
          );
        })}
      </ul>
      {error && <p className="mt-1 text-[10px] text-status-err">{error}</p>}
      {/* Honest framing: the internal timeline blocks do NOT visually change when a split is
          accepted — the L/J lives in the OTIO/exported NLE project, not the hard-cut editing
          timeline. Full 2-lane editing here is a deferred step. */}
      <p className="mt-1.5 border-t border-bezel pt-1.5 text-[9px] leading-snug text-content-faint">
        Übernommene Splits erscheinen im OTIO-Export und im NLE-Projekt (Premiere/FCP). Die interne
        Schnitt-Timeline bleibt hart geschnitten — 2-Spur-Bearbeitung folgt später.
      </p>
    </div>
  );
}

