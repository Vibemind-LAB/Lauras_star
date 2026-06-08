import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ImportBar, type UrlImportRequest } from "./ImportBar";

afterEach(() => cleanup());

function setup(disabled = false) {
  const onUrls = vi.fn<(req: UrlImportRequest) => void>();
  render(
    <ImportBar
      disabled={disabled}
      onUrls={onUrls}
      onPickFiles={vi.fn()}
      onPickFolder={vi.fn()}
    />,
  );
  const textarea = screen.getByPlaceholderText(/URLs einfügen/i);
  const load = screen.getByRole("button", { name: "Laden" });
  return { onUrls, textarea, load };
}

describe("ImportBar", () => {
  it("splits multiple URLs on newlines and commas, dropping empties", () => {
    const { onUrls, textarea, load } = setup();
    fireEvent.change(textarea, {
      target: { value: " https://a/1 \n\nhttps://b/2 , https://c/3 \n" },
    });
    fireEvent.click(load);
    expect(onUrls).toHaveBeenCalledOnce();
    expect(onUrls.mock.calls[0][0].urls).toEqual([
      "https://a/1",
      "https://b/2",
      "https://c/3",
    ]);
  });

  it("passes the chosen format and cookies; defaults to best/none", () => {
    const { onUrls, textarea, load } = setup();
    fireEvent.change(textarea, { target: { value: "https://youtu.be/x" } });
    fireEvent.click(load);
    expect(onUrls.mock.calls[0][0]).toMatchObject({
      format: "best",
      cookiesFromBrowser: null,
    });

    fireEvent.change(textarea, { target: { value: "https://youtu.be/y" } });
    fireEvent.change(screen.getByLabelText("Qualität"), { target: { value: "audio" } });
    fireEvent.change(screen.getByLabelText("Cookies aus Browser"), {
      target: { value: "firefox" },
    });
    fireEvent.click(load);
    expect(onUrls.mock.calls[1][0]).toMatchObject({
      format: "audio",
      cookiesFromBrowser: "firefox",
    });
  });

  it("does not submit when the textarea is empty (whitespace only)", () => {
    const { onUrls, textarea, load } = setup();
    fireEvent.change(textarea, { target: { value: "   \n , \n" } });
    fireEvent.click(load);
    expect(onUrls).not.toHaveBeenCalled();
  });

  it("clears the textarea after a successful submit", () => {
    const { textarea, load } = setup();
    fireEvent.change(textarea, { target: { value: "https://a/1" } });
    fireEvent.click(load);
    expect((textarea as HTMLTextAreaElement).value).toBe("");
  });
});
