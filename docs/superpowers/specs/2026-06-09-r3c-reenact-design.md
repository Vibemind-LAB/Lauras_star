# R3-C — Reenact (konsentiert, gekennzeichnet) — Design

> Zweites Bau-Teil des **R3-Programms**. Setzt auf die fertige **Replace-Overlay-Primitive**
> (`2026-06-09-replacement-lane-*`) auf. Brainstorming-Entscheidungen (2026-06-09): Reenact zuerst ·
> separater **HTTP-Sidecar** · **timeline-range-getrieben** (Ergebnis ersetzt die Quelle der Range) ·
> Platzierung über die Replace-Overlay-Primitive.

## Kernprinzip (nicht verhandelbar)

Laura baut **kein ungekennzeichnetes/unkonsentiertes** Deepfake-Werkzeug. Jeder Reenact-Job ist
**hart gegated**: er verweigert ohne gültigen **Consent-Record**, und sein Output ist als
`synthetic` markiert (sichtbares „KI"-Burn-in aus R0 + später Pixel-Wasserzeichen). Das gilt schon
für das Skelett mit Stub-Backend.

## Scope dieses Specs

**Dep-freies Skelett (autonom baubar, voll testbar):** Datenmodell, Consent-Gate, `ai.reenact`-Job,
pluggbares `ReenactBackend`-Interface + **Stub-Backend** (kein echtes Modell), Output→synthetic-Asset
→ Platzierung als Replace-Overlay, API, minimale UI.

**NICHT hier (eigene, blockierte Teile):** das echte **LivePortrait-Backend** (schwere GPU-Dep,
Sidecar-HTTP, Modell-Download — braucht User-Install); Face-Probe; 2. Kennzeichnungs-Ebene
(Video Seal/C2PA); Swap-Backend; Qualitäts-/Eval-Pipeline (LSE/ArcFace/LPIPS).

## Architektur

```
UI: Driving-Range (timeline + seq[in,out)) + Ziel-Portrait-Asset + Consent bestätigen
  → POST /timelines/{id}/reenact  (legt Job an, NUR mit consent_id)
    → Job ai.reenact:
        1. Consent prüfen (sonst Fehler)
        2. Driving-Frames der Range dekodieren (frame-genau: src + fps an der Grenze)
        3. ReenactBackend.reenact(driving, portrait, out, params)   ← Stub ODER LivePortrait
        4. ffprobe(out) → add_asset(synthetic=true, ai_effect='reenact')
        5. Replace-Overlay (RL-Primitive) auf die Driving-Range setzen → Asset erscheint im Schnitt
  → UI: synthetisches Ergebnis liegt auf der V2/Replace-Lane über der Range
```

Die **Black-Box-Grenze** (ai-effects-Prinzip): Laura übergibt Ganzzahl-Frame-Range + Quelle + fps,
der Backend liefert eine Mediendatei, Laura re-probt → Asset. Lauras Zustand bleibt im Frame-Raum.

## Pluggbares Backend-Interface

```python
class ReenactBackend(Protocol):
    name: str
    def available(self) -> bool: ...                      # False ⇒ UI sagt „nicht installiert"
    def reenact(self, *, driving_path: Path, portrait_path: Path,
                out_path: Path, fps_num: int, fps_den: int) -> None: ...
```
- **StubReenactBackend** (dep-frei, Default im Skelett): erzeugt ein deterministisches Platzhalter-MP4
  aus dem Driving-Clip — z. B. Driving + sichtbares „REENACT (stub)"-Overlay/Tönung — damit die ganze
  Pipeline (Job→Asset→Overlay) end-to-end läuft. **Bewusst offensichtlich kein echter Deepfake.**
- **LivePortraitBackend** (später, optionales Extra `[ai-reenact]`): ruft den lokalen Sidecar
  (`POST http://127.0.0.1:<port>/reenact`), Modell bleibt warm. `available()` = Sidecar erreichbar.
  Auswahl per `LAURA_REENACT_BACKEND` env / Job-Param; Default `stub`.

## Datenmodell (additiv, meins)

- `media_assets`: **+ `synthetic INTEGER DEFAULT 0`**, **+ `ai_effect TEXT`** (Provenienz, z. B. 'reenact').
- Neue Tabelle **`consent_records`**: `id, project_id, subject_label, source_asset_id (nullable),
  confirmed_by, confirmed_at, note`. Repos: `create_consent_record`, `get_consent_record`, `list_…`.
- Job-Kind `ai.reenact` (bestehende Queue). Payload: `{timeline_id, seq_in_frame, seq_out_frame_exclusive,
  portrait_asset_id, consent_id, backend}`.
- Idempotenz: `reenact:{job-params-hash}`.

## Consent-Gate (Pflicht)

- `POST /projects/{id}/consent` legt einen Consent-Record an (subject_label + Bestätigung).
- `POST /timelines/{id}/reenact` **erfordert** `consent_id`; der Handler lädt den Record und **bricht
  ab (Fehlerstatus)**, wenn er fehlt/ungültig ist. Kein Bypass.
- Output-Asset `synthetic=true`; Export-Disclosure (R0) brennt sichtbares Label ein.

## Render-/Placement-Andockpunkt

Schritt 5 nutzt die **fertige Replace-Overlay-API** (`POST /timelines/{id}/overlays`,
`role='replace'`): das reenactete Asset deckt die Driving-Range 1:1 (`src[0,len)`), zeit-aligned.
Keine neue Platzierungslogik. Render zeigt es über die `resolve_clip_rows`-Präzedenz.

## API / UI (minimal)

- API: `create_consent`, `reenact` (oben).
- UI: kleine `ReenactPanel`-Komponente (in AssembleView, wie OverlayControls): Driving-Range-Felder +
  Portrait-Asset-Auswahl + Consent-Bestätigung (subject_label) → Button „Reenact (stub)". Zeigt Job-Status.
  Backend-Auswahl: solange nur Stub verfügbar, fix „stub" + Hinweis „LivePortrait nicht installiert".

## Invarianten

Ganzzahl-Frames (Range), end-exclusive; OTIO=Wahrheit (Reenact-Asset = normales synthetisches Asset,
platziert via Overlay-Track); schwere Modelle = optionales Extra, Backend startet ohne; Consent
nicht-umgehbar; `timelines.py` (User) unberührt — separate Endpoints.

## Testing / Evaluation (Skelett-Stufe)

- Consent-Gate: `reenact` ohne/mit ungültigem `consent_id` → Fehler; mit gültigem → Job läuft (pytest).
- Stub-Job e2e: echtes ffmpeg, Driving-Range → Stub-Output → `synthetic`-Asset → Replace-Overlay gesetzt
  → `resolve_clip_rows` zeigt es in der Range (ffprobe + Präsenz-Check). 
- `synthetic`/`ai_effect`-Round-Trip; Backend-`available()`-Schalter (Stub immer true).
- API/UI: consent + reenact-Endpoint (TestClient); tsc.
- **„Evaluiert" auf Skelett-Ebene = volle Suite + e2e grün.** Die echte Qualitäts-Eval (LSE/ArcFace/
  LPIPS/Human-MOS) kommt mit dem LivePortrait-Backend (eigenes Teil).

## Heavy-Dep-Wand (hier pausiere ich)

`LivePortraitBackend` + Sidecar + Modell-Download (RTX 3060, MIT-Lizenz, ~GB Gewichte) = **User-Install**.
Das Skelett ist so gebaut, dass dieser Schritt ein reiner **Adapter-Tausch** ist (Interface + Sidecar-
Contract stehen), kein Umbau.
