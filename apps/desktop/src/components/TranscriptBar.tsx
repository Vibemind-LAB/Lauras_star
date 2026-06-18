import { type ReactElement, useEffect, useState } from "react";

import { type LauraClient, type Segment } from "../api";
import { type CutWord } from "../shared/transcriptProjection";

function SegmentText({
  segment,
  currentFrame,
  onSeek,
}: {
  segment: Segment;
  currentFrame: number;
  onSeek: (frame: number) => void;
}): ReactElement {
  // After a text edit the word-level timings go stale, so the old word chips no longer match
  // the corrected text — fall back to the plain (single seek button) rendering until a realign
  // rebuilds the words. Word-less segments use the same path.
  if (segment.words.length === 0 || segment.alignment_status === "stale") {
    const active = currentFrame >= segment.start_frame && currentFrame < segment.end_frame;
    return (
      <button
        type="button"
        onClick={() => onSeek(segment.start_frame)}
        className={`text-left hover:underline ${active ? "text-sky-300" : ""}`}
      >
        {segment.text}
      </button>
    );
  }
  return (
    <span className="leading-relaxed">
      {segment.words.map((w) => {
        const active = currentFrame >= w.start_frame && currentFrame < w.end_frame;
        return (
          <span key={w.id}>
            <button
              type="button"
              onClick={() => onSeek(w.start_frame)}
              className={`rounded px-0.5 hover:bg-edge ${active ? "bg-sky-600/50 text-white" : ""}`}
            >
              {w.text}
            </button>{" "}
          </span>
        );
      })}
    </span>
  );
}

/**
 * The full-width transcript bar beneath the timeline (Clipchamp-captions style). Words are
 * clickable to seek; segments derived from a rough cut can be appended; captions export and
 * (P7) re-transcription live in its header.
 *
 * When `cutWords` is provided (Feinschnitt cut-mode), only those projected words are rendered
 * (trimmed-out words are suppressed) and the existing segment-rendering path is bypassed.
 * All other callers that pass `segments` without `cutWords` are unaffected.
 */
