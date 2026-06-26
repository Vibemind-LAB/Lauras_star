import { type ReactElement } from "react";

/**
 * Shown in Rough Cut / Feinschnitt when the selected video has no transcript words. Surfaces
 * *why* (the analysis note — e.g. ASR failed or was skipped) and offers a one-tap re-run, so a
 * silently-empty transcript (most often an ASR memory failure on a busy machine) is both
 * explained and recoverable instead of just missing.
 */
export function TranscriptStatusBanner({
  note,
  busy,
  onGenerate,
}: {
  note: string | null;
  busy: boolean;
  onGenerate: () => void;
}): ReactElement {
  return (
    <div className="flex items-center gap-3 border-t border-bezel bg-status-warn/10 px-3 py-2 text-xs text-content-muted">
      <span className="flex-1 leading-snug">
        {note ?? "Für dieses Video liegt noch kein Transkript vor."}
      </span>
      <button
        type="button"
        onClick={onGenerate}
        disabled={busy}
        className="shrink-0 rounded bg-accent px-3 py-1 text-xs font-medium text-white hover:bg-accent-glow disabled:opacity-40"
      >
        {busy ? "Transkript läuft…" : "Transkript erzeugen"}
      </button>
    </div>
  );
}
