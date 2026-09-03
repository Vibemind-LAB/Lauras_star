import { type ReactElement, useEffect, useMemo, useState } from "react";

import { type AiRuntime, type LauraClient, type RuntimeEffect } from "../api";
import { log } from "../shared/log";

function runtimeLabel(runtime: AiRuntime): string {
  if (runtime.kind === "stub") return runtime.display_name;
  return `${runtime.display_name} · ${runtime.kind}`;
}

export function RuntimeSelect({
  client,
  effect,
  label,
  value,
  onChange,
  disabled = false,
  reloadKey = 0,
  labelClassName = "flex flex-col gap-1 text-xs text-slate-400",
  selectClassName = "rounded border border-edge bg-panel px-2 py-1 text-xs text-slate-200 disabled:opacity-50",
}: {
  client: LauraClient;
  effect: RuntimeEffect;
  label: string;
  value: string;
  onChange: (runtimeId: string) => void;
  disabled?: boolean;
  reloadKey?: number;
  labelClassName?: string;
  selectClassName?: string;
}): ReactElement {
  const [runtimes, setRuntimes] = useState<AiRuntime[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    client
      .listAiRuntimes(effect)
      .then((rows) => {
        if (cancelled) return;
        setRuntimes(rows);
        setError(null);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : String(err);
        log.error("listAiRuntimes failed:", message);
        setRuntimes([]);
        setError(message);
      });
    return () => {
      cancelled = true;
    };
  }, [client, effect, reloadKey]);

  const selectableRuntimes = useMemo(
    () =>
      runtimes
        .filter((runtime) => runtime.effect === effect && runtime.enabled)
        .sort((left, right) =>
          left.display_name.localeCompare(right.display_name, "de", { sensitivity: "base" }),
        ),
    [effect, runtimes],
  );

  useEffect(() => {
    if (value !== "" && !selectableRuntimes.some((runtime) => runtime.id === value)) {
      onChange("");
    }
  }, [onChange, selectableRuntimes, value]);

  return (
    <label className={labelClassName}>
      Runtime
      <select
        aria-label={label}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
        className={selectClassName}
      >
        <option value="">automatisch</option>
        {selectableRuntimes.map((runtime) => (
          <option key={runtime.id} value={runtime.id}>
            {runtimeLabel(runtime)}
          </option>
        ))}
      </select>
      {error !== null && <span className="text-[11px] text-red-300">{error}</span>}
    </label>
  );
}
