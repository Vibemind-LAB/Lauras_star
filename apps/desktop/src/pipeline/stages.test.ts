import { describe, expect, it } from "vitest";
import { STAGES, type Stage } from "./stages";
describe("pipeline stages", () => {
  it("defines the seven stages in order, chat first", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["chat","media","roughcut","finecut","assemble","shorts","export"]);
  });
  it("every stage has a label", () => { expect(STAGES.every((s) => s.label.length > 0)).toBe(true); });
  it("Stage type accepts a known id", () => { const s: Stage = "media"; expect(STAGES.some((x) => x.id === s)).toBe(true); });
});
