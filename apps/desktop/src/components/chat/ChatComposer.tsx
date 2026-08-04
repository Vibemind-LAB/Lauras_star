import { type KeyboardEvent, type ReactElement, useState } from "react";

export interface ChatComposerProps {
  disabled: boolean;
  onSend: (text: string) => void;
}

/**
 * The chat input row: a textarea plus „Senden". Enter sends (trimmed, then clears); Shift+Enter
 * inserts a newline — the keydown handler only intercepts the plain-Enter case, so the browser's
 * default newline insertion runs untouched on Shift+Enter. Whitespace-only text never reaches
 * `onSend`, whether via the button (disabled) or Enter (no-op).
 */
export function ChatComposer({ disabled, onSend }: ChatComposerProps): ReactElement {
  const [text, setText] = useState("");

  const send = (): void => {
    const trimmed = text.trim();
    if (trimmed === "" || disabled) return;
    onSend(trimmed);
    setText("");
  };

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  };

  return (
    <div className="flex gap-1 border-t border-bezel p-1.5">
      <textarea
        aria-label="Nachricht"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Schreib Laura, was du willst …"
        disabled={disabled}
        rows={2}
        className="min-w-0 flex-1 resize-none rounded border border-bezel bg-surface-1 px-1.5 py-1 text-[11px] text-content-strong placeholder:text-content-faint disabled:opacity-40"
      />
      <button
        type="button"
        onClick={send}
        disabled={disabled || text.trim() === ""}
        className="shrink-0 self-end rounded bg-accent px-2 py-1 text-[11px] font-medium text-accent-ink hover:bg-accent-glow disabled:opacity-40"
      >
        Senden
      </button>
    </div>
  );
}
