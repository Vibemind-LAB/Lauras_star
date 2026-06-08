import { useEffect, useState } from "react";

import type { ImportStatus, LauraClient } from "../api";

const TERMINAL: ReadonlySet<ImportStatus["phase"]> = new Set(["ready", "error", "cancelled"]);

export function useImportStatus(
  client: LauraClient,
  assetId: string | null,
  intervalMs = 1000,
): ImportStatus | null {
  const [status, setStatus] = useState<ImportStatus | null>(null);

  useEffect(() => {
    if (assetId == null) {
      setStatus(null);
      return;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const poll = async (): Promise<void> => {
      try {
        const s = await client.getImportStatus(assetId);
        if (!active) return;
        setStatus(s);
        if (!TERMINAL.has(s.phase)) {
          timer = setTimeout(poll, intervalMs);
        }
      } catch {
        if (active) timer = setTimeout(poll, intervalMs);
      }
    };
    void poll();

    return () => {
      active = false;
      if (timer) clearTimeout(timer);
    };
  }, [client, assetId, intervalMs]);

  return status;
}
