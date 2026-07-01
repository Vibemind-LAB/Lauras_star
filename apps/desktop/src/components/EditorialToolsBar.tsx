import { useState, type ReactElement } from "react";

import type { BoundaryIdentity, LauraClient, VoiceoverVoice } from "../api";
import { useConsent } from "../hooks/useConsent";
import { ReenactPanel } from "./ReenactPanel";

export interface EditorialToolsBarProps {
  voices?: VoiceoverVoice[];
  voiceId?: string | null;
  onVoiceChange?(voiceId: string | null): void;
  pendingEdge?: BoundaryIdentity | null;
  onSmooth?(): void;
  /** Optional legacy callback — kept for backward compat; the panel is now embedded. */
  onReenact?(): void;
  syntheticEffects?: string[];
  busy?: boolean;
  /** Required for the consent inspector and embedded ReenactPanel. */
  client?: LauraClient | null;
  projectId?: string | null;
  /** Required for embedded ReenactPanel. */
  timelineId?: string | null;
  /** Assets forwarded to the embedded ReenactPanel. */
  assets?: { id: string; display_name: string }[];
  /** Called after a successful reenact so the parent can reload. */
  onChange?(): void;
  /** Current live playhead position in sequence frames. */
  currentSeqFrame?: number;
  /** Numerator of the project sequence frame rate. */
  rateNum?: number;
  /** Denominator of the project sequence frame rate. */
  rateDen?: number;
  /** Undo/redo history state and handlers. */
  canUndo?: boolean;
  canRedo?: boolean;
  undoLabel?: string | null;
  redoLabel?: string | null;
  onUndo?(): void;
  onRedo?(): void;
  /** Batch-smooth: auto-apply the transition heuristic to every boundary. */
  onAutoTransitions?(): void;
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
  voices = [],
  voiceId = null,
  onVoiceChange,
  pendingEdge = null,
  onSmooth,
  onReenact,
  syntheticEffects = [],
  busy = false,
  client = null,
  projectId = null,
  timelineId = null,
  assets = [],
  onChange,
  currentSeqFrame = 0,
  rateNum = 30,
  rateDen = 1,
  canUndo = false,
  canRedo = false,
  undoLabel = null,
  redoLabel = null,
  onUndo,
  onRedo,
  onAutoTransitions,
}: EditorialToolsBarProps): ReactElement {
  const { active, create, revoke, error: consentError } = useConsent(client, projectId);
  const [subject, setSubject] = useState<string>("");
  const [reenactOpen, setReenactOpen] = useState(false);

  const effectsLabel = syntheticEffects.length
    ? syntheticEffects.join(", ")
    : "keine";
  const subjectsLabel = active.length
    ? active.map((c) => c.subject_label).join(", ")
    : "—";

  return (
    <div className="flex flex-col gap-1 border-y border-bezel/80 px-2 py-1.5">
      <div className="flex flex-wrap items-center gap-2 text-xs">
        <button
          type="button"
          onClick={onUndo ?? (() => undefined)}
          disabled={!canUndo}
          title={undoLabel ? `Rückgängig: ${undoLabel}` : "Rückgängig"}
          className="rounded bg-accent px-3 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40"
        >
          ↶ Rückgängig
        </button>
        <button
          type="button"
          onClick={onRedo ?? (() => undefined)}
          disabled={!canRedo}
          title={redoLabel ? `Wiederholen: ${redoLabel}` : "Wiederholen"}
          className="rounded bg-accent px-3 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40"
        >
          ↷ Wiederholen
        </button>
        <span className="mx-0.5 h-5 w-px self-center bg-bezel/60" aria-hidden="true" />
        <label className="flex items-center gap-1 text-content-muted">
          <span className="text-[10px] uppercase tracking-wide">Stimme</span>
          <select
            aria-label="Stimme"
            value={voiceId ?? ""}
            disabled={busy}
            onChange={(e) => onVoiceChange?.(e.target.value === "" ? null : e.target.value)}
            className="rounded border border-bezel bg-surface-1 px-1 py-1 text-[11px] text-content-strong disabled:opacity-40"
          >
            <option value="">Auto</option>
            {voices.map((v) => (
              <option key={v.name} value={v.name}>{`${v.name} (${v.culture})`}</option>
            ))}
          </select>
        </label>
        <span className="mx-0.5 h-5 w-px self-center bg-bezel/60" aria-hidden="true" />
        <button
          type="button"
          onClick={onSmooth ?? (() => undefined)}
          disabled={busy || pendingEdge === null}
          title="Markierte Schnittkante mit einer kurzen Blende glätten"
          className="rounded bg-accent px-3 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40"
        >
          Übergang glätten
        </button>
        <button
          type="button"
          onClick={onAutoTransitions ?? (() => undefined)}
          disabled={busy}
          title="Alle passenden Schnittkanten automatisch mit kurzen Blenden glätten"
          className="rounded border border-bezel bg-surface-1 px-3 py-1 text-[11px] text-content-strong hover:border-accent disabled:opacity-40"
        >
          Alle Übergänge glätten
        </button>
        <button
          type="button"
          onClick={() => {
            setReenactOpen((v) => !v);
            onReenact?.();
          }}
          disabled={busy}
          aria-expanded={reenactOpen}
          className="rounded border border-bezel bg-surface-1 px-3 py-1 text-[11px] text-content-strong hover:border-accent disabled:opacity-40"
        >
          Reenact
        </button>
      </div>

      {/* Embedded ReenactPanel — shown when the Reenact toggle is open. */}
      {reenactOpen && client != null && (
        <ReenactPanel
          client={client}
          projectId={projectId}
          timelineId={timelineId}
          assets={assets}
          onChange={onChange ?? (() => undefined)}
          currentSeqFrame={currentSeqFrame}
          rateNum={rateNum}
          rateDen={rateDen}
        />
      )}

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
        {consentError && (
          <p className="mt-1 text-[11px] text-status-err" role="alert">
            {consentError}
          </p>
        )}
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
