import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatComposer } from "./ChatComposer";

describe("ChatComposer", () => {
  it(`sends trimmed text on "Send" click and clears the textarea`, () => {
    const onSend = vi.fn();
    render(<ChatComposer disabled={false} onSend={onSend} />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "  Make me a short  " } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    expect(onSend).toHaveBeenCalledWith("Make me a short");
    expect(textarea.value).toBe("");
  });

  it("Enter (no shift) sends and clears", () => {
    const onSend = vi.fn();
    render(<ChatComposer disabled={false} onSend={onSend} />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "Hallo Laura" } });
    fireEvent.keyDown(textarea, { key: "Enter" });

    expect(onSend).toHaveBeenCalledWith("Hallo Laura");
    expect(textarea.value).toBe("");
  });

  it("Shift+Enter never fires onSend (newline instead)", () => {
    const onSend = vi.fn();
    render(<ChatComposer disabled={false} onSend={onSend} />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "Zeile eins" } });
    fireEvent.keyDown(textarea, { key: "Enter", shiftKey: true });

    expect(onSend).not.toHaveBeenCalled();
  });

  it("empty or whitespace-only text never fires onSend — button disabled, Enter no-ops", () => {
    const onSend = vi.fn();
    render(<ChatComposer disabled={false} onSend={onSend} />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    const button = screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;

    expect(button.disabled).toBe(true);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();

    fireEvent.change(textarea, { target: { value: "   " } });
    expect(button.disabled).toBe(true);
    fireEvent.keyDown(textarea, { key: "Enter" });
    expect(onSend).not.toHaveBeenCalled();
  });

  it("disabled blocks the button and Enter, even with text typed", () => {
    const onSend = vi.fn();
    render(<ChatComposer disabled={true} onSend={onSend} />);
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;
    const button = screen.getByRole("button", { name: "Send" }) as HTMLButtonElement;

    expect(textarea.disabled).toBe(true);
    expect(button.disabled).toBe(true);

    fireEvent.change(textarea, { target: { value: "Send anyway?" } });
    fireEvent.keyDown(textarea, { key: "Enter" });
    fireEvent.click(button);

    expect(onSend).not.toHaveBeenCalled();
  });
});
