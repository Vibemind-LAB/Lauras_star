import { useState, type ReactElement } from "react";

import type { BoundaryIdentity, LauraClient, VoiceoverVoice } from "../api";
import { useConsent } from "../hooks/useConsent";

export interface EditorialToolsBarProps {
  voices: VoiceoverVoice[];
  voiceId: string | null;
  onVoiceChange(voiceId: string | null): void;
  pendingEdge: BoundaryIdentity | null;
  onSmooth(): void;
  onReenact(): void;
  syntheticEffects: string[];
  busy?: boolean;
  /** Required for the consent inspector. If omitted, the inspector renders in a disconnected state. */
  client?: LauraClient | null;
  projectId?: string | null;
}

/**
 * Compact strip under the player (spec §10): the ONLY explicit choice is the voice.
 * VO + lipsync happen automatically on a transcript edit (no toggles here). Smooth is
 * offered one-tap only when a same-source jump-cut was auto-marked; Reenact is a manual
 * creative action. The synthetic-content disclosure line is always on (spec §7).
 * The "Das bin ich" consent inspector (spec §11) lets the subject create/revoke consent
 * records without leaving the edit view.
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
  client = null,
  projectId = null,
}: EditorialToolsBarProps): ReactElement {
  const { active, create, revoke } = useConsent(client, projectId);
  const [subject, setSubject] = useState<string>("");

  const effectsLabel = syntheticEffects.length
    ? syntheticEffects.join(", ")
    : "keine";
  const subjectsLabel = active.length
    ? active.map((c) => c.subject_label).join(", ")
    : "—";

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

      {/* Always-on synthetic-content disclosure (spec §7 — no off-switch). */}
      <div className="flex items-center gap-2 text-[11px] text-content-muted">
        <span>
          Enthält synthetische Inhalte: {effectsLabel} · Einwilligung: {subjectsLabel}
        </span>
      </div>

      {/* "Das bin ich" consent inspector — collapsible, in-strip (spec §11). */}
      <details className="text-[11px] text-content-muted">
        <summary className="cursor-pointer select-none">Das bin ich (Einwilligung)</summary>
        <div className="mt-1 flex items-center gap-2">
          <input
            value={subject}
            onChange={(e) => setSubject(e.target.value)}
            placeholder="Name der Person"
            className="rounded border border-bezel bg-surface-2 px-2 py-1 text-xs text-content-strong"
          />
          <button
            type="button"
            disabled={!subject.trim()}
            onClick={() => {
              void create(subject.trim());
              setSubject("");
            }}
            className="rounded bg-surface-2 px-2 py-1 text-xs text-content-strong disabled:opacity-40"
          >
            Bestätigen
          </button>
        </div>
        <ul className="mt-1 space-y-0.5">
          {active.map((c) => (
            <li key={c.id} className="flex items-center justify-between gap-2">
              <span>{c.subject_label}</span>
              <button
                type="button"
                onClick={() => {
                  void revoke(c.id);
                }}
                className="text-content-faint hover:text-content-strong"
              >
                widerrufen
              </button>
            </li>
          ))}
        </ul>
      </details>
    </div>
  );
}
