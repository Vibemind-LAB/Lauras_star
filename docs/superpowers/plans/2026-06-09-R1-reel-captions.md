# R1 — Reel-Captions (eingebrannte Wort-Captions) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Checkbox-getrackt.
> **Kollisions-Regeln (unverändert):** Pathspec-Commits nur mit explizitem Pfad; **niemals
> `api/timelines.py`, `RoughCutView.tsx`, `FineCutView.tsx`, `analysis/refine.py`, `shots.py`, `Player.tsx`**
> anfassen; uv.lock/.claude/build nie stagen; Subagenten committen nicht. Design-Quelle:
> [`docs/superpowers/specs/2026-06-09-reel-production-track.md`](../specs/2026-06-09-reel-production-track.md) §B (user-abgenommen).

**Goal:** Wort-genaue, eingebrannte Captions im Reel — Karaoke-Highlight (`\kf`) über ASS/libass,
in den bestehenden 9:16-Filtergraph (`ass=`). GPU-frei, **keine neue schwere Dep** fürs Skelett
(Wort-Timings kommen aus den bereits existierenden Transcript-`segments`).

**Architecture:** Additive Erweiterung des R0-Renderpfads. Reiner Builder `render/captions.py`
(`build_ass(lines, …) -> str`) erzeugt eine ASS-Datei (PlayRes 1080×1920). `render_clips_mp4` bekommt
ein optionales `caption_ass: str | None`; ist es gesetzt, wird die ASS in `dest.parent` geschrieben und
per **`ass=<basename>`** an die Reel-Chain gehängt (gleiche **cwd/Basename**-Behandlung wie R0.8
`textfile=` — der Windows-Drive-Colon-Bug betrifft Filter-Dateipfade generell). Defaults aus ⇒ R0
unverändert.

**Tech Stack:** Python 3.11/uv/pytest/ffmpeg (`ass`/libass) · React/TS · Verifikation: `uv run pytest`
+ echter ffmpeg-`ass=`-Render + ffprobe.

**Invarianten:** **Caption-Timing als Ganzzahl-Frames** im Zustand; ASS-Centisekunden nur beim
Formatieren (`build_ass`-Rand). **ASS ist Export-Artefakt, nie Projektzustand** (analog OTIO/SRT).
Idempotenz über den bestehenden `render:{export_id}`-Key. Whisper/WhisperX bleiben optionale Extras —
das Skelett konsumiert nur schon vorhandene `segments`.

---

## File Structure
- **Create:** `services/local-api/src/laura/render/captions.py` (reiner ASS-Builder) ·
  `tests/test_captions_ass.py` (Unit) · `tests/test_caption_render.py` (echter ffmpeg-`ass=`-Render)
- **Modify (alles meins):** `render/mp4.py` (optionales `caption_ass`-Kwarg + ASS-Datei schreiben/cwd/cleanup)
- **Später (R1.3+, UX-Entscheidungen — PAUSE für User):** `db` options.captions · `api/reels.py` ·
  `apps/desktop/src/api.ts` + `ExportView.tsx` · Segment→Lines-Mapping.

---

## Task R1.1 — Reiner ASS-Karaoke-Builder
**Files:** Create `render/captions.py`, `tests/test_captions_ass.py`

Datentyp: ein „Wort" ist `tuple[str, int, int]` = `(text, start_frame, end_frame)` (end-exclusive,
Ganzzahl-Frames). Eine „Zeile" = `list[Word]` (eine Dialogue-Event-Zeile). Eingabe = `list[Line]`.

- [ ] **Step 1 — Failing test** (`tests/test_captions_ass.py`):
```python
from laura.render.captions import build_ass

def test_header_has_playres():
    ass = build_ass([], rate_num=30, rate_den=1)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "[Events]" in ass

def test_one_line_two_words_karaoke():
    ass = build_ass([[("Hallo", 0, 15), ("Welt", 15, 30)]], rate_num=30, rate_den=1)
    # eine Dialogue-Zeile, zwei \kf-Tags, korrekte Start/End-Zeit (0..1s)
    dlg = [ln for ln in ass.splitlines() if ln.startswith("Dialogue:")]
    assert len(dlg) == 1
    assert dlg[0].count("\\kf") == 2
    assert "0:00:00.00" in dlg[0] and "0:00:01.00" in dlg[0]

def test_kf_duration_centiseconds():
    # 15 Frames @30fps = 0.5s = 50 centiseconds
    ass = build_ass([[("a", 0, 15)]], rate_num=30, rate_den=1)
    assert "\\kf50}a" in ass

def test_brace_is_escaped():
    ass = build_ass([[("{x}", 0, 30)]], rate_num=30, rate_den=1)
    assert "{x}" not in ass.split("[Events]")[1]  # raw braces never reach text field
```
- [ ] **Step 2 — Run, expect fail** (ImportError).
- [ ] **Step 3 — Implement** `render/captions.py`:
  - `build_ass(lines, *, rate_num, rate_den, play_w=1080, play_h=1920, fontsize=72, margin_v=250) -> str`.
  - Header: `[Script Info]` (ScriptType v4.00+, PlayResX/Y, WrapStyle 2), `[V4+ Styles]` (ein `Reel`-Style:
    Arial, bold, weiße PrimaryColour, gefärbte SecondaryColour fürs Karaoke-Highlight, Outline+Shadow,
    Alignment 2 (unten-mittig), MarginV=`margin_v`), `[Events]` mit Format-Zeile.
  - Pro Zeile eine `Dialogue:`-Event: Start = erstes Wort `start_frame`, End = letztes Wort `end_frame`,
    Text = je Wort `{\kf<cs>}<escaped_word> ` wobei `cs = round((end-start)*rate_den/rate_num*100)`.
  - Zeit-Format `h:mm:ss.cc` (Centisekunden) aus `frame*rate_den/rate_num` — **nur hier** wird aus Frames
    Zeit; Frames bleiben sonst Ganzzahl.
  - ASS-Text-Escaping: `\n`→`\N`; `{`→`(`, `}`→`)` (rohe Braces würden Override-Blöcke öffnen);
    Backslash im Wort → entfernen/ersetzen. Leere `lines` ⇒ gültige ASS ohne Dialogue.
