import { type ReactElement, useEffect, useMemo, useState } from "react";

import {
  type AiPersona,
  type AiRuntime,
  type LauraClient,
  type PreferredRuntimeMap,
  type RuntimeEffect,
} from "../api";

const EFFECTS: RuntimeEffect[] = ["voice", "reenact", "lipsync", "faceswap"];

function runtimesForEffect(runtimes: AiRuntime[], effect: RuntimeEffect): AiRuntime[] {
  return runtimes.filter((runtime) => runtime.effect === effect);
}

function preferredRuntimeSummary(preferredRuntimes: PreferredRuntimeMap): string {
  const entries = Object.entries(preferredRuntimes) as [RuntimeEffect, string][];
  if (entries.length === 0) return "keine Runtime-Präferenz";
  return entries.map(([effect, runtimeId]) => `${effect} -> ${runtimeId}`).join(", ");
}

export function PersonaKitPanel({
  client,
  projectId,
}: {
  client: LauraClient;
  projectId: string | null;
}): ReactElement {
  const [personas, setPersonas] = useState<AiPersona[]>([]);
  const [runtimes, setRuntimes] = useState<AiRuntime[]>([]);
  const [name, setName] = useState("");
  const [faceReferenceAssetId, setFaceReferenceAssetId] = useState("");
  const [voiceReferenceAssetId, setVoiceReferenceAssetId] = useState("");
  const [effects, setEffects] = useState<RuntimeEffect[]>([]);
  const [preferredRuntimes, setPreferredRuntimes] = useState<PreferredRuntimeMap>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function load(): Promise<void> {
    if (projectId === null) {
      setPersonas([]);
      setRuntimes([]);
      return;
    }
    const [nextPersonas, nextRuntimes] = await Promise.all([
      client.listAiPersonas(projectId),
      client.listAiRuntimes(),
    ]);
    setPersonas(nextPersonas);
    setRuntimes(nextRuntimes);
  }

  useEffect(() => {
    void load().catch((err: unknown) => {
      setError(err instanceof Error ? err.message : String(err));
    });
  }, [client, projectId]);

  useEffect(() => {
    setName("");
    setFaceReferenceAssetId("");
    setVoiceReferenceAssetId("");
    setEffects([]);
    setPreferredRuntimes({});
    setBusy(false);
    setError(null);
  }, [projectId]);

  function toggle(effect: RuntimeEffect): void {
    setEffects((current) => {
      if (current.includes(effect)) {
        setPreferredRuntimes((existing) => {
          const next = { ...existing };
          delete next[effect];
          return next;
        });
        return current.filter((item) => item !== effect);
      }

      const firstRuntime = runtimesForEffect(runtimes, effect)[0];
      if (firstRuntime !== undefined) {
        setPreferredRuntimes((existing) => ({
          ...existing,
          [effect]: existing[effect] ?? firstRuntime.id,
        }));
      }
      return [...current, effect];
    });
  }

  async function createPersona(): Promise<void> {
    if (projectId === null || name.trim() === "") {
      return;
    }

    setBusy(true);
    setError(null);
    try {
      const trimmedName = name.trim();
      const consent = await client.createConsent(projectId, { subjectLabel: trimmedName });
      const selectedPreferredRuntimes = effects.reduce<PreferredRuntimeMap>((accumulator, effect) => {
        const runtimeId = preferredRuntimes[effect];
        if (runtimeId !== undefined && runtimeId !== "") {
          accumulator[effect] = runtimeId;
        }
        return accumulator;
      }, {});

      await client.createAiPersona({
        projectId,
        name: trimmedName,
        consentId: consent.id,
        faceReferenceAssetId: faceReferenceAssetId.trim() || undefined,
        voiceReferenceAssetId: voiceReferenceAssetId.trim() || undefined,
        allowedEffects: effects,
        preferredRuntimes: selectedPreferredRuntimes,
      });

      setName("");
      setFaceReferenceAssetId("");
      setVoiceReferenceAssetId("");
      setEffects([]);
      setPreferredRuntimes({});
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  const sortedPersonas = useMemo(
    () =>
      [...personas].sort((left, right) =>
        left.name.localeCompare(right.name, "de", { sensitivity: "base" }),
      ),
    [personas],
  );

  return (
    <section className="rounded border border-edge bg-panel/50 p-3">
      <div className="mb-2 flex items-center justify-between gap-2">
        <div>
          <div className="text-xs font-semibold text-slate-200">AI Persona Kit</div>
          <div className="text-[11px] text-slate-600">Consent, Referenzen und Runtime-Präferenzen</div>
        </div>
        <span className="text-[11px] text-slate-500">{sortedPersonas.length} Personas</span>
      </div>

      {error !== null && (
        <div className="mb-2 rounded border border-red-900/70 bg-red-950/20 p-2 text-xs text-red-200">
          {error}
        </div>
      )}

      <div className="grid grid-cols-1 gap-2">
        <label className="flex flex-col gap-1 text-[11px] text-slate-400">
          Persona-Name
          <input
            aria-label="Persona-Name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            disabled={busy || projectId === null}
            placeholder="Persona-Name"
            className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100 disabled:opacity-50"
          />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-[11px] text-slate-400">
            Face-Reference-Asset
            <input
              aria-label="Face-Reference-Asset"
              value={faceReferenceAssetId}
              onChange={(event) => setFaceReferenceAssetId(event.target.value)}
              disabled={busy || projectId === null}
              placeholder="asset-face-123"
              className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100 disabled:opacity-50"
            />
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-slate-400">
            Voice-Reference-Asset
            <input
              aria-label="Voice-Reference-Asset"
              value={voiceReferenceAssetId}
              onChange={(event) => setVoiceReferenceAssetId(event.target.value)}
              disabled={busy || projectId === null}
              placeholder="asset-voice-456"
              className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100 disabled:opacity-50"
            />
          </label>
        </div>
        <div className="grid grid-cols-2 gap-2">
          {EFFECTS.map((effect) => {
            const effectRuntimes = runtimesForEffect(runtimes, effect);
            const enabled = effects.includes(effect);

            return (
              <div key={effect} className="flex flex-col gap-1 rounded border border-edge/70 bg-ink/50 p-2">
                <label className="flex items-center gap-2 text-xs text-slate-300">
                  <input
                    type="checkbox"
                    checked={enabled}
                    onChange={() => toggle(effect)}
                    disabled={busy || projectId === null}
                  />
                  {effect}
                </label>
                <label className="flex flex-col gap-1 text-[11px] text-slate-500">
                  Runtime
                  <select
                    aria-label={`Bevorzugte Runtime für ${effect}`}
                    value={preferredRuntimes[effect] ?? ""}
                    onChange={(event) =>
                      setPreferredRuntimes((current) => ({
                        ...current,
                        [effect]: event.target.value,
                      }))
                    }
                    disabled={!enabled || busy || projectId === null}
                    className="rounded border border-edge bg-panel px-2 py-1 text-xs text-slate-100 disabled:opacity-50"
                  >
                    <option value="">automatisch</option>
                    {effectRuntimes.map((runtime) => (
                      <option key={runtime.id} value={runtime.id}>
                        {runtime.display_name}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
            );
          })}
        </div>
        <button
          type="button"
          onClick={() => void createPersona()}
          disabled={busy || projectId === null || name.trim() === ""}
          className="rounded bg-sky-700 px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
        >
          {busy ? "Erstellt..." : "Persona erstellen"}
        </button>
      </div>

      <div className="mt-3 border-t border-edge/80 pt-2">
        {sortedPersonas.length === 0 ? (
          <div className="text-xs text-slate-500">Noch keine Persona.</div>
        ) : (
          <div className="flex flex-col divide-y divide-edge/70">
            {sortedPersonas.map((persona) => (
              <div key={persona.id} className="py-2 text-xs">
                <div className="font-medium text-slate-100">{persona.name}</div>
                <div className="mt-1 text-[11px] text-slate-500">{persona.consent_id}</div>
                <div className="text-[11px] text-slate-500">
                  Face: <span>{persona.face_reference_asset_id ?? "keine"}</span>
                </div>
                <div className="text-[11px] text-slate-500">
                  Voice: <span>{persona.voice_reference_asset_id ?? "keine"}</span>
                </div>
                <div className="text-[11px] text-slate-500">
                  {persona.allowed_effects.length > 0
                    ? persona.allowed_effects.join(", ")
                    : "keine Effekte"}
                </div>
                <div className="text-[11px] text-slate-500">
                  {preferredRuntimeSummary(persona.preferred_runtimes)}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </section>
  );
}
