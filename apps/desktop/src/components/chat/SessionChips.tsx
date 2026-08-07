import { type ReactElement, useState } from "react";

import type { ProductionArtifactState, ProductionStatus } from "../../api";

/** Board artifact chain in display order, with a friendly short label per slot. `render_report`
 * reads as "Export" rather than its raw key — that's what it represents to the user, and the
 * board status carries no separate export id to show instead (see ProductionStatus). */
const SESSION_ARTIFACT_ORDER = [
  "storyline",
  "script",
  "voice",
  "cutlist",
  "contact_sheet",
  "render_report",
  "qa_report",
] as const;

const SESSION_ARTIFACT_LABELS: Record<(typeof SESSION_ARTIFACT_ORDER)[number], string> = {
  storyline: "storyline",
  script: "script",
  voice: "voice",
  cutlist: "cutlist",
  contact_sheet: "Bogen",
  render_report: "Export",
  qa_report: "QA",
};

/** *name* under {@link SESSION_ARTIFACT_LABELS}'s friendly label, or itself when unrecognized —
 * defensive because artifact names travelling through `restored` are wire data, not a closed
 * union at this point. */
export function sessionArtifactLabel(name: string): string {
  return name in SESSION_ARTIFACT_LABELS
    ? SESSION_ARTIFACT_LABELS[name as keyof typeof SESSION_ARTIFACT_LABELS]
    : name;
}

/** Shared chip-pill styling — the plain read-only chip and the button variant that opens a
 * revert dropdown both use it, so the button never looks different from its neighbors at rest. */
const SESSION_CHIP_CLS =
  "inline-block rounded-full border border-accent/40 bg-accent/15 px-2 py-0.5 text-[10px] text-content-strong";

/** One artifact chip whose slot has archived versions: click opens a small dropdown listing
 * them ("v1", "v2", …) plus the current version for reference; picking one and confirming with
 * "Zurückdrehen" calls `onConfirm(version)`. Owns its open/selected state independently per
 * chip — simpler than a shared "which chip is open" slot on the parent, and multiple dropdowns
 * open at once is harmless. */
function RevertChip({
  text,
  title,
  archivedVersions,
  currentVersion,
  onConfirm,
}: {
  text: string;
  title?: string;
  archivedVersions: number[];
  currentVersion: number;
  onConfirm: (version: number) => void;
}): ReactElement {
  const [open, setOpen] = useState(false);
  const [selected, setSelected] = useState<number | null>(null);
  // `board.revert` leaves the restored version's own archive entry in versions/ — so reverting
  // an artifact to v1 can leave v1 sitting in `archivedVersions` alongside the *new* current
  // version's number being v1 too. Without this filter the dropdown would offer a no-op revert
  // to the version already showing as current.
  const offeredVersions = archivedVersions.filter((v) => v !== currentVersion);

  return (
    <div className="relative">
      <button
        type="button"
        title={title}
        onClick={() => {
          setOpen((prev) => !prev);
          setSelected(null);
        }}
        className={`${SESSION_CHIP_CLS} cursor-pointer hover:bg-accent/25`}
      >
        {text}
      </button>
      {open && (
        <div className="absolute z-10 mt-1 flex flex-col gap-1 rounded border border-bezel bg-surface-1 p-1.5 text-[10px] shadow-lg">
          <div className="text-content-faint">aktuell: v{currentVersion}</div>
          <div className="flex gap-1">
            {offeredVersions.map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={selected === v}
                onClick={() => setSelected(v)}
                className={`rounded border px-1.5 py-0.5 ${
                  selected === v
                    ? "border-accent bg-accent/30 text-content-strong"
                    : "border-bezel text-content-muted hover:text-content-strong"
                }`}
              >
                v{v}
              </button>
            ))}
          </div>
          <button
            type="button"
            disabled={selected === null}
            onClick={() => {
              if (selected === null) return;
              onConfirm(selected);
              setOpen(false);
              setSelected(null);
            }}
            className="rounded bg-accent px-1.5 py-0.5 font-medium text-accent-ink disabled:opacity-40"
          >
            Zurückdrehen
          </button>
        </div>
      )}
    </div>
  );
}

/** One chip's render data: a plain read-only pill, or (when `onRevert` is wired and the slot has
 * archived versions) a revert-capable button chip. */
type SessionChipData =
  | { key: string; kind: "plain"; text: string; title?: string }
  | {
      key: string;
      kind: "revert";
      text: string;
      title?: string;
      archivedVersions: number[];
      currentVersion: number;
      onConfirm: (version: number) => void;
    };

/** Board chips: a restored-artifacts chip when a resume brought any back, a review-count chip
 * when any exist, then one version chip per present artifact (chain order) — e.g. "♻️ 2",
 * "🎬 5", "storyline v2", "script v1". When `onRevert` is given, an artifact chip with archived
 * versions renders as a button that opens a small revert dropdown ({@link RevertChip}) instead
 * of a plain pill — chips without archived versions are never affected. */
