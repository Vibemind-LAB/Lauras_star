# ComfyUI-Rollout — was lokal geht (12 GB) und der Plan für „den Rest"

- **Datum:** 2026-06-23
- **Hardware hier:** NVIDIA RTX 3060, **12 GB** VRAM · C: ~4 GB frei (voll) · **E: ~1,49 TB frei** → ComfyUI + Modelle liegen auf **`E:\ComfyUI`**.
- **Kontext:** Ergänzung zu [comfyui-fit.md](comfyui-fit.md). Trennt das, was die 12-GB-Karte *jetzt* kann, von dem, was 24-GB/Cloud bzw. echten Bau-Aufwand braucht.

## A. Was lokal mit 12 GB geht (wird jetzt umgesetzt)

| Fähigkeit | Modell (12-GB-tauglich) | Status |
|---|---|---|
| **Bild-Generierung** (Stills, Title-Cards, B-Roll-Frames) | SDXL (~6,5 GB), Flux-GGUF | machbar, sehr zuverlässig |
| **Kurzes KI-Video** (txt2vid / img2vid) | **LTX-Video** (für ~12 GB ausgelegt; t5xxl_fp8 statt fp16) | machbar, kurze/kleine Clips |
| **Portrait-Reenact** | LivePortrait (~8 GB) | machbar (braucht Portrait + Driving-Clip) |
| **Lipsync** | Wav2Lip / MuseTalk (~4–8 GB) | machbar |

Setup: `E:\ComfyUI` (geklont), eigenes venv mit torch cu128, als **standalone HTTP-Server** (`:8188`)
betrieben — Out-of-Process, GPL-sauber. Ergebnis dieses /loop wird hier unter „Ergebnis" ergänzt.

## B. Der Rest — als Plan festgehalten (deferred)

### B1. Hochwertiges generatives KI-Video (braucht > 12 GB)
- **Modelle:** Wan 2.2, HunyuanVideo (Qualitätsliga) — wollen real **24 GB**.
- **Weg:** entweder **lokale 24-GB-Karte** (gebrauchte RTX 3090 ~) ODER **Cloud**:
  - **Comfy Cloud** ($20–100/mo, gleiche `/prompt`-API → keine Code-Änderung nötig).
  - **Gemietete GPU** (RunPod/Vast.ai, 24-GB 3090/4090 ~0,3–0,5 $/h): ComfyUI-Image starten, Laura/Client auf die URL zeigen.
- **Wichtig:** Workflow-Schicht ist identisch zur lokalen — nur `base_url` zeigt woanders hin. Kein Rewrite.

### B2. Laura ↔ ComfyUI-Integration (Bau-Aufwand, GPU-unabhängig)
Aus [comfyui-fit.md](comfyui-fit.md), in Reihenfolge des Hebels:
1. **`reenact`-Slot via `ComfyUIBackend`** (höchster Hebel — Plumbing existiert, ersetzt heutigen Stub):
   - `RuntimeEffect` ggf. erweitern (`api/models.py:895`), ComfyUI-`ai_runtime`-Zeile (`external_http`/`container`),
   - `ComfyUIBackend` (stdlib `urllib`: `available()` via `/system_stats`, generate via `POST /prompt` → Poll `/history` → `/view`) + Stub-Fallback + Resolver-Branch,
   - `handle_reenact` nutzt es über `_backend_config_from_runtime`, Kind auf `analysis.gpu` geroutet.
   - **Koordination nötig:** AI-Runtime-Subtree (`services/ai-runtimes/`, `ai/runtime_*`, `api/ai_runtimes.py`) ist parallele codex-Arbeit.
2. **Generative B-Roll / Title-Cards** als **neuer Effekt** (Greenfield, höchster *einzigartiger* Wert): neuer Effekt + UI-Fläche + Bild/Video-Workflows.
3. **`lipsync` via ComfyUI** (optional; dedizierter Wav2Lip-Sidecar ist Alternative).

### B3. Härtung (vor jedem Ausliefern)
- ComfyUI-Commit + Custom-Node-Versionen **pinnen**; Workflows als versionierte Assets; beim Upgrade gegen `/object_info` validieren.
- Custom-Node-Registry **untrusted** behandeln (kein Runtime-Auto-Install).
- GPL-3.0: **nur Out-of-Process** (nie importieren/bündeln). Rechtsprüfung vor Auslieferung.
- EU-AI-Act: generierte Medien `synthetic=True` + Provenance (reenact macht das schon).

## Ergebnis (lokal, dieser /loop) — ✅ Video produziert

Auf der RTX 3060 (12 GB) wurde ein echtes KI-Video generiert:
- **Output:** `Desktop\laura_ltx_clip.webm` (+ `.mp4` transkodiert). ffprobe: **vp9, 704×480, 25 fps, 49 Frames (~2 s)**. Renderzeit **~65 s**.
- **Engine:** ComfyUI 0.26.0 auf `E:\ComfyUI` (eigenes uv-venv, torch 2.11.0+cu128), Server `http://127.0.0.1:8188`.
- **Modelle (auf E:):** `ltxv-2b-0.9.8-distilled.safetensors` (6,0 GB) + `t5xxl_fp8_e4m3fn.safetensors` (4,6 GB).
- **Workflow:** `E:\ComfyUI\laura_gen.py` — API-Format-Workflow, gegen die echten `/object_info`-Schemata dieser Version gebaut (CheckpointLoaderSimple → CLIPLoader(ltxv) → CLIPTextEncode×2 → EmptyLTXVLatentVideo → LTXVConditioning → LTXVScheduler → KSamplerSelect → SamplerCustom(cfg=1) → VAEDecode → SaveWEBM).

**Gelöste Stolpersteine (für Reproduktion wichtig):**
- **Disk voll** (C: 0,15 GB) → torch-CUDA-DLLs konnten nicht committen (`WinError 1455`). Fix: `uv cache clean` → C: 6,6 GB. Modelle/Engine liegen auf **E:** (1,5 TB).
- **Deps:** uv-venv hat kein pip; `-r requirements.txt` mit Positionals gemischt installierte nichts → **sauberer `uv pip install -r requirements.txt`** brachte `safetensors` & Co.; zusätzlich `sqlalchemy`/`alembic`/`comfy-aimdo`/`blake3` (Comfy-Org-Mirror-Produkt-Features).
- **fp8-Compute** nicht verfügbar (`comfy_kitchen` fehlt) → **bf16-distilled-Modell** statt fp8-Modell; T5 als fp8 (passt in ~8 GB freies VRAM).

**Reproduktion:**
```
E:\ComfyUI\.venv\Scripts\python.exe E:\ComfyUI\main.py            # Server (:8188)
E:\ComfyUI\.venv\Scripts\python.exe E:\ComfyUI\laura_gen.py "dein prompt"
# Output: E:\ComfyUI\output\laura_ltx_*.webm
```

**Was 12 GB hier NICHT konnte → im Plan (Abschnitt B):** hochauflösendes/langes KI-Video (Wan/Hunyuan, 24 GB) → Cloud/Miet-GPU; und die Laura-Integration (reenact via ComfyUIBackend) ist Bau-Arbeit, GPU-unabhängig.
