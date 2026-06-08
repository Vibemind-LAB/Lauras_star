import { type ReactElement, useState } from "react";

import { type LauraClient, type Segment } from "../api";

function SegmentText({
  segment,
  currentFrame,
  onSeek,
}: {
  segment: Segment;
  currentFrame: number;
  onSeek: (frame: number) => void;
}): ReactElement {
  if (segment.words.length === 0) {
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
 */
export function TranscriptBar({
  client,
  assetId,
  assetName,
  segments,
  note,
  currentFrame,
  onSeek,
  canAppend,
  onAppendSegment,
  onDeleteWords,
}: {
  client: LauraClient | null;
  assetId: string | null;
  assetName: string | null;
  segments: Segment[];
  note: string | null;
  currentFrame: number;
  onSeek: (frame: number) => void;
  canAppend: boolean;
  onAppendSegment: (seg: Segment) => void;
  /** When provided, each segment with words gets a ripple-delete affordance that passes
   *  the segment's first and last word ids. Additive — existing callers are unaffected. */
  onDeleteWords?: (wordStartId: string, wordEndId: string) => void;
}): ReactElement {
  const [error, setError] = useState<string | null>(null);

  async function exportCaptions(fmt: "srt" | "vtt"): Promise<void> {
    if (!client || !assetId) return;
    try {
      const text = await client.getCaptions(assetId, fmt);
      await window.laura.saveTextFile(`${assetName ?? "transcript"}.${fmt}`, text);
    } catch (e) {
      setError(String(e));
    }
  }

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
                )}{" "}
              </span>
            );
          })}
        </div>
      )}
    </div>
  );
}
