/**
 * Pure helper: group projected cut-words into the continuous transcript's scene sections.
 *
 * Each word is assigned to the scene whose [seq_in_frame, seq_out_frame_exclusive) contains its
 * seqStart (end-exclusive, invariant #2). Words past the last scene attach to the last scene so
 * nothing is dropped. Scenes keep their input order; every scene yields a group (possibly empty)
 * so the UI can render its label + cut marker even before the first word lands.
 */
import { type Scene } from "../api";
import { type CutWord } from "./transcriptProjection";

export interface SceneGroup {
  scene: Scene;
  words: CutWord[];
}

export function groupCutWordsByScene(words: CutWord[], scenes: Scene[]): SceneGroup[] {
  if (scenes.length === 0) return [];
  const groups: SceneGroup[] = scenes.map((scene) => ({ scene, words: [] }));
  for (const w of words) {
    let idx = groups.findIndex(
      (g) => w.seqStart >= g.scene.seq_in_frame && w.seqStart < g.scene.seq_out_frame_exclusive,
    );
    if (idx === -1) idx = groups.length - 1; // past the last scene -> attach to it
    groups[idx].words.push(w);
  }
  return groups;
}