export function SessionChips({
  status,
  onRevert,
}: {
  status: ProductionStatus;
  onRevert?: (artifact: string, version: number) => void;
}): ReactElement | null {
  const chips: SessionChipData[] = [];

  // Available before the board exists too — `job` (and its `restored` list) sits outside the
  // board_ready discriminant, so a resume's restore is visible even in the queued/running window.
  const restored = status.job?.restored;
  if (restored !== undefined && restored.length > 0) {
    chips.push({
      key: "restored",
      kind: "plain",
      text: `♻️ ${restored.length}`,
      title: `Wiederhergestellt: ${restored.map(sessionArtifactLabel).join(", ")}`,
    });
  }

  // Before the board exists there is nothing else to chip — and dereferencing the board fields
  // on that shape was a live crash: every new session passes through a queued/running window in
  // which the endpoint reports only { job, board_ready: false }.
  if (status.board_ready) {
    if (status.scene_reviews.count > 0) {
      // A degraded review is one the VLM never actually produced — a board with zero visual
      // analysis used to look identical to a fully reviewed one in this very chip.
      const degraded = status.scene_reviews.degraded_count;
      chips.push({
        key: "reviews",
        kind: "plain",
        text:
          degraded > 0
            ? `🎬 ${status.scene_reviews.count} (${degraded}⚠)`
            : `🎬 ${status.scene_reviews.count}`,
        title:
          degraded > 0
            ? `${degraded} Review(s) ohne echte Bildanalyse (Szenen ${status.scene_reviews.degraded_scenes.join(", ")})`
            : undefined,
      });
    }
    for (const name of SESSION_ARTIFACT_ORDER) {
      // Defensive: a pre-Kontaktbogen backend does not send the contact_sheet key at all —
      // the type says required, the wire decides. A missing key must skip, never crash.
      const info = status.artifacts[name] as ProductionArtifactState | undefined;
      if (info === undefined || info.version === null) continue;
      const warnings: string[] = [];
      if (info.stale === true) {
        warnings.push("gehört zu einem älteren Skript (stale)");
      }
      if (info.checks_ok === false) {
        warnings.push(`Checks fehlgeschlagen: ${(info.failed_checks ?? []).join(", ")}`);
      }
      // Unknown is not current: null means the artifact predates provenance and cannot be
      // judged either way — saying nothing here would present it as proven-fresh.
      const unknown = info.stale === null ? "Provenienz unbekannt (älteres Board)" : undefined;
      // How much of the requested length the DELIVERED film reached. Live 2026-08-02: a 60s
      // short came out 15.8s and the panel showed a finished export with no hint why. A short
      // film is allowed — the Scene Author's charter says to write less rather than invent
      // material — so this is a fact, never a ⚠: staying silent about it is the defect.
      const ratio = name === "render_report" ? info.target_ratio : undefined;
      const ratioText = typeof ratio === "number" ? ` · ${Math.round(ratio * 100)}%` : "";
      const ratioTitle =
        typeof ratio === "number"
          ? `gelieferter Film: ${Math.round(ratio * 100)}% der Ziellänge`
          : undefined;
      const text = `${SESSION_ARTIFACT_LABELS[name]} v${info.version}${ratioText}${warnings.length > 0 ? " ⚠" : ""}`;
      const title =
        [...warnings, ...(ratioTitle !== undefined ? [ratioTitle] : [])].join(" · ") ||
        unknown;
      if (onRevert !== undefined && info.archived_versions.some((v) => v !== info.version)) {
        const revert = onRevert;
        const artifact = name;
        chips.push({
          key: name,
          kind: "revert",
          text,
          title,
          archivedVersions: info.archived_versions,
          currentVersion: info.version,
          onConfirm: (version) => revert(artifact, version),
        });
      } else {
        chips.push({ key: name, kind: "plain", text, title });
      }
    }
  }
  if (chips.length === 0) return null;
  return (
    <div className="mb-1 flex flex-wrap gap-1">
      {chips.map((chip) =>
        chip.kind === "plain" ? (
          <span key={chip.key} title={chip.title} className={SESSION_CHIP_CLS}>
            {chip.text}
          </span>
        ) : (
          <RevertChip
            key={chip.key}
            text={chip.text}
            title={chip.title}
            archivedVersions={chip.archivedVersions}
            currentVersion={chip.currentVersion}
            onConfirm={chip.onConfirm}
          />
        ),
      )}
    </div>
  );
}

/** Extracts an HTTP status code and a human-readable detail from a `LauraClient` request error
 * (`Error("<status>: <body>")` — see api.ts's `request()`), decoding a FastAPI `{"detail": "..."}`
 * body when present, since that's how the revert endpoint's 409/422 responses are shaped. */
export function parseRevertError(e: unknown): { code: number | null; detail: string } {
  const message = e instanceof Error ? e.message : String(e);
  const match = message.match(/^(\d{3}):\s*([\s\S]*)$/);
  if (match === null) return { code: null, detail: message };
  const code = Number(match[1]);
  const body = match[2];
  try {
    const parsed = JSON.parse(body) as unknown;
    if (typeof parsed === "object" && parsed !== null && "detail" in parsed) {
      const detail = (parsed as Record<string, unknown>).detail;
      if (typeof detail === "string") return { code, detail };
    }
  } catch {
    // Not JSON — fall through to the raw body text.
  }
  return { code, detail: body };
}