- [ ] **Step 4 — Run, expect pass.**
- [ ] **Step 5 — Commit:** `git commit -m "feat(reel): pure ASS karaoke caption builder [R1.1]" -- services/local-api/src/laura/render/captions.py services/local-api/tests/test_captions_ass.py`

## Task R1.2 — `render_clips_mp4`: `ass=` einbrennen (cwd/Basename) + echter Render
**Files:** Modify `render/mp4.py`; Create `tests/test_caption_render.py`

- [ ] **Step 1 — Failing integration test** (`tests/test_caption_render.py`, skip ohne ffmpeg/ffprobe):
  baue 1s-Testclip, `ass = build_ass([[("Hallo",0,15),("Welt",15,30)]], rate_num=25, rate_den=1)`,
  rufe `render_clips_mp4([(fix,0,25)], out, rate_num=25, rate_den=1, vertical=True, caption_ass=ass)`,
  dann ffprobe → 1080×1920, `out.exists()`, und **keine** `*.reel_*.ass`/`.txt`-Leichen in `tmp_path`.
- [ ] **Step 2 — Run, expect fail** (TypeError: unexpected kwarg `caption_ass`).
- [ ] **Step 3 — Implement:** Signatur + Kwarg `caption_ass: str | None = None`. Wenn gesetzt: ASS-Inhalt
  nach `dest.parent / f"{dest.stem}.reel_caption.ass"` (UTF-8) schreiben, in `reel_files` aufnehmen (gleicher
  `finally`-Cleanup), und an die Reel-Chain `,ass=<basename>` anhängen (libass). Da Captions impliziert
  Reel-Verpackung, läuft der Pfad bereits mit `cwd=dest.parent` (R0.8) — **Basename genügt, kein
  Path-Escaping** (empirisch bewiesen für Filter-Dateipfade). Reel-Chain leer + nur Captions: dann muss
  trotzdem ein `[vcat]…[out]`-Zweig mit `ass=` entstehen — sicherstellen, dass `concat_out`-Logik das
  abdeckt (Captions zählen wie „reel vorhanden"). Defaults aus ⇒ unverändert.
- [ ] **Step 4 — Run, expect pass** (echter `ass=`-Render, 1080×1920, Cleanup ok).
- [ ] **Step 5 — Commit** (`render/mp4.py`, `tests/test_caption_render.py`).

## Task R1.3+ — UX/Integration (PAUSE für User-Entscheidungen)
Nicht autonom bauen — offene Entscheidungen dem User vorlegen:
1. **Captions immer an vs. Toggle** im Reel-Preset? Karaoke-Highlight vs. simples Wort-Pop?
2. **Wort-Quelle:** Transcript-`segments` der Sequenz/Szene → Zeilen-Gruppierung (wie viele Wörter pro
   Zeile, Umbruch-Heuristik)? Mapping src→seq über die bestehende Clip-Projektion (wie in TimelineBar).
3. **Persistenz:** `options.captions` (bool) + ggf. Stil — Endpoint `render-reel` erweitern (eigener Router).
4. **Frontend:** „Captions"-Toggle in ExportView.

---

## Risiken / De-Risk
- **`ass=` Windows-Pfad** = derselbe Drive-Colon-Bug wie R0.8 `textfile=`; **gelöst** durch cwd+Basename
  (bereits bewiesen). R1.2 nutzt denselben Mechanismus.
- **ASS-Escaping** (`{}`/Backslash/Newline) → Builder + Unit-Test decken es ab; rohe Braces nie ins Textfeld.
- **Filtergraph-Verzweigung** wenn *nur* Captions (ohne vertical/hook): Concat→`[vcat]`→`ass`→`[out]` muss
  greifen — Test deckt `vertical=True` ab; reiner-Caption-Fall in R1.2 mit-testen.
- Forced-Alignment-Qualität (Drift ohne WhisperX) ist **R1.3+**-Thema (Wort-Quelle), nicht im Skelett.