export function TranscriptBar({
  client,
  assetId,
  assetName,
  segments,
  cutWords,
  note,
  currentFrame,
  onSeek,
  canAppend,
  onAppendSegment,
  onDeleteWords,
  onEditSegment,
}: {
  client: LauraClient | null;
  assetId: string | null;
  assetName: string | null;
  segments: Segment[];
  /**
   * When provided, render the cut-projected word list instead of the raw segments.
   * Words are already sorted by seqStart (cut order). Highlighting and seek use srcFrame.
   */
  cutWords?: CutWord[];
  note: string | null;
  currentFrame: number;
  onSeek: (frame: number) => void;
  canAppend: boolean;
  onAppendSegment: (seg: Segment) => void;
  /** When provided, each segment with words gets a ripple-delete affordance that passes
   *  the segment's first and last word ids. Additive — existing callers are unaffected. */
  onDeleteWords?: (wordStartId: string, wordEndId: string) => void;
  /** When provided, each segment gets an edit (✎) affordance that opens an inline editor for
   *  its text; saving calls this with the segment id + new text. Additive — existing callers
   *  unaffected. Only active in raw-segment mode (never in the cut-mode word list). */
  onEditSegment?: (segmentId: string, text: string) => Promise<void> | void;
}): ReactElement {
  const [error, setError] = useState<string | null>(null);
  // Inline transcript text editing (raw-segment mode only): one segment editable at a time.
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [saving, setSaving] = useState(false);

  // Never let an open editor leak across assets (the segment ids belong to the old asset).
  useEffect(() => {
    setEditingId(null);
  }, [assetId]);

  async function saveEdit(segmentId: string): Promise<void> {
    if (!onEditSegment) return;
    setSaving(true);
    try {
      await onEditSegment(segmentId, draft.trim());
      setEditingId(null);
    } catch (e) {
      setError(String(e)); // stay in edit mode so the user can retry
    } finally {
      setSaving(false);
    }
  }

  function cancelEdit(): void {
    setEditingId(null);
  }

  async function exportCaptions(fmt: "srt" | "vtt"): Promise<void> {
    if (!client || !assetId) return;
    try {
      const text = await client.getCaptions(assetId, fmt);
      await window.laura.saveTextFile(`${assetName ?? "transcript"}.${fmt}`, text);
    } catch (e) {
      setError(String(e));
    }
  }

  // Cut-mode: render only the projected surviving words, in cut order.
  if (cutWords !== undefined) {
    const hasWords = cutWords.length > 0;
    return (
      <div className="flex h-32 flex-col border-t border-edge bg-panel">
        <div className="flex items-center justify-between px-5 py-1.5">
          <span className="text-xs uppercase tracking-wide text-slate-500">Transkript (Schnitt)</span>
          <span className="flex items-center gap-1">
            {error && <span className="mr-2 text-xs text-red-400">{error}</span>}
          </span>
        </div>
        {!hasWords ? (
          <div className="flex flex-1 items-center px-5 text-xs text-slate-600">
            {assetId ? "Keine Wörter im Schnitt." : "Wähle ein Medium."}
          </div>
        ) : (
          <div className="flex-1 overflow-auto px-5 pb-2 text-sm leading-relaxed text-slate-200">
            {cutWords.map((w) => {
              const active = currentFrame >= w.srcFrame && currentFrame < w.srcEndFrame;
              return (
                <span key={w.id}>
                  <button
                    type="button"
                    onClick={() => onSeek(w.srcFrame)}
                    className={`rounded px-0.5 hover:bg-edge ${active ? "bg-sky-600/50 text-white" : ""}`}
                  >
                    {w.text}
                  </button>{" "}
                </span>
              );
            })}
          </div>
        )}
      </div>
    );
  }

  // Default (raw-segment) mode — used by App.tsx / ImportView / RoughCut etc.
  return (
    <div className="flex h-32 flex-col border-t border-edge bg-panel">
      <div className="flex items-center justify-between px-5 py-1.5">
        <span className="text-xs uppercase tracking-wide text-slate-500">Transkript</span>
        <span className="flex items-center gap-1">
          {error && <span className="mr-2 text-xs text-red-400">{error}</span>}
          {segments.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => void exportCaptions("srt")}
                className="rounded bg-ink px-2 py-0.5 text-xs text-slate-300 hover:bg-edge"
              >
                SRT
              </button>
              <button
                type="button"
                onClick={() => void exportCaptions("vtt")}
                className="rounded bg-ink px-2 py-0.5 text-xs text-slate-300 hover:bg-edge"
              >
                VTT
              </button>
            </>
          )}
        </span>
      </div>
      {segments.length === 0 ? (
        <div className="flex flex-1 items-center px-5 text-xs text-slate-600">
          {note ?? (assetId ? "noch kein Transkript" : "Wähle ein Medium.")}
        </div>
      ) : (
        <div className="flex-1 space-y-1 overflow-auto px-5 pb-2 text-sm text-slate-200">
          {segments.map((seg) => {
            const active =
              currentFrame >= seg.start_frame && currentFrame < seg.end_frame;
            return (
              <span
                key={seg.id}
                className={`mr-1 inline rounded px-1 ${active ? "bg-sky-900/40" : ""}`}
              >
                {seg.speaker_label && (
                  <span className="mr-1 rounded bg-ink px-1.5 py-0.5 text-xs text-sky-300">
                    {seg.speaker_label}
                  </span>
                )}
                {editingId === seg.id ? (
                  <span className="inline-flex items-center gap-1 align-middle">
                    <textarea
                      aria-label="Segmenttext bearbeiten"
                      autoFocus
                      rows={1}
                      value={draft}
                      disabled={saving}
                      onChange={(e) => setDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter" && !e.shiftKey) {
                          e.preventDefault();
                          void saveEdit(seg.id);
                        } else if (e.key === "Escape") {
                          e.preventDefault();
                          cancelEdit();
                        }
                      }}
                      className="w-64 max-w-full rounded bg-ink px-1 align-middle text-sm text-slate-100"
                    />
                    <button
                      type="button"
                      onClick={() => void saveEdit(seg.id)}
                      disabled={saving}
                      title="Speichern"
                      className="rounded bg-ink px-1 text-xs text-emerald-300 hover:bg-edge disabled:opacity-50"
                    >
                      ✓
                    </button>
                    <button
                      type="button"
                      onClick={cancelEdit}
                      disabled={saving}
                      title="Abbrechen"
                      className="rounded bg-ink px-1 text-xs text-slate-400 hover:bg-edge disabled:opacity-50"
                    >
                      ✕
                    </button>
                  </span>
                ) : (
                  <>
                    <SegmentText segment={seg} currentFrame={currentFrame} onSeek={onSeek} />
                    {canAppend && seg.words.length > 0 && (
                      <button
                        type="button"
                        onClick={() => onAppendSegment(seg)}
                        title="an Rough Cut anhängen"
                        className="ml-0.5 rounded bg-ink px-1 text-xs text-emerald-300 hover:bg-edge"
                      >
                        →
                      </button>
                    )}
                    {onDeleteWords && seg.words.length > 0 && (
                      <button
                        type="button"
                        onClick={() =>
                          onDeleteWords(seg.words[0].id, seg.words[seg.words.length - 1].id)
                        }
                        title="Segment ripple-löschen"
                        className="ml-0.5 rounded bg-ink px-1 text-xs text-red-400 hover:bg-red-600/40"
                      >
                        ✂
                      </button>
                    )}
                    {onEditSegment && (
                      <button
                        type="button"
                        onClick={() => {
                          setError(null);
                          setDraft(seg.text);
                          setEditingId(seg.id);
                        }}
                        title="Transkript bearbeiten"
                        className="ml-0.5 rounded bg-ink px-1 text-xs text-slate-300 hover:bg-edge"
                      >
                        ✎
                      </button>
                    )}
                  </>
                )}{" "}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
