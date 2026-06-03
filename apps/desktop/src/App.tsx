import { type FormEvent, type ReactElement, useCallback, useEffect, useState } from "react";

import {
  type Asset,
  type Health,
  hasFile,
  LauraClient,
  type Project,
  type SearchResult,
  type Timeline,
} from "./api";
import { AssetView } from "./components/AssetView";
import { TimelineBar } from "./components/TimelineBar";

interface FpsPreset {
  label: string;
  num: number;
  den: number;
  drop: boolean;
}

const FPS_PRESETS: readonly FpsPreset[] = [
  { label: "23.976", num: 24000, den: 1001, drop: false },
  { label: "24", num: 24, den: 1, drop: false },
  { label: "25 (PAL)", num: 25, den: 1, drop: false },
  { label: "29.97 NDF", num: 30000, den: 1001, drop: false },
  { label: "29.97 DF", num: 30000, den: 1001, drop: true },
  { label: "30", num: 30, den: 1, drop: false },
  { label: "50", num: 50, den: 1, drop: false },
  { label: "59.94 DF", num: 60000, den: 1001, drop: true },
  { label: "60", num: 60, den: 1, drop: false },
];

const sleep = (ms: number): Promise<void> => new Promise((r) => setTimeout(r, ms));

function fpsLabel(p: Project): string {
  const fps = Math.round((p.sequence_rate_num / p.sequence_rate_den) * 1000) / 1000;
  return `${fps}${p.drop_frame ? " DF" : ""}`;
}

/** Import has reached a terminal-enough state for the UI (waveform ready, or a
 *  silent video proxied, or a hard failure). */
function importSettled(a: Asset): boolean {
  if (hasFile(a, "waveform")) return true;
  const probed = a.duration_frames != null;
  const silentVideo = a.type === "video" && probed && !a.audio_sample_rate && hasFile(a, "proxy");
  return silentVideo;
}

