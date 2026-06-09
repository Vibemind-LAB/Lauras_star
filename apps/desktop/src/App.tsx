import { type FormEvent, type ReactElement, useCallback, useEffect, useState } from "react";

import {
  type Asset,
  type Health,
  LauraClient,
  type Project,
  type SearchResult,
  type Segment,
  type Shot,
  type Timeline,
  type UrlImportOptions,
} from "./api";
import { AssembleView } from "./components/AssembleView";
import { DownloadView } from "./components/DownloadView";
import { DropZone, type ResolvedImport } from "./components/DropZone";
import { ExportView } from "./components/ExportView";
import { FineCutView } from "./components/FineCutView";
import { ImportBar } from "./components/ImportBar";
import { ImportProgress } from "./components/ImportProgress";
import { ImportView } from "./components/ImportView";
import { InspectorPanel } from "./components/InspectorPanel";
import { MediaSidebar } from "./components/MediaSidebar";
import { NavRail } from "./components/NavRail";
import { Player } from "./components/Player";
import { RoughCutView } from "./components/RoughCutView";
import { SceneInspector } from "./components/SceneInspector";
import { TimelineBar } from "./components/TimelineBar";
import { TranscriptBar } from "./components/TranscriptBar";
import { useAnalysis } from "./hooks/useAnalysis";
import { useImportStatus } from "./hooks/useImportStatus";
import type { Stage } from "./pipeline/stages";

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

function fpsLabel(p: Project): string {
  const fps = Math.round((p.sequence_rate_num / p.sequence_rate_den) * 1000) / 1000;
  return `${fps}${p.drop_frame ? " DF" : ""}`;
}

