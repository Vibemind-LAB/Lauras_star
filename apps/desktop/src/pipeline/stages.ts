export type Stage = "chat" | "media" | "roughcut" | "finecut" | "assemble" | "shorts" | "export";
export interface StageMeta { id: Stage; label: string; }
// "media" replaced the separate Download and Import stages: Import already did everything
// Download did (URL import, the same gallery) plus search/filter/sort, format and cookie
// options and file/folder pickers — two tabs for one job, one of them strictly poorer.
export const STAGES: readonly StageMeta[] = [
  { id: "chat", label: "💬 Chat" },
  { id: "media", label: "Media" },
  { id: "roughcut", label: "Rough Cut" },
  { id: "finecut", label: "Fine Cut" },
  { id: "assemble", label: "Assemble" },
  { id: "shorts", label: "Shorts" },
  { id: "export", label: "Export" },
] as const;
