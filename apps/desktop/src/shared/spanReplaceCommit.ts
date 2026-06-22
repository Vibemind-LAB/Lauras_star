export interface ProjectedWord {
  id: string;
  seq_in_frame: number;
  seq_out_frame_exclusive: number;
  text: string;
}

export interface VoiceoverCommit {
  seqIn: number;
  seqOut: number;
  text: string;
  voiceId?: string;
  mixMode: "replace_original";
  duckingPercent: 0;
}

/**
 * Map a transcript word-span text edit to a VO request: original audio out
 * (mix_mode=replace_original, ducking 0). Pure; the hook handles debounce + fetch.
 * Returns null for an empty/blank edit or an unresolvable span (no enqueue).
 */
export function buildVoiceoverCommit(args: {
  startWordId: string;
  endWordId: string;
  newText: string;
  voiceId: string | null;
  words: ProjectedWord[];
}): VoiceoverCommit | null {
  const trimmed = args.newText.trim();
  if (trimmed === "") return null;
  const byId = new Map(args.words.map((w) => [w.id, w] as const));
  const start = byId.get(args.startWordId);
  const end = byId.get(args.endWordId);
  if (start === undefined || end === undefined) return null;
  const seqIn = Math.min(start.seq_in_frame, end.seq_in_frame);
  const seqOut = Math.max(start.seq_out_frame_exclusive, end.seq_out_frame_exclusive);
  if (seqOut <= seqIn) return null;
  const base: VoiceoverCommit = {
    seqIn,
    seqOut,
    text: trimmed,
    mixMode: "replace_original",
    duckingPercent: 0,
  };
  return args.voiceId ? { ...base, voiceId: args.voiceId } : base;
}
