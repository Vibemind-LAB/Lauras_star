import type { ReactElement } from "react";
import { STAGES, type Stage } from "../pipeline/stages";
export function NavRail({ active, onSelect }: { active: Stage; onSelect: (stage: Stage) => void }): ReactElement {
  return (
    <nav className="flex w-44 shrink-0 flex-col gap-1 border-r border-edge bg-panel p-2">
      {STAGES.map((s, i) => (
        <button
          key={s.id}
          type="button"
          aria-current={s.id === active ? "page" : undefined}
          onClick={() => onSelect(s.id)}
          className={`flex items-center gap-2 rounded px-3 py-2 text-left text-sm transition ${
            s.id === active ? "bg-sky-600/20 text-sky-300" : "text-slate-300 hover:bg-edge hover:text-white"
          }`}
        >
          <span aria-hidden="true" className="w-4 text-right text-[10px] tabular-nums text-slate-500">{i + 1}</span>
          {s.label}
        </button>
      ))}
    </nav>
  );
}
