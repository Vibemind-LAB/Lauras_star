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
 * Words are grouped into scene sections (label + cut marker at each boundary). Four gestures,
 * no tool picker: click a word -> seek; drag / shift over words -> selection (ordered by seqStart)
 * which the parent ripple-deletes; click the caret between two words -> cut at the right word's
 * sequence frame (a new scene starts there); "Text ersetzen" on an active selection -> inline
 * input prefilled with selection text -> commit on Enter calls onReplaceText. Pure presentation —
 * all effects flow through props.
 */
export function ContinuousTranscript({
  words,
  scenes,
  selection,
  onSelectionChange,
  onDeleteSelection,
  onCutAt,
  onSeek,
  onReplaceText,
}: {
  words: CutWord[];
  scenes: Scene[];
  selection: Selection | null;
  onSelectionChange: (sel: Selection | null) => void;
  onDeleteSelection: (startWordId: string, endWordId: string) => void;
  onCutAt: (seqFrame: number) => void;
  onSeek: (seqFrame: number) => void;
  onReplaceText?: (startWordId: string, endWordId: string, newText: string) => void;
}): ReactElement {
  const groups = groupCutWordsByScene(words, scenes);
  // words are already sorted by seqStart from projectCutWords
  const [anchor, setAnchor] = useState<CutWord | null>(null);
  // null = no inline editor open; string = current draft text in the editor
  const [replaceText, setReplaceText] = useState<string | null>(null);

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

  /** Collect the display text of all words currently in the selection (joined by space). */
  function selectionText(): string {
    if (!selection) return "";
    const s = words.find((x) => x.id === selection.startWordId);
    const e = words.find((x) => x.id === selection.endWordId);
    if (!s || !e) return "";
    return words
      .filter((w) => w.seqStart >= s.seqStart && w.seqStart <= e.seqStart)
      .map((w) => w.text)
      .join(" ");
  }

  function openReplaceEditor(): void {
    setReplaceText(selectionText());
  }

  function commitReplace(): void {
    if (!selection || replaceText === null) return;
    const trimmed = replaceText.trim();
    if (trimmed !== "" && onReplaceText) {
      onReplaceText(selection.startWordId, selection.endWordId, trimmed);
    }
    setReplaceText(null);
    onSelectionChange(null);
  }

  function cancelReplace(): void {
    setReplaceText(null);
  }

  return (
    <div
      className="flex h-full flex-col gap-2 overflow-auto p-3 text-sm"
      data-testid="continuous-transcript"
    >
      {selection && replaceText === null && (
        <div className="flex items-center gap-2 text-xs text-content-muted">
          <button
            type="button"
            className="rounded bg-status-err/20 px-2 py-0.5 text-status-err hover:bg-status-err/30"
            onClick={() => onDeleteSelection(selection.startWordId, selection.endWordId)}
          >
            Delete selection
          </button>
          {onReplaceText && (
            <button
              type="button"
              className="rounded bg-accent/20 px-2 py-0.5 text-accent hover:bg-accent/30"
              onClick={openReplaceEditor}
            >
              Replace text
            </button>
          )}
        </div>
      )}
      {selection && replaceText !== null && (
        <div className="flex items-center gap-2 text-xs">
          <input
            type="text"
            aria-label="New text"
            className="flex-1 rounded border border-accent bg-surface-1 px-2 py-0.5 text-content-base focus:outline-none focus:ring-1 focus:ring-accent"
            value={replaceText}
            onChange={(e) => setReplaceText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") commitReplace();
              if (e.key === "Escape") cancelReplace();
            }}
            autoFocus
          />
          <button
            type="button"
            className="rounded bg-accent px-2 py-0.5 text-accent-ink hover:opacity-90"
            onClick={commitReplace}
          >
            OK
          </button>
          <button
            type="button"
            className="rounded bg-surface-2 px-2 py-0.5 text-content-muted hover:bg-surface-3"
            onClick={cancelReplace}
          >
            Cancel
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
                  aria-label={`Cut before ${w.text}`}
                  data-testid={`caret-${w.id}`}
                  title="Cut here"
                  className="mx-0.5 inline-block h-3.5 w-0.5 cursor-col-resize rounded-sm bg-transparent align-middle hover:bg-accent"
                  onClick={() => onCutAt(w.seqStart)}
                />
                <span
                  role="button"
                  tabIndex={0}
                  className={`cursor-text rounded px-0.5 ${
                    inSelection(w) ? "bg-accent text-accent-ink" : "hover:bg-surface-2"
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
