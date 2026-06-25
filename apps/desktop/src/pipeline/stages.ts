export type Stage = "download" | "import" | "roughcut" | "finecut" | "assemble" | "shorts" | "export";
export interface StageMeta { id: Stage; label: string; }
export const STAGES: readonly StageMeta[] = [
  { id: "download", label: "Download" },
  { id: "import", label: "Import" },
  { id: "roughcut", label: "Rough Cut" },
  { id: "finecut", label: "Feinschnitt" },
  { id: "assemble", label: "Zusammenfügen" },
  { id: "shorts", label: "Shorts" },
  { id: "export", label: "Export" },
] as const;
