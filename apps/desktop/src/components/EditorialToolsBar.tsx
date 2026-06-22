import { type ReactElement } from "react";

import type { BoundaryIdentity, VoiceoverVoice } from "../api";

export interface EditorialToolsBarProps {
  voices: VoiceoverVoice[];
  voiceId: string | null;
  onVoiceChange(voiceId: string | null): void;
  pendingEdge: BoundaryIdentity | null;
  onSmooth(): void;
  onReenact(): void;
  syntheticEffects: string[];
  busy?: boolean;
}

/**
 * Compact strip under the player (spec §10): the ONLY explicit choice is the voice.
 * VO + lipsync happen automatically on a transcript edit (no toggles here). Smooth is
 * offered one-tap only when a same-source jump-cut was auto-marked; Reenact is a manual
 * creative action. The synthetic-content disclosure line is always on (spec §7).
 */
export function EditorialToolsBar({
  voices,
  voiceId,
  onVoiceChange,
  pendingEdge,
  onSmooth,
  onReenact,
  syntheticEffects,
  busy = false,
}: EditorialToolsBarProps): ReactElement {
  return (
    <div className="flex flex-col gap-1 border-y border-bezel/80 px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <label className="flex items-center gap-1 text-content-muted">
          <span className="text-[10px] uppercase tracking-wide">Stimme</span>
          <select
            aria-label="Stimme"
            value={voiceId ?? ""}
            disabled={busy}
            onChange={(e) => onVoiceChange(e.target.value === "" ? null : e.target.value)}
            className="rounded border border-bezel bg-surface-1 px-1 py-1 text-[11px] text-content-strong disabled:opacity-40"
          >
            <option value="">Auto</option>
            {voices.map((v) => (
              <option key={v.name} value={v.name}>{`${v.name} (${v.culture})`}</option>
            ))}
          </select>
        </label>
        <button
          type="button"
          onClick={onSmooth}
          disabled={busy || pendingEdge === null}
          title="Markierte Schnittkante mit einer kurzen Blende glätten"
          className="rounded bg-accent px-3 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40"
        >
          Übergang glätten
        </button>
        <button
          type="button"
          onClick={onReenact}
          disabled={busy}
          className="rounded border border-bezel bg-surface-1 px-3 py-1 text-[11px] text-content-strong hover:border-accent disabled:opacity-40"
        >
          Reenact
        </button>
      </div>
      <div className="text-[10px] text-content-faint">
        {syntheticEffects.length > 0
          ? `Enthält synthetische Inhalte: ${syntheticEffects.join(", ")}`
          : "Enthält synthetische Inhalte"}
      </div>
    </div>
  );
}