export function App(): ReactElement {
  const [stage, setStage] = useState<Stage>("import");

  const [client, setClient] = useState<LauraClient | null>(null);
  const [offline, setOffline] = useState(false);
  const [health, setHealth] = useState<Health | null>(null);

  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useState<string | null>(null);
  const [assets, setAssets] = useState<Asset[]>([]);
  const [selectedAssetId, setSelectedAssetId] = useState<string | null>(null);
  const [roughCut, setRoughCut] = useState<Timeline | null>(null);
  const [selectedClipId, setSelectedClipId] = useState<string | null>(null);

  // A single seek request the Player consumes; a fresh object re-triggers it.
  const [seek, setSeek] = useState<{ frame: number } | null>(null);
  const [currentFrame, setCurrentFrame] = useState(0);

  const [name, setName] = useState("");
  const [presetIdx, setPresetIdx] = useState(3);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const [semantic, setSemantic] = useState(false);
  const [searchResults, setSearchResults] = useState<SearchResult[]>([]);
  const [buildResult, setBuildResult] = useState<{ kept: number; dropped: number } | null>(null);

  const detailAsset = assets.find((a) => a.id === selectedAssetId) ?? null;
  const analysis = useAnalysis(client, detailAsset);

  const selectedClip = roughCut?.clips.find((c) => c.id === selectedClipId) ?? null;
  const selectedClipAsset = selectedClip
    ? assets.find((a) => a.id === selectedClip.asset_id) ?? null
    : null;

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      // Guard against a missing/throwing preload bridge: fall to offline instead of
      // hanging on the "connecting" state forever.
      const info = window.laura
        ? await window.laura.getServiceInfo().catch(() => null)
        : null;
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

  const loadAssets = useCallback(async (c: LauraClient, projectId: string) => {
    setAssets(await c.listAssets(projectId));
  }, []);

  // One rough cut per video: load the SELECTED asset's rough cut (created on demand,
  // linked via created_from=asset_id) so switching videos shows that video's scenes.
  const loadRoughCut = useCallback(
    async (c: LauraClient, projectId: string, assetId: string) => {
      setRoughCut(await c.getAssetRoughCut(projectId, assetId));
    },
    [],
  );

  const reloadRoughCut = useCallback(() => {
    if (client && selectedProjectId && selectedAssetId) {
      void loadRoughCut(client, selectedProjectId, selectedAssetId).catch((e) =>
        setError(String(e)),
      );
    }
  }, [client, selectedProjectId, selectedAssetId, loadRoughCut]);

  // Re-load the rough cut whenever the selected video changes (or clear it if none).
  useEffect(() => {
    if (client && selectedProjectId && selectedAssetId) {
      void loadRoughCut(client, selectedProjectId, selectedAssetId).catch((e) =>
        setError(String(e)),
      );
    } else {
      setRoughCut(null);
    }
  }, [client, selectedProjectId, selectedAssetId, loadRoughCut]);

  const seekToFrame = useCallback((frame: number) => setSeek({ frame }), []);

  const importPaths = useCallback(
    async (paths: string[]): Promise<string[]> => {
      if (!client || !selectedProjectId) return [];
      const ids: string[] = [];
      for (const p of paths) ids.push((await client.importAsset(selectedProjectId, p)).asset_id);
      return ids;
    },
    [client, selectedProjectId],
  );

  const importUrls = useCallback(
    async (urls: string[], opts: UrlImportOptions = {}): Promise<string[]> => {
      if (!client || !selectedProjectId) return [];
      const ids: string[] = [];
      for (const u of urls) {
        // A playlist/channel URL returns one primary asset plus extra_asset_ids.
        const accepted = await client.importAssetFromUrl(selectedProjectId, u, opts);
        ids.push(accepted.asset_id, ...accepted.extra_asset_ids);
      }
      return ids;
    },
    [client, selectedProjectId],
  );

  const runImport = useCallback(
    async (paths: string[], urls: string[], opts: UrlImportOptions = {}): Promise<void> => {
      if (!client || !selectedProjectId) return;
      try {
        const ids = [...(await importPaths(paths)), ...(await importUrls(urls, opts))];
        await loadAssets(client, selectedProjectId);
        if (ids[0]) setSelectedAssetId(ids[0]);
      } catch (e) {
        setError(String(e));
      }
    },
    [client, selectedProjectId, importPaths, importUrls, loadAssets],
  );

  const onDropImport = useCallback(
    (r: ResolvedImport): void => {
      void runImport(r.paths, r.urls);
    },
    [runImport],
  );

  // Show the clip's source asset and seek the player to its frame.
  const previewClip = useCallback((assetId: string, frame: number) => {
    setSelectedAssetId(assetId);
    setSeek({ frame });
  }, []);

  async function selectProject(id: string): Promise<void> {
    setSelectedProjectId(id);
    setSelectedAssetId(null);
    setAssets([]);
    setRoughCut(null);
    setSelectedClipId(null);
    setSeek(null);
    setBuildResult(null);
    if (client) {
      try {
        await loadAssets(client, id);
        // rough cut loads per selected video (see the effect above)
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

  async function onBuildFromShots(): Promise<void> {
    if (!client || !selectedProjectId || !detailAsset) return;
    setError(null);
    try {
      // Non-destructive: fill the current rough cut only if it's empty, else make a new one.
      const fillId = roughCut && roughCut.clips.length === 0 ? roughCut.id : undefined;
      const res = await client.buildRoughCutFromShots(selectedProjectId, detailAsset.id, fillId);
      setRoughCut(res.timeline);
      setBuildResult({ kept: res.timeline.clips.length, dropped: res.dropped.length });
    } catch (e) {
      setError(String(e));
    }
  }

  async function onAppendShot(shot: Shot): Promise<void> {
    if (!client || !roughCut || !detailAsset) return;
    try {
      await client.applyOperation(roughCut.id, {
        op: "append_clip",
        asset_id: detailAsset.id,
        src_in_frame: shot.src_in_frame,
        src_out_frame_exclusive: shot.src_out_frame_exclusive,
      });
      reloadRoughCut();
    } catch (e) {
      setError(String(e));
    }
  }

  async function onAppendSegment(seg: Segment): Promise<void> {
    if (!client || !roughCut || seg.words.length === 0) return;
    try {
      await client.applyOperation(roughCut.id, {
        op: "append_from_words",
        word_start_id: seg.words[0].id,
        word_end_id: seg.words[seg.words.length - 1].id,
      });
      reloadRoughCut();
    } catch (e) {
      setError(String(e));
    }
  }

  return (
    <div className="flex h-full flex-col">
      {/* Import drag-and-drop only in Import/Download. Its window-level dragover
          listener otherwise pops the full-screen import overlay during clip/scene
          drags in Rough Cut / Feinschnitt / Zusammenfügen. */}
      {(stage === "import" || stage === "download") && <DropZone onImport={onDropImport} />}
      <header className="flex items-center justify-between border-b border-edge bg-panel px-5 py-3">
        <div className="flex items-baseline gap-3">
          <h1 className="text-lg font-semibold tracking-tight text-white">Laura</h1>
          <span className="text-xs text-slate-400">frame-genauer KI-Filmschnitt · local-first</span>
        </div>
        {client && (
          <div className="flex items-center gap-2">
            <select
              value={selectedProjectId ?? ""}
              onChange={(e) => {
                if (e.target.value) void selectProject(e.target.value);
              }}
              disabled={projects.length === 0}
              aria-label="Projekt wählen"
              className="max-w-[12rem] rounded bg-slate-800 px-2 py-1 text-xs text-slate-100 disabled:opacity-40"
            >
              <option value="">{projects.length ? "— Projekt wählen —" : "Kein Projekt"}</option>
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
            <form onSubmit={(e) => void onCreateProject(e)} className="flex items-center gap-1">
              <input
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="Neues Projekt…"
                aria-label="Neuer Projektname"
                className="w-36 rounded bg-slate-800 px-2 py-1 text-xs text-slate-100"
              />
              <select
                value={presetIdx}
                onChange={(e) => setPresetIdx(Number(e.target.value))}
                aria-label="Framerate"
                className="rounded bg-slate-800 px-1 py-1 text-xs text-slate-100"
              >
                {FPS_PRESETS.map((p) => (
                  <option key={`${p.num}-${p.den}-${String(p.drop)}`} value={FPS_PRESETS.indexOf(p)}>
                    {`${Math.round((p.num / p.den) * 100) / 100}${p.drop ? " DF" : ""}`}
                  </option>
                ))}
              </select>
              <button
                type="submit"
                disabled={!name.trim() || busy}
                className="rounded bg-sky-600 px-2 py-1 text-xs font-medium text-white hover:bg-sky-500 disabled:opacity-40"
              >
                + Anlegen
              </button>
            </form>
          </div>
        )}
        <HealthBadge health={health} offline={offline} />
      </header>

      {error && (
        <div className="border-b border-red-900 bg-red-950/60 px-5 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <div className="flex min-h-0 flex-1">
        <NavRail active={stage} onSelect={setStage} />

        {client && (
          <MediaSidebar
            client={client}
            assets={assets}
            selectedAssetId={selectedAssetId}
            onSelect={setSelectedAssetId}
          />
        )}

        <div className="flex min-h-0 flex-1 flex-col">
          {stage === "download" && (client ? (
            <DownloadView
              client={client}
              disabled={!selectedProjectId}
              assets={assets}
              onUrl={(u) => void runImport([], [u])}
            />
          ) : (
            <div className="flex flex-1 items-center justify-center text-sm text-slate-600">Service offline — starte den lokalen Server.</div>
          ))}

          {stage === "import" && (client ? (
            <ImportView
              client={client}
              disabled={!selectedProjectId}
              assets={assets}
              onUrls={(req) =>
                void runImport([], req.urls, {
                  format: req.format,
                  cookiesFromBrowser: req.cookiesFromBrowser ?? undefined,
                })
              }
              onPickFiles={() => {
                void (async () => {
                  try {
                    const f = await window.laura.pickMediaFiles();
                    if (f.length) await runImport(f, []);
                  } catch (e) {
                    setError(String(e));
                  }
                })();
              }}
              onPickFolder={() => {
                void (async () => {
                  try {
                    const folder = await window.laura.pickFolder();
                    if (folder) await runImport(await window.laura.listMediaInFolder(folder), []);
                  } catch (e) {
                    setError(String(e));
                  }
                })();
              }}
            />
          ) : (
            <div className="flex flex-1 items-center justify-center text-sm text-slate-600">Service offline — starte den lokalen Server.</div>
          ))}

          {stage === "roughcut" && client && (
            <RoughCutView
              client={client}
              projectId={selectedProjectId}
              asset={detailAsset}
              roughCut={roughCut}
              segments={analysis.segments}
              onRoughCutChange={async () => {
                if (client && selectedProjectId && selectedAssetId)
                  await loadRoughCut(client, selectedProjectId, selectedAssetId);
              }}
              seek={seek}
              currentFrame={currentFrame}
              onSeek={seekToFrame}
              onFrame={(f) => setCurrentFrame(f)}
            />
          )}

          {stage === "finecut" && client && (
            <FineCutView
              client={client}
              asset={detailAsset}
              roughCutId={roughCut?.id ?? null}
              segments={analysis.segments}
              currentFrame={currentFrame}
              seek={seek}
              onSeek={seekToFrame}
              onFrame={(f) => setCurrentFrame(f)}
            />
          )}

          {stage === "assemble" && client && (
            <AssembleView
              client={client}
              projectId={selectedProjectId}
              roughCutId={roughCut?.id ?? null}
              onSeekScene={() => undefined}
            />
          )}

          {/* TODO(zusammenfuegen): dead legacy 4-zone layout — remove once unused handlers are pruned */}
          {(false as boolean) && (
            <>
              <main className="grid min-h-0 flex-1 grid-cols-[260px_1fr_340px] gap-px overflow-hidden bg-edge">
                {/* Library: projects + media */}
                <section className="flex flex-col overflow-hidden bg-ink">
                  <div className="flex min-h-0 flex-1 flex-col border-b border-edge">
                    <h2 className="px-4 pb-2 pt-4 text-xs font-medium uppercase tracking-wide text-slate-500">
                      Projekte
                    </h2>
                    <ul className="min-h-0 flex-1 space-y-1 overflow-auto px-3">
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
                    <form onSubmit={onCreateProject} className="space-y-2 p-3">
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
                  </div>

                  <div className="flex min-h-0 flex-1 flex-col">
                    <div className="flex items-center justify-between px-4 pb-2 pt-3">
                      <h2 className="text-xs font-medium uppercase tracking-wide text-slate-500">Medien</h2>
                    </div>
                    <div className="px-3 pb-2">
                      <ImportBar
                        disabled={!selectedProjectId}
                        onUrls={(req) =>
                          void runImport([], req.urls, {
                            format: req.format,
                            cookiesFromBrowser: req.cookiesFromBrowser ?? undefined,
                          })
                        }
                        onPickFiles={() => {
                          void (async () => {
                            try {
                              const files = await window.laura.pickMediaFiles();
                              if (files.length > 0) await runImport(files, []);
                            } catch (e) {
                              setError(String(e));
                            }
                          })();
                        }}
                        onPickFolder={() => {
                          void (async () => {
                            try {
                              const folder = await window.laura.pickFolder();
                              if (folder) await runImport(await window.laura.listMediaInFolder(folder), []);
                            } catch (e) {
                              setError(String(e));
                            }
                          })();
                        }}
                      />
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
                    <ul className="min-h-0 flex-1 space-y-1 overflow-auto px-3 pb-3">
                      {!selectedProjectId && (
                        <li className="px-1 py-2 text-xs text-slate-600">Wähle ein Projekt.</li>
                      )}
                      {selectedProjectId && assets.length === 0 && (
                        <li className="px-1 py-2 text-xs text-slate-600">Noch keine Medien importiert.</li>
                      )}
                      {assets.map((a) => (
                        <li key={a.id} className="flex flex-col gap-0.5">
                          <div className="flex items-center gap-1">
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
                          </div>
                          {client && !isImportSettled(a) && (
                            <AssetImportRow client={client} assetId={a.id} />
                          )}
                        </li>
                      ))}
                    </ul>
                  </div>
                </section>

                {/* Preview: the source player */}
                <section className="flex flex-col overflow-auto bg-ink p-4">
                  {client && detailAsset ? (
                    <Player asset={detailAsset} seekTo={seek} onFrame={setCurrentFrame} />
                  ) : (
                    <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
                      Wähle ein Medium oder importiere eines.
                    </div>
                  )}
                </section>

                {/* Inspector: frame-accurate scene editor when a clip is selected, else
                    analysis + metadata for the previewed asset. */}
                <section className="flex flex-col overflow-hidden bg-ink">
                  {client && selectedClip && selectedClipAsset ? (
                    <SceneInspector
                      client={client}
                      clip={selectedClip}
                      asset={selectedClipAsset}
                      timelineId={roughCut!.id}
                      onChange={reloadRoughCut}
                      onSeek={seekToFrame}
                    />
                  ) : client && detailAsset ? (
                    <InspectorPanel
                      client={client}
                      asset={detailAsset}
                      analysis={analysis}
                      canAppend={roughCut != null}
                      onAppendShot={(s) => void onAppendShot(s)}
                      onBuildFromShots={() => void onBuildFromShots()}
                      buildResult={buildResult}
                    />
                  ) : (
                    <div className="flex flex-1 items-center justify-center p-4 text-center text-sm text-slate-600">
                      Inspector — wähle ein Medium.
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
                  onSelect={setSelectedClipId}
                />
              )}

              <TranscriptBar
                client={client}
                assetId={detailAsset?.id ?? null}
                assetName={detailAsset?.display_name ?? null}
                segments={analysis.segments}
                note={analysis.note}
                currentFrame={currentFrame}
                onSeek={seekToFrame}
                canAppend={roughCut != null}
                onAppendSegment={(s) => void onAppendSegment(s)}
              />
            </>
          )}

          {stage === "export" &&
            (client ? (
              <ExportView
                client={client}
                projectId={selectedProjectId}
                timelineId={roughCut?.id ?? null}
              />
            ) : (
              <div className="flex flex-1 items-center justify-center text-sm text-slate-600">
                Service offline — starte den lokalen Server.
              </div>
            ))}
        </div>
      </div>
    </div>
  );
}

function isImportSettled(asset: Asset): boolean {
  return asset.files?.some((f) => f.kind === "waveform" || f.kind === "proxy") ?? false;
}

function AssetImportRow({
  client,
  assetId,
}: {
  client: LauraClient;
  assetId: string;
}): ReactElement | null {
  const status = useImportStatus(client, assetId);
  if (!status) return null;
  return (
    <div className="px-3 pb-1">
      <ImportProgress status={status} onRetry={() => void client.retryImport(assetId)} />
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
