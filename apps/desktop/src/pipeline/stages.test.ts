import { describe, expect, it } from "vitest";
import { STAGES, type Stage } from "./stages";
describe("pipeline stages", () => {
  it("defines the six stages in order", () => {
    expect(STAGES.map((s) => s.id)).toEqual(["download","import","roughcut","finecut","assemble","export"]);
  });
  it("every stage has a label", () => { expect(STAGES.every((s) => s.label.length > 0)).toBe(true); });
  it("Stage type accepts a known id", () => { const s: Stage = "import"; expect(STAGES.some((x) => x.id === s)).toBe(true); });
});
