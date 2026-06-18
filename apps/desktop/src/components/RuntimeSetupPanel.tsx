import { type ReactElement, useState } from "react";

import { type AiRuntimeCreate, type LauraClient, type RuntimeEffect, type RuntimeKind } from "../api";

const EFFECTS: RuntimeEffect[] = ["voice", "reenact", "lipsync", "faceswap", "restore"];
const KINDS: RuntimeKind[] = ["stub", "external_http", "container"];

function defaultContainerName(effect: RuntimeEffect, name: string): string {
  const slug = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-|-$/g, "");
  return `laura-${effect}${slug ? `-${slug}` : ""}`;
}

function parseContainerPort(value: string): number | undefined {
  const trimmed = value.trim();
  if (trimmed === "" || !/^\d+$/.test(trimmed)) return undefined;
  const parsed = Number(trimmed);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) return undefined;
  return parsed;
}

export function RuntimeSetupPanel({
  client,
  onCreated,
}: {
  client: LauraClient;
  onCreated?: () => void;
}): ReactElement {
  const [kind, setKind] = useState<RuntimeKind>("stub");
  const [effect, setEffect] = useState<RuntimeEffect>("voice");
  const [displayName, setDisplayName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [containerImage, setContainerImage] = useState("");
  const [port, setPort] = useState("");
  const [modelMount, setModelMount] = useState("");
  const [requiresGpu, setRequiresGpu] = useState(false);
  const [licenseAccepted, setLicenseAccepted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(): Promise<void> {
    if (displayName.trim() === "") return;
    setBusy(true);
    setError(null);
    try {
      const trimmedDisplayName = displayName.trim();
      const runtime: AiRuntimeCreate = {
        kind,
        effect,
        displayName: trimmedDisplayName,
        licenseStatus: licenseAccepted ? "accepted" : kind === "stub" ? "not_required" : "unknown",
      };
      if (kind === "external_http" && baseUrl.trim() !== "") {
        runtime.baseUrl = baseUrl.trim();
      }
      if (kind === "container") {
        if (containerImage.trim() !== "") runtime.containerImage = containerImage.trim();
        runtime.containerName = defaultContainerName(effect, trimmedDisplayName);
        const parsedPort = parseContainerPort(port);
        if (parsedPort !== undefined) runtime.port = parsedPort;
        if (modelMount.trim() !== "") runtime.modelMount = modelMount.trim();
        runtime.requiresGpu = requiresGpu;
      }
      const runtimeCreated = await client.createAiRuntime(runtime);
      await client.refreshAiRuntime(runtimeCreated.id);
      setDisplayName("");
      setBaseUrl("");
      setContainerImage("");
      setPort("");
      setModelMount("");
      setRequiresGpu(false);
      setLicenseAccepted(false);
      onCreated?.();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded border border-edge bg-panel/50 p-3">
      <div className="mb-2 text-xs font-semibold text-slate-200">Runtime Setup</div>
      {error !== null && <div className="mb-2 text-xs text-red-400">{error}</div>}
      <div className="flex flex-col gap-2">
        <label className="flex flex-col gap-1 text-[11px] text-slate-400">
          Runtime-Name
          <input
            aria-label="Runtime-Name"
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
          />
        </label>
        <div className="grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-[11px] text-slate-400">
            Runtime-Art
            <select
              aria-label="Runtime-Art"
              value={kind}
              onChange={(event) => setKind(event.target.value as RuntimeKind)}
              className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
            >
              {KINDS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1 text-[11px] text-slate-400">
            Effekt
            <select
              aria-label="Effekt"
              value={effect}
              onChange={(event) => setEffect(event.target.value as RuntimeEffect)}
              className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
            >
              {EFFECTS.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
        </div>
        {kind === "external_http" && (
          <label className="flex flex-col gap-1 text-[11px] text-slate-400">
            Base-URL
            <input
              aria-label="Base-URL"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
              className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
            />
          </label>
        )}
        {kind === "container" && (
          <>
            <label className="flex flex-col gap-1 text-[11px] text-slate-400">
              Container-Image
              <input
                aria-label="Container-Image"
                value={containerImage}
                onChange={(event) => setContainerImage(event.target.value)}
                className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
              />
            </label>
            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                Port
                <input
                  aria-label="Port"
                  value={port}
                  onChange={(event) => setPort(event.target.value)}
                  inputMode="numeric"
                  className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
                />
              </label>
              <label className="flex flex-col gap-1 text-[11px] text-slate-400">
                Modellpfad
                <input
                  aria-label="Modellpfad"
                  value={modelMount}
                  onChange={(event) => setModelMount(event.target.value)}
                  className="rounded border border-edge bg-ink px-2 py-1 text-xs text-slate-100"
                />
              </label>
            </div>
            <label className="flex items-center gap-2 text-xs text-slate-300">
              <input
                aria-label="GPU verwenden"
                type="checkbox"
                checked={requiresGpu}
                onChange={(event) => setRequiresGpu(event.target.checked)}
              />
              GPU verwenden
            </label>
          </>
        )}
        <label className="flex items-center gap-2 text-xs text-slate-300">
          <input
            aria-label="Lizenz akzeptiert"
            type="checkbox"
            checked={licenseAccepted}
            onChange={(event) => setLicenseAccepted(event.target.checked)}
          />
          Lizenz akzeptiert
        </label>
        <button
          type="button"
          onClick={() => void submit()}
          disabled={busy || displayName.trim() === ""}
          className="rounded bg-sky-700 px-3 py-1 text-xs font-medium text-white disabled:opacity-40"
        >
          {busy ? "Registriert..." : "Runtime registrieren"}
        </button>
      </div>
    </section>
  );
}
