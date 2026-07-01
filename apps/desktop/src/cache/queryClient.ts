import { QueryClient } from "@tanstack/react-query";

/**
 * The single renderer-wide QueryClient — the central cache that keeps cut data in sync.
 *
 * Local-first / Electron tuning: no window-focus refetch (the localhost backend doesn't go
 * stale when the window regains focus), conservative retry (one retry — a dead local service
 * should surface quickly, not hang), and a short `staleTime` so reads that depend on a mutation
 * become fresh right after its `invalidateQueries`.
 */
export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 5_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});
