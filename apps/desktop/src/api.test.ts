import { describe, expect, it } from "vitest";

import { type Asset, hasFile } from "./api";

const make = (kinds: string[]): Asset =>
  ({ files: kinds.map((k) => ({ kind: k })) }) as unknown as Asset;

describe("hasFile", () => {
  it("finds a present file kind", () => {
    expect(hasFile(make(["proxy", "waveform"]), "proxy")).toBe(true);
  });

  it("returns false for a missing kind", () => {
    expect(hasFile(make(["poster"]), "proxy")).toBe(false);
  });

  it("returns false when there are no files", () => {
    expect(hasFile(make([]), "proxy")).toBe(false);
  });
});
