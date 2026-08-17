import { type ReactElement, useEffect, useState } from "react";

import { type LauraClient } from "../api";
import { log } from "../shared/log";
import { framesToTimecode } from "../shared/timecode";

export interface OverlayAsset {
  id: string;
  display_name: string;
}

export interface OverlayControlsProps {
  client: LauraClient;
  timelineId: string | null;
  assets: OverlayAsset[];
  /** Called after a successful setOverlay so the parent can reload. */
  onChange: () => void;
  /** Current live playhead position in sequence frames, forwarded from SequencePlayer. */
  currentSeqFrame: number;
  /** Numerator of the project sequence frame rate (e.g. 30000 for 29.97). */
  rateNum: number;
  /** Denominator of the project sequence frame rate (e.g. 1001 for 29.97). */
  rateDen: number;
}

/**
 * Compact panel for inserting a Replace-Overlay clip onto a timeline.
 *
 * Renders:
 *   - an asset picker (`<select>` over the passed `assets` list)
 *   - number inputs for `seqIn` and `seqOut` (sequence frames, end-exclusive)
 *   - a submit button → `client.setOverlay(timelineId, { assetId, seqIn, seqOut })`
 *
 * On success calls `onChange`; on error surfaces a local error string via `log.error`.
 * Busy state disables the form while the request is in flight.
 */
export function OverlayControls({
  client,
  timelineId,
  assets,
  onChange,
  currentSeqFrame,
  rateNum,
  rateDen,
}: OverlayControlsProps): ReactElement {
  const [assetId, setAssetId] = useState<string>(assets[0]?.id ?? "");
  const [seqIn, setSeqIn] = useState<number>(0);
  const [seqOut, setSeqOut] = useState<number>(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // `assets` is populated asynchronously by the parent; once it arrives, default
  // the picker to the first asset so the visible <select> matches state (otherwise
  // assetId stays "" and submit shows a false "Bitte ein Asset auswählen").
  useEffect(() => {
    if (!assetId && assets.length > 0) {
      setAssetId(assets[0].id);
    }
  }, [assets, assetId]);

  async function submit(): Promise<void> {
    if (!timelineId) {
      setError("No timeline selected.");
      return;
    }
    if (!assetId) {
      setError("Pick an asset first.");
      return;
    }
    if (seqOut <= seqIn) {
      setError("seq_out must be greater than seq_in.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await client.setOverlay(timelineId, { assetId, seqIn, seqOut });
      log.info("overlay added to timeline", timelineId, "asset", assetId, seqIn, "–", seqOut);
      onChange();
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e);
      log.error("setOverlay failed:", msg);
      setError(msg);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-2 rounded-md border border-bezel bg-surface-0/60 p-3">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-violet-300">
        Replace-Overlay einsetzen
      </span>
      {error && <div className="text-xs text-status-err">{error}</div>}
      <div className="flex flex-wrap items-center gap-2">
        {/* Asset picker */}
        <select
          value={assetId}
          onChange={(e) => setAssetId(e.target.value)}
          disabled={busy || assets.length === 0}
          aria-label="Choose asset"
          className="min-w-0 flex-1 truncate rounded border border-bezel bg-surface-1 px-2 py-1 text-xs text-content-strong disabled:opacity-50"
        >
          {assets.length === 0 ? (
            <option value="">— no assets —</option>
          ) : (
            assets.map((a) => (
              <option key={a.id} value={a.id}>
                {a.display_name}
              </option>
            ))
          )}
        </select>
        {/* Sequence IN */}
        <div className="flex flex-col gap-0.5">
          <label className="flex items-center gap-1 text-xs text-content-muted">
            <span>seq in</span>
            <input
              type="number"
              min={0}
              step={1}
              value={seqIn}
              onChange={(e) => setSeqIn(Math.trunc(Number(e.target.value)) || 0)}
              disabled={busy}
              aria-label="Sequenz-Einpunkt (Frames)"
              className="w-20 rounded border border-bezel bg-surface-1 px-1.5 py-0.5 text-xs tabular-nums text-content-strong disabled:opacity-50"
            />
            <span className="text-[10px] text-content-faint tabular-nums">
              {framesToTimecode(seqIn, rateNum, rateDen)}
            </span>
          </label>
          <button
            type="button"
            onClick={() => setSeqIn(Math.max(0, Math.trunc(currentSeqFrame)))}
            disabled={busy || !Number.isFinite(currentSeqFrame)}
            className="self-start rounded border border-bezel bg-surface-1 px-1.5 py-0.5 text-[10px] text-content-muted hover:bg-surface-2 hover:text-content-strong disabled:opacity-40"
          >
            In = Playhead
          </button>
        </div>
        {/* Sequence OUT (exclusive) */}
        <div className="flex flex-col gap-0.5">
          <label className="flex items-center gap-1 text-xs text-content-muted">
            <span>seq out</span>
            <input
              type="number"
              min={0}
              step={1}
              value={seqOut}
              onChange={(e) => setSeqOut(Math.trunc(Number(e.target.value)) || 0)}
              disabled={busy}
              aria-label="Sequenz-Auspunkt exklusiv (Frames)"
              className="w-20 rounded border border-bezel bg-surface-1 px-1.5 py-0.5 text-xs tabular-nums text-content-strong disabled:opacity-50"
            />
            <span className="text-[10px] text-content-faint tabular-nums">
              {framesToTimecode(seqOut, rateNum, rateDen)}
            </span>
          </label>
          <button
            type="button"
            onClick={() => setSeqOut(Math.max(0, Math.trunc(currentSeqFrame)))}
            disabled={busy || !Number.isFinite(currentSeqFrame)}
            className="self-start rounded border border-bezel bg-surface-1 px-1.5 py-0.5 text-[10px] text-content-muted hover:bg-surface-2 hover:text-content-strong disabled:opacity-40"
          >
            Out = Playhead
          </button>
        </div>
        {/* Submit */}
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || !timelineId || assets.length === 0}
          className="rounded bg-violet-700 px-3 py-1 text-xs font-medium text-white hover:bg-violet-600 disabled:opacity-40"
        >
          {busy ? "…" : "Einsetzen"}
        </button>
      </div>
    </div>
  );
}


