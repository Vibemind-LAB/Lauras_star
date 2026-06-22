import { useCallback, useEffect, useState } from "react";

import { type LauraClient, type ConsentRecord } from "../api";

export function partitionConsent(records: ConsentRecord[]): {
  active: ConsentRecord[];
  revoked: ConsentRecord[];
} {
  const active: ConsentRecord[] = [];
  const revoked: ConsentRecord[] = [];
  for (const r of records) (r.revoked_at == null ? active : revoked).push(r);
  return { active, revoked };
}

export function useConsent(
  client: LauraClient | null,
  projectId: string | null,
) {
  const [records, setRecords] = useState<ConsentRecord[]>([]);
  const [loading, setLoading] = useState<boolean>(false);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async (): Promise<void> => {
    if (!client || !projectId) {
      setRecords([]);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      setRecords(await client.listConsent(projectId));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [client, projectId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  const create = useCallback(
    async (subjectLabel: string): Promise<void> => {
      if (!client || !projectId) return;
      await client.createConsent(projectId, { subjectLabel });
      await reload();
    },
    [client, projectId, reload],
  );

  const revoke = useCallback(
    async (consentId: string): Promise<void> => {
      if (!client || !projectId) return;
      await client.revokeConsent(projectId, consentId);
      await reload();
    },
    [client, projectId, reload],
  );

  const { active } = partitionConsent(records);
  return { records, active, loading, error, create, revoke, reload };
}
