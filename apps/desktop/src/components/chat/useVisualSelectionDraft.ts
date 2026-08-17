import { useCallback, useEffect, useRef, useState } from "react";

import {
  LauraApiError,
  type LauraClient,
  type VisualSceneSelection,
  type VisualSelectionDraft,
  type VisualSelectionGateStatus,
} from "../../api";

type DraftClient = Pick<LauraClient, "saveVisualSelectionDraft">;

export type VisualDraftSaveState =
  | "idle"
  | "saving"
  | "saved"
  | "error"
  | "conflict"
  | "stale";

export interface UseVisualSelectionDraftArgs {
  client: DraftClient;
  sessionId: string;
  gate: VisualSelectionGateStatus;
}

export interface VisualSelectionDraftController {
  decisions: VisualSceneSelection[];
  updateDecision: (next: VisualSceneSelection[]) => void;
  saveState: VisualDraftSaveState;
  savedAt: string | null;
  flush: () => Promise<void>;
  retry: () => void;
  loadServerDraft: () => void;
}

function recommendedSelections(gate: VisualSelectionGateStatus): VisualSceneSelection[] {
  return (gate.scene_choices ?? []).map((choice) => ({
    rough_cut_order: choice.rough_cut_order,
    candidate_id: choice.recommended_candidate_id,
    included: choice.recommended_included,
    requested_duration_s: choice.recommended_duration_s,
  }));
}

function initialSelections(gate: VisualSelectionGateStatus): VisualSceneSelection[] {
  return gate.draft?.selections ?? recommendedSelections(gate);
}

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : null;
}

function conflictCode(error: LauraApiError): string | null {
  const detail = record(record(error.body)?.detail);
  return typeof detail?.code === "string" ? detail.code : null;
}

function conflictDraft(error: LauraApiError): VisualSelectionDraft | null {
  const current = record(record(record(error.body)?.detail)?.current);
  if (
    current === null ||
    typeof current.session_id !== "string" ||
    typeof current.proposal_hash !== "string" ||
    !Array.isArray(current.selections) ||
    typeof current.revision !== "number"
  ) {
    return null;
  }
  return current as unknown as VisualSelectionDraft;
}

export function useVisualSelectionDraft({
  client,
  sessionId,
  gate,
}: UseVisualSelectionDraftArgs): VisualSelectionDraftController {
  const [decisions, setDecisions] = useState<VisualSceneSelection[]>(() =>
    initialSelections(gate),
  );
  const [saveState, setSaveState] = useState<VisualDraftSaveState>(() =>
    gate.draft?.stale ? "stale" : gate.draft?.updated_utc ? "saved" : "idle",
  );
  const [savedAt, setSavedAt] = useState<string | null>(gate.draft?.updated_utc ?? null);
  const revisionRef = useRef<number | null>(gate.draft?.revision ?? null);
  const queueRef = useRef<Promise<void>>(Promise.resolve());
  const latestSelectionsRef = useRef<VisualSceneSelection[]>(initialSelections(gate));
  const serverDraftRef = useRef<VisualSelectionDraft | null>(gate.draft ?? null);
  const blockedRef = useRef(gate.draft?.stale ?? false);
  const generationRef = useRef(0);
  const proposalHash = gate.proposal_id;
  const proposalKey = `${sessionId}:${proposalHash ?? "none"}`;

  useEffect(() => {
    generationRef.current += 1;
    const next = initialSelections(gate);
    latestSelectionsRef.current = next;
    revisionRef.current = gate.draft?.revision ?? null;
    serverDraftRef.current = gate.draft ?? null;
    blockedRef.current = gate.draft?.stale ?? false;
    queueRef.current = Promise.resolve();
    setDecisions(next);
    setSavedAt(gate.draft?.updated_utc ?? null);
    setSaveState(
      gate.draft?.stale ? "stale" : gate.draft?.updated_utc ? "saved" : "idle",
    );
    // The persisted proposal identity, not polling object identity, owns a local draft generation.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [proposalKey]);

  const enqueue = useCallback(
    (snapshot: VisualSceneSelection[]): void => {
      const generation = generationRef.current;
      queueRef.current = queueRef.current.then(async () => {
        if (generation !== generationRef.current || blockedRef.current) return;
        if (proposalHash === null) {
          blockedRef.current = true;
          setSaveState("stale");
          return;
        }
        setSaveState("saving");
        try {
          const savedDraft = await client.saveVisualSelectionDraft(sessionId, {
            proposal_hash: proposalHash,
            expected_revision: revisionRef.current,
            selections: snapshot,
          });
          if (generation !== generationRef.current) return;
          revisionRef.current = savedDraft.revision;
          serverDraftRef.current = savedDraft;
          setSavedAt(savedDraft.updated_utc);
          setSaveState(savedDraft.stale ? "stale" : "saved");
          blockedRef.current = savedDraft.stale;
        } catch (error) {
          if (generation !== generationRef.current) return;
          blockedRef.current = true;
          if (error instanceof LauraApiError && error.status === 409) {
            const code = conflictCode(error);
            if (code === "revision_conflict") {
              serverDraftRef.current = conflictDraft(error);
              setSaveState("conflict");
              return;
            }
            if (code === "stale_visual_selection") {
              setSaveState("stale");
              return;
            }
          }
          setSaveState("error");
        }
      });
    },
    [client, proposalHash, sessionId],
  );

  const updateDecision = useCallback(
    (next: VisualSceneSelection[]): void => {
      const snapshot = next.map((selection) => ({ ...selection }));
      latestSelectionsRef.current = snapshot;
      setDecisions(snapshot);
      if (!blockedRef.current) setSaveState("saving");
      enqueue(snapshot);
    },
    [enqueue],
  );

  const flush = useCallback(async (): Promise<void> => {
    await queueRef.current;
  }, []);

  const retry = useCallback((): void => {
    blockedRef.current = false;
    setSaveState("saving");
    enqueue(latestSelectionsRef.current.map((selection) => ({ ...selection })));
  }, [enqueue]);

  const loadServerDraft = useCallback((): void => {
    const serverDraft = serverDraftRef.current;
    if (serverDraft === null) return;
    const next = serverDraft.selections.map((selection) => ({ ...selection }));
    latestSelectionsRef.current = next;
    revisionRef.current = serverDraft.revision;
    blockedRef.current = serverDraft.stale;
    setDecisions(next);
    setSavedAt(serverDraft.updated_utc);
    setSaveState(serverDraft.stale ? "stale" : "saved");
  }, []);

  return {
    decisions,
    updateDecision,
    saveState,
    savedAt,
    flush,
    retry,
    loadServerDraft,
  };
}
