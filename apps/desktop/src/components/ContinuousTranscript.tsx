import { type ReactElement, useState } from "react";

import { type Scene } from "../api";
import { type CutWord } from "../shared/transcriptProjection";
import { groupCutWordsByScene } from "../shared/sceneTranscript";

interface Selection {
  startWordId: string;
  endWordId: string;
}

/**
 * The continuous rough-cut transcript as the editing surface (spec §3, §4.1).
 *
 * Words are grouped into scene sections (label + cut marker at each boundary). Three gestures,
 * no tool picker: click a word -> seek; drag / shift over words -> selection (ordered by seqStart)
 * which the parent ripple-deletes; click the caret between two words -> cut at the right word's
 * sequence frame (a new scene starts there). Pure presentation — all effects flow through props.
 */
export function ContinuousTranscript({
  words,
  scenes,
  selection,
  onSelectionChange,
  onDeleteSelection,
  onCutAt,
  onSeek,
}: {
  words: CutWord[];
  scenes: Scene[];
  selection: Selection | null;
  onSelectionChange: (sel: Selection | null) => void;
  onDeleteSelection: (startWordId: string, endWordId: string) => void;
  onCutAt: (seqFrame: number) => void;
  onSeek: (seqFrame: number) => void;
}): ReactElement {
  const groups = groupCutWordsByScene(words, scenes);
  // words are already sorted by seqStart from projectCutWords
  const [anchor, setAnchor] = useState<CutWord | null>(null);

  function selectTo(from: CutWord, to: CutWord): void {
    const [a, b] = from.seqStart <= to.seqStart ? [from, to] : [to, from];
    onSelectionChange({ startWordId: a.id, endWordId: b.id });
  }

  function inSelection(w: CutWord): boolean {
    if (!selection) return false;
    const s = words.find((x) => x.id === selection.startWordId);
    const e = words.find((x) => x.id === selection.endWordId);
    if (!s || !e) return false;
    return w.seqStart >= s.seqStart && w.seqStart <= e.seqStart;
  }

  return (
    <div
      className="flex flex-col gap-2 overflow-auto p-2 text-sm"
      data-testid="continuous-transcript"
    >
      {selection && (
        <div className="flex items-center gap-2 text-xs text-content-muted">
          <button
            type="button"
            className="rounded bg-status-err/20 px-2 py-0.5 text-status-err hover:bg-status-err/30"
            onClick={() => onDeleteSelection(selection.startWordId, selection.endWordId)}
          >
            Auswahl löschen
          </button>
        </div>
      )}
      {groups.map((g) => (
        <section key={g.scene.id} data-scene-id={g.scene.id}>
          <div className="flex items-center gap-1 text-[11px] uppercase tracking-wide text-content-faint">
            <span aria-hidden>✂</span>
            <span>{g.scene.name}</span>
          </div>
          <p className="leading-7">
            {g.words.map((w) => (
              <span key={w.id} className="whitespace-nowrap">
                <button
                  type="button"
                  aria-label={`Schnitt vor ${w.text}`}
                  data-testid={`caret-${w.id}`}
                  className="mx-0.5 inline-block w-1 cursor-col-resize align-middle text-content-faint hover:text-sky-400"
                  onClick={() => onCutAt(w.seqStart)}
                >
                  |
                </button>
                <span
                  role="button"
                  tabIndex={0}
                  className={`cursor-text rounded px-0.5 ${
                    inSelection(w) ? "bg-sky-700 text-white" : "hover:bg-surface-2"
                  }`}
                  onClick={() => onSeek(w.seqStart)}
                  onMouseDown={() => {
                    setAnchor(w);
                  }}
                  onMouseEnter={() => {
                    if (anchor) selectTo(anchor, w);
                  }}
                  onMouseUp={() => {
                    if (anchor) selectTo(anchor, w);
                    setAnchor(null);
                  }}
                >
                  {w.text}
                </span>
              </span>
            ))}
          </p>
        </section>
      ))}
    </div>
  );
}
