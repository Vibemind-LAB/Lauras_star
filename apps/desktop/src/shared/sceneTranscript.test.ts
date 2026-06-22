import { describe, expect, it } from "vitest";

import { groupCutWordsByScene } from "./sceneTranscript";
import { type CutWord } from "./transcriptProjection";
import { type Scene } from "../api";

function scene(id: string, inF: number, outF: number, order: number): Scene {
  return {
    id, project_id: "p", source_timeline_id: "t", name: id,
    order_index: order, seq_in_frame: inF, seq_out_frame_exclusive: outF,
  };
}
function word(id: string, seqStart: number): CutWord {
  return { id, text: id, srcFrame: seqStart, srcEndFrame: seqStart + 1, seqStart, seqEnd: seqStart + 1 };
}

describe("groupCutWordsByScene", () => {
  it("assigns words to the scene whose end-exclusive range contains seqStart", () => {
    const scenes = [scene("s1", 0, 100, 0), scene("s2", 100, 200, 1)];
    const words = [word("a", 0), word("b", 99), word("c", 100), word("d", 150)];
    const groups = groupCutWordsByScene(words, scenes);
    expect(groups.map((g) => g.scene.id)).toEqual(["s1", "s2"]);
    expect(groups[0].words.map((w) => w.id)).toEqual(["a", "b"]); // 99 < 100 stays in s1
    expect(groups[1].words.map((w) => w.id)).toEqual(["c", "d"]); // 100 is s2's in-frame
  });

  it("attaches words past the last scene to the last scene", () => {
    const scenes = [scene("s1", 0, 100, 0)];
    const groups = groupCutWordsByScene([word("a", 250)], scenes);
    expect(groups[0].words.map((w) => w.id)).toEqual(["a"]);
  });

  it("returns one empty group per scene when there are no words", () => {
    const scenes = [scene("s1", 0, 100, 0), scene("s2", 100, 200, 1)];
    const groups = groupCutWordsByScene([], scenes);
    expect(groups.map((g) => g.words.length)).toEqual([0, 0]);
  });

  it("returns no groups when there are no scenes", () => {
    expect(groupCutWordsByScene([word("a", 0)], [])).toEqual([]);
  });
});
