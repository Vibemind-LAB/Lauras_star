/**
 * Normalized query-key factory — the single source of cache keys so every read and the
 * invalidation map use identical keys and never drift. Keyed by entity + the id(s) that scope
 * it. See docs/superpowers/plans/2026-06-26-client-cache-sync-refactor.md for the invalidation
 * map (which mutation refreshes which keys).
 */
export const qk = {
  projects: () => ["projects"] as const,
  assets: (projectId: string) => ["assets", projectId] as const,
  analysis: (assetId: string) => ["analysis", assetId] as const,
  shots: (assetId: string) => ["shots", assetId] as const,
  transcript: (assetId: string) => ["transcript", assetId] as const,
  roughCut: (projectId: string, assetId: string) => ["roughCut", projectId, assetId] as const,
  timeline: (timelineId: string) => ["timeline", timelineId] as const,
  scenes: (timelineId: string) => ["scenes", timelineId] as const,
  projectScenes: (projectId: string) => ["projectScenes", projectId] as const,
  sequence: (projectId: string) => ["sequence", projectId] as const,
  sequenceFlattened: (sequenceId: string) => ["sequenceFlattened", sequenceId] as const,
  sequenceTranscript: (sequenceId: string) => ["sequenceTranscript", sequenceId] as const,
  audioClips: (timelineId: string) => ["audioClips", timelineId] as const,
  exports: (projectId: string) => ["exports", projectId] as const,
  job: (jobId: string) => ["job", jobId] as const,
} as const;
