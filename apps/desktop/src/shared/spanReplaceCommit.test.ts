import { describe, expect, it } from "vitest";
import { buildVoiceoverCommit } from "./spanReplaceCommit";

const words = [
  { id: "w1", seq_in_frame: 10, seq_out_frame_exclusive: 20, text: "alt" },
  { id: "w2", seq_in_frame: 20, seq_out_frame_exclusive: 40, text: "text" },
];

describe("buildVoiceoverCommit", () => {
  it("maps span to a replace_original VO request, ducking 0", () => {
    const out = buildVoiceoverCommit({
      startWordId: "w1", endWordId: "w2", newText: "neuer text", voiceId: "Hedda", words,
    });
    expect(out).toEqual({
      seqIn: 10, seqOut: 40, text: "neuer text",
      voiceId: "Hedda", mixMode: "replace_original", duckingPercent: 0,
    });
  });

  it("returns null when text is blank", () => {
    expect(buildVoiceoverCommit({
      startWordId: "w1", endWordId: "w2", newText: "   ", voiceId: null, words,
    })).toBeNull();
  });

  it("returns null when the span words are missing", () => {
    expect(buildVoiceoverCommit({
      startWordId: "wX", endWordId: "w2", newText: "x", voiceId: null, words,
    })).toBeNull();
  });

  it("omits voiceId when null (backend uses default voice)", () => {
    const out = buildVoiceoverCommit({
      startWordId: "w1", endWordId: "w2", newText: "x", voiceId: null, words,
    });
    expect(out).not.toBeNull();
    expect(out && "voiceId" in out).toBe(false);
  });
});