export function App(): ReactElement {
  const [client, setClient] = useState<LauraClient | null>(null);
  const [offline, setOffline] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);

  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [importingAsset, setImportingAsset] = useState<Asset | null>(null);
  const [roughCut, setRoughCut] = useState<Timeline | null>(null);
  // Clicking a rough-cut clip jumps the player: select the clip's asset + seek its IN frame.
  const [previewSeek, setPreviewSeek] = useState<{ assetId: string; frame: number } | null>(null);

  const [name, setName] = useState("");
  const [presetIdx, setPresetIdx] = useState(3);
  const [busy, setBusy] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [semantic, setSemantic] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const info = await window.laura.getServiceInfo();
      if (cancelled) return;
      if (!info) {
        setOffline(true);
        return;
      }
      const c = new LauraClient(info.baseUrl, info.token);
      setClient(c);
      try {
        setHealth(await c.health());
        setProjects(await c.listProjects());
      } catch (e) {
        setError(String(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadAssets = useCallback(
    async (c: LauraClient, projectId: string) => {
      const list = await c.listAssets(projectId);
      setAssets(list);
    },
    [],
  );

  const loadRoughCut = useCallback(async (c: LauraClient, projectId: string) => {
    const timelines = await c.listTimelines(projectId);
    const existing = timelines.find((t) => t.kind === "rough_cut");
    setRoughCut(existing ?? (await c.createTimeline(projectId, "Rough Cut")));
  }, []);

  const reloadRoughCut = useCallback(() => {
    if (client && selectedProjectId) {
      void loadRoughCut(client, selectedProjectId).catch((e) => setError(String(e)));
    }
  }, [client, selectedProjectId, loadRoughCut]);

  // Show the clip's source asset and seek the player to its IN frame. A new object each
  // call so the same clip re-seeks; AssetView only honours it once its asset matches.
  const previewClip = useCallback((assetId: string, frame: number) => {
    setSelectedAssetId(assetId);
    setPreviewSeek({ assetId, frame });
  }, []);

  async function selectProject(id: string): Promise<void> {
    setSelectedProjectId(id);
    setSelectedAssetId(null);
    setImportingAsset(null);
    setAssets([]);
    setRoughCut(null);
    setPreviewSeek(null);
    if (client) {
      try {
        await loadAssets(client, id);
        await loadRoughCut(client, id);
      } catch (e) {
        setError(String(e));
      }
    }
  }

  async function onCreateProject(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (!client || !name.trim()) return;
    const preset = FPS_PRESETS[presetIdx];
    setBusy(true);
    setError(null);
    try {
      const created = await client.createProject({
        name: name.trim(),
        sequence_rate_num: preset.num,
        sequence_rate_den: preset.den,
        drop_frame: preset.drop,
      });
      setName("");
      setProjects(await client.listProjects());
      await selectProject(created.id);
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onImport(): Promise<void> {
    if (!client || !selectedProjectId) return;
    const path = await window.laura.pickMediaFile();
    if (!path) return;
    setImporting(true);
    setError(null);
    try {
      const { asset_id } = await client.importAsset(selectedProjectId, path);
      for (let i = 0; i < 150; i++) {
        const a = await client.getAsset(asset_id);
        setImportingAsset(a);
        if (importSettled(a)) break;
        await sleep(700);
      }
      await loadAssets(client, selectedProjectId);
      setSelectedAssetId(asset_id);
    } catch (e) {
      setError(String(e));
    } finally {
      setImporting(false);
      setImportingAsset(null);
    }
  }

  async function onDeleteProject(id: string): Promise<void> {
    if (!client) return;
    try {
      await client.deleteProject(id);
      if (selectedProjectId === id) {
        setSelectedProjectId(null);
        setAssets([]);
        setRoughCut(null);
        setSelectedAssetId(null);
      }
      setProjects(await client.listProjects());
    } catch (e) {
      setError(String(e));
    }
  }

  async function onDeleteAsset(id: string): Promise<void> {
    if (!client || !selectedProjectId) return;
    try {
      await client.deleteAsset(id);
      if (selectedAssetId === id) setSelectedAssetId(null);
      await loadAssets(client, selectedProjectId);
    } catch (e) {
      setError(String(e));
    }
  }

  async function onSearch(e: FormEvent): Promise<void> {
    e.preventDefault();
    if (!client || !selectedProjectId || !searchQuery.trim()) {
      setSearchResults([]);
      return;
    }
    try {
      setSearchResults(
        await client.searchTranscript(
          selectedProjectId,
          searchQuery.trim(),
          semantic ? "semantic" : "lexical",
        ),
      );
    } catch (err) {
      setError(String(err));
    }
  }

  const detailAsset =
    importingAsset ?? assets.find((a) => a.id === selectedAssetId) ?? null;

  return (
    <div className="flex h-full flex-col">
      <header className="flex items-center justify-between border-b border-edge bg-panel px-5 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-lg font-semibold tracking-tight text-white">Laura</h1>
          <span className="text-xs text-slate-400">frame-genauer KI-Filmschnitt · local-first</span>
        </div>
        <HealthBadge health={health} offline={offline} />
      </header>

      {error && (
        <div className="border-b border-red-900 bg-red-950/60 px-5 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <main className="grid flex-1 grid-cols-[260px_280px_1fr] gap-px overflow-hidden bg-edge">
        {/* Projects */}
        <section className="flex flex-col overflow-hidden bg-ink">
          <h2 className="px-4 pb-2 pt-4 text-xs font-medium uppercase tracking-wide text-slate-500">
            Projekte
          </h2>
          <ul className="flex-1 space-y-1 overflow-auto px-3">
            {projects.map((p) => (
              <li key={p.id} className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => void selectProject(p.id)}
                  className={`min-w-0 flex-1 rounded-md px-3 py-2 text-left text-sm transition ${
                    p.id === selectedProjectId
                      ? "bg-sky-600/20 text-sky-200"
                      : "text-slate-200 hover:bg-panel"
                  }`}
                >
                  <div className="truncate font-medium">{p.name}</div>
                  <div className="text-xs text-slate-500">{fpsLabel(p)} fps</div>
                </button>
                <button
                  type="button"
                  onClick={() => void onDeleteProject(p.id)}
                  title="Projekt löschen"
                  className="shrink-0 rounded px-2 py-1 text-slate-600 hover:text-red-400"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
          <form onSubmit={onCreateProject} className="space-y-2 border-t border-edge p-3">
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="Neues Projekt…"
              className="w-full rounded-md border border-edge bg-panel px-3 py-2 text-sm text-slate-100 outline-none focus:border-slate-500"
            />
            <select
              value={presetIdx}
              onChange={(e) => setPresetIdx(Number(e.target.value))}
              className="w-full rounded-md border border-edge bg-panel px-2 py-2 text-xs text-slate-100 outline-none focus:border-slate-500"
            >
              {FPS_PRESETS.map((p, i) => (
                <option key={p.label} value={i}>
                  {p.label} fps
                </option>
              ))}
            </select>
            <button
              type="submit"
              disabled={busy || !client || !name.trim()}
              className="w-full rounded-md bg-sky-600 px-3 py-2 text-sm font-medium text-white transition hover:bg-sky-500 disabled:opacity-40"
            >
              {busy ? "Lege an…" : "Projekt anlegen"}
            </button>
          </form>
        </section>

        {/* Assets */}
        <section className="flex flex-col overflow-hidden bg-ink">
          <div className="flex items-center justify-between px-4 pb-2 pt-4">
            <h2 className="text-xs font-medium uppercase tracking-wide text-slate-500">Medien</h2>
            <button
              type="button"
              onClick={() => void onImport()}
              disabled={!selectedProjectId || importing}
              className="rounded-md bg-panel px-2 py-1 text-xs text-slate-200 transition hover:bg-edge disabled:opacity-40"
            >
              {importing ? "Importiere…" : "+ Import"}
            </button>
          </div>
          <form onSubmit={onSearch} className="space-y-1 px-3 pb-2">
            <input
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder={semantic ? "Semantisch suchen…" : "Transkript durchsuchen…"}
              disabled={!selectedProjectId}
              className="w-full rounded-md border border-edge bg-panel px-3 py-1.5 text-xs text-slate-100 outline-none focus:border-slate-500 disabled:opacity-40"
            />
            <button
              type="button"
              onClick={() => setSemantic((s) => !s)}
              disabled={!selectedProjectId}
              title="Lexikalisch (LIKE) ↔ semantisch (Qdrant-Vektoren)"
              className={`rounded px-2 py-0.5 text-[10px] transition disabled:opacity-40 ${
                semantic ? "bg-sky-600/30 text-sky-200" : "bg-panel text-slate-400 hover:bg-edge"
              }`}
            >
              {semantic ? "● semantisch" : "○ lexikalisch"}
            </button>
          </form>
          {searchResults.length > 0 && (
            <ul className="max-h-40 space-y-1 overflow-auto border-b border-edge px-3 pb-2">
              {searchResults.map((r) => (
                <li key={r.segment_id}>
                  <button
                    type="button"
                    onClick={() => setSelectedAssetId(r.asset_id)}
                    className="w-full truncate rounded bg-panel px-2 py-1 text-left text-xs text-slate-300 hover:bg-edge"
                  >
                    {r.score != null && (
                      <span className="mr-1 text-emerald-400">{Math.round(r.score * 100)}%</span>
                    )}
                    <span className="text-slate-500">{r.asset_name}:</span> {r.text.slice(0, 60)}
                  </button>
                </li>
              ))}
            </ul>
          )}
          <ul className="flex-1 space-y-1 overflow-auto px-3 pb-3">
            {!selectedProjectId && (
              <li className="px-1 py-2 text-xs text-slate-600">Wähle links ein Projekt.</li>
            )}
            {selectedProjectId && assets.length === 0 && !importing && (
              <li className="px-1 py-2 text-xs text-slate-600">Noch keine Medien importiert.</li>
            )}
            {assets.map((a) => (
              <li key={a.id} className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setSelectedAssetId(a.id)}
                  className={`min-w-0 flex-1 truncate rounded-md px-3 py-2 text-left text-sm transition ${
                    a.id === selectedAssetId
                      ? "bg-sky-600/20 text-sky-200"
                      : "text-slate-200 hover:bg-panel"
                  }`}
                >
                  {a.display_name}
                </button>
                <button
                  type="button"
                  onClick={() => void onDeleteAsset(a.id)}
                  title="Medium löschen"
                  className="shrink-0 rounded px-2 py-1 text-slate-600 hover:text-red-400"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        </section>

        {/* Detail */}
        <section className="overflow-auto bg-ink p-5">
          {client && detailAsset ? (
            <AssetView
              client={client}
              asset={detailAsset}
              roughCut={roughCut}
              onTimelineChange={reloadRoughCut}
              seekRequest={
                previewSeek && previewSeek.assetId === detailAsset.id ? previewSeek : null
              }
            />
          ) : (
            <div className="flex h-full items-center justify-center text-sm text-slate-600">
              {importing ? "Importiere & analysiere…" : "Wähle ein Medium oder importiere eines."}
            </div>
          )}
        </section>
      </main>

      {client && (
        <TimelineBar
          client={client}
          timeline={roughCut}
          onChange={reloadRoughCut}
          onScrub={previewClip}
        />
      )}
    </div>
  );
}

function HealthBadge({ health, offline }: { health: Health | null; offline: boolean }): ReactElement {
  if (offline) return <Badge color="red" text="Service offline" />;
  if (!health) return <Badge color="amber" text="verbinde…" />;
  return <Badge color="green" text={`API v${health.version} · schema ${health.schema_version}`} />;
}

function Badge({ color, text }: { color: "green" | "amber" | "red"; text: string }): ReactElement {
  const dot =
    color === "green" ? "bg-emerald-400" : color === "amber" ? "bg-amber-400" : "bg-red-400";
  return (
    <span className="flex items-center gap-2 rounded-full border border-edge bg-ink px-3 py-1 text-xs text-slate-300">
      <span className={`h-2 w-2 rounded-full ${dot}`} />
      {text}
    </span>
  );
}
