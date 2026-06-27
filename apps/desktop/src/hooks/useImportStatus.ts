import { useQuery } from "@tanstack/react-query";

import type { ImportStatus, LauraClient } from "../api";

const TERMINAL: ReadonlySet<ImportStatus["phase"]> = new Set(["ready", "error", "cancelled"]);

/** Stable query-key factory for import-status — scoped to assetId. */
function importStatusKey(assetId: string): readonly [string, string] {
  return ["importStatus", assetId] as const;
}

export function useImportStatus(
  client: LauraClient,
  assetId: string | null,
  intervalMs = 1000,
): ImportStatus | null {
  const query = useQuery({
    queryKey: importStatusKey(assetId ?? ""),
    queryFn: () => client.getImportStatus(assetId!),
    enabled: assetId !== null,
    refetchInterval: (query) => {
      const data = query.state.data;
      if (data !== undefined && TERMINAL.has(data.phase)) return false;
      return intervalMs;
    },
    // Always fetch fresh — the import status changes rapidly while in-flight.
    staleTime: 0,
    // On error, keep retrying at the same interval (mirror the original catch-and-reschedule).
    retryDelay: intervalMs,
    retry: true,
  });

  return assetId !== null ? (query.data ?? null) : null;
}
