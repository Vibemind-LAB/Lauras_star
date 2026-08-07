# Voice pro Szene Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Jede Script-Zeile bekommt ihren eigenen Voice-Clip; das Videosegment ihrer Szene wird exakt auf die Clip-Länge gebunden — Audio↔Bild-Sync per Konstruktion statt Kapitel-Fenster-Arithmetik.

**Architecture:** `synthesize_script_voice` synthetisiert pro Zeile (Zeilen-Cache auf Platte), konstruiert daraus EINE Gesamtspur mit festen Pausen und einen gemergten Wort-Timings-Sidecar — Renderer, Captions, QA lesen unverändert die Gesamtspur. `build_cutlist` nimmt bei vorhandenen Voice-Segmenten die Clip-Dauern als Segmentdauern; der Legacy-Ein-Spur-Pfad (`segments=None`) bleibt vollständig erhalten.

**Tech Stack:** Python 3.11, ffmpeg/ffprobe (subprocess, wie der bestehende Renderer), pydantic v2, pytest (echte ffmpeg-Fixtures wie in den Render-Tests).

**Spec:** `docs/superpowers/specs/2026-08-06-scene-selection-per-scene-voice-design.md` §5.

## Global Constraints

- `INTER_SCENE_GAP_S = 0.35` — die EINE Pausenkonstante; Audio-Konstruktion und Cutlist rechnen mit exakt derselben.
- Sync-Invariante: Gesamtspur = Clips mit Gap-Stille DAZWISCHEN (`n-1` Gaps); Videosegment i dauert `duration_s(i) + gap` für alle außer dem letzten, das letzte exakt `duration_s(n-1)` — Summe Video == Summe Audio, `-shortest` schneidet nichts ab.
- Timeline-Invarianten: Ganzzahl-Frames, end-exclusive; Sekunden nur als Projektion.
- Tool-Funktionen dürfen NIE raisen — `{"ok": False, "reason": str(exc)[:200]}`.
- Legacy-Verträglichkeit: `VoiceArtifact` mit `segments=None` (jedes bestehende Board) läuft durch `build_cutlist`/Render exakt wie heute; kein bestehender Test darf sich ändern müssen (außer um NEUE Felder zu tolerieren).
- ElevenLabs-Fehler bei einer Zeile: genau 1 Retry, dann harter Tool-Fehler mit Szenen-Referenz (Spec §6).
- Python-Gates aus `services/local-api`: `uv run pytest` (NIE zusätzliches `-q`), bare `uv run mypy`, `uv run ruff check .`.
- Git: explizite `git add <pfade>` (nie `-A`), Conventional Commits, Commit-Message endet mit `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.
- mp3-Dauern sind frame-quantisiert (~26 ms): Toleranzen in Tests ±0.15 s; Frame-Rundung im Cutlist wie bisher über `round(seg_dur_s * fps)`.

---

### Task VS1: voice_concat — Konstruktion der Gesamtspur (pure + ffmpeg)

**Files:**
- Create: `services/local-api/src/laura/short_creator/voice_concat.py`
- Test: `services/local-api/tests/test_voice_concat.py` (neu; erzeugt Mini-MP3s per ffmpeg `sine`-Filter, wie die bestehenden Render-Tests echte Medien bauen)

**Interfaces:**
- Consumes: ffmpeg/ffprobe auf PATH (Projektstandard).
- Produces:
  - `probe_duration_s(path: Path) -> float` (ffprobe)
  - `line_offsets(durations: list[float], gap_s: float) -> list[float]` (pure)
  - `merge_word_timings(per_line_words: list[list[dict[str, Any]]], offsets: list[float]) -> dict[str, Any]` (pure; Ergebnis `{"words": [...]}` im Sidecar-Format von voice.py:116)
  - `concat_with_gaps(clip_paths: list[Path], gap_s: float, out_path: Path) -> None` (ffmpeg; wirft `RuntimeError` bei ffmpeg-Fehler — der Tool-Wrapper in VS2 fängt)

- [ ] **Step 1: Failing Tests**

```python
"""Constructed voice track: offsets, merged sidecar, real ffmpeg concat."""

import subprocess
from pathlib import Path

import pytest

from laura.short_creator.voice_concat import (
    concat_with_gaps,
    line_offsets,
    merge_word_timings,
    probe_duration_s,
)


def _tone(path: Path, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}",
         "-q:a", "9", str(path)],
        check=True, capture_output=True,
    )


def test_line_offsets_cumulative_with_gaps() -> None:
    assert line_offsets([2.0, 3.0, 1.0], gap_s=0.35) == pytest.approx([0.0, 2.35, 5.7])


def test_merge_word_timings_shifts_by_offset() -> None:
    merged = merge_word_timings(
        [
            [{"text": "hallo", "start_s": 0.0, "end_s": 0.4}],
            [{"text": "welt", "start_s": 0.1, "end_s": 0.5}],
        ],
        offsets=[0.0, 2.35],
    )
    assert merged == {
        "words": [
            {"text": "hallo", "start_s": 0.0, "end_s": 0.4},
            {"text": "welt", "start_s": 2.45, "end_s": 2.85},
        ]
    }


def test_merge_skips_missing_line_timings() -> None:
    merged = merge_word_timings([[], [{"text": "x", "start_s": 0.0, "end_s": 0.2}]],
                                offsets=[0.0, 1.0])
    assert merged == {"words": [{"text": "x", "start_s": 1.0, "end_s": 1.2}]}


def test_concat_with_gaps_duration(tmp_path: Path) -> None:
    a, b, out = tmp_path / "a.mp3", tmp_path / "b.mp3", tmp_path / "out.mp3"
    _tone(a, 1.0)
    _tone(b, 0.5)
    concat_with_gaps([a, b], gap_s=0.35, out_path=out)
    total = probe_duration_s(out)
    assert total == pytest.approx(1.0 + 0.35 + 0.5, abs=0.15)
```

- [ ] **Step 2: Fail verifizieren**

Run: `uv run pytest tests/test_voice_concat.py`
Expected: FAIL — Modul existiert nicht.

- [ ] **Step 3: Implementation**

```python
"""Constructed single voice track from per-line clips (spec 2026-08-06 §5).

The whole point is sync BY CONSTRUCTION: video segment i is sized to clip i, and the
audio track is exactly those clips with ``gap_s`` of silence between them — offsets and
segment starts coincide with no arithmetic in between. The renderer keeps seeing ONE
mp3 + ONE word-timings sidecar, so nothing downstream changes.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

INTER_SCENE_GAP_S = 0.35


def probe_duration_s(path: Path) -> float:
    """Container duration via ffprobe; raises RuntimeError when unreadable."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe failed for {path.name}: {proc.stderr[:200]}")
    try:
        return float(json.loads(proc.stdout)["format"]["duration"])
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(f"ffprobe gave no duration for {path.name}") from exc


def line_offsets(durations: list[float], gap_s: float) -> list[float]:
    """Start offset of each clip inside the constructed track (gaps BETWEEN clips)."""
    offsets: list[float] = []
    cursor = 0.0
    for duration in durations:
        offsets.append(cursor)
        cursor += duration + gap_s
    return offsets


def merge_word_timings(
    per_line_words: list[list[dict[str, Any]]], offsets: list[float]
) -> dict[str, Any]:
    """One sidecar over the constructed track: every line's words shifted by its offset.

    A line without timings contributes nothing (captions there fall back to the render's
    even-spread) — never a reason to fail the voice.
    """
    words: list[dict[str, Any]] = []
    for line_words, offset in zip(per_line_words, offsets, strict=True):
        for word in line_words:
            words.append(
                {
                    "text": str(word.get("text", "")),
                    "start_s": float(word.get("start_s", 0.0)) + offset,
                    "end_s": float(word.get("end_s", 0.0)) + offset,
                }
            )
    return {"words": words}


def concat_with_gaps(clip_paths: list[Path], gap_s: float, out_path: Path) -> None:
    """Concat clips with ``gap_s`` silence BETWEEN them (n-1 gaps) into one mp3.

    filter_complex re-encodes; no gap after the last clip, so track end == last word's
    breath and ``-shortest`` at mux time has nothing to trim.
    """
    if not clip_paths:
        raise RuntimeError("no clips to concat")
    args: list[str] = ["ffmpeg", "-y"]
    for clip in clip_paths:
        args += ["-i", str(clip)]
    parts: list[str] = []
    labels: list[str] = []
    for i in range(len(clip_paths)):
        labels.append(f"[{i}:a]")
        if i < len(clip_paths) - 1:
            parts.append(
                f"aevalsrc=0:d={gap_s}:s=44100[g{i}]"
            )
            labels.append(f"[g{i}]")
    graph = ";".join(parts + [
        "".join(labels) + f"concat=n={len(labels)}:v=0:a=1[out]"
    ])
    args += ["-filter_complex", graph, "-map", "[out]", "-q:a", "4", str(out_path)]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(args, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg concat failed: {proc.stderr[-300:]}")
```

- [ ] **Step 4: Grün verifizieren**

Run: `uv run pytest tests/test_voice_concat.py`
Expected: PASS (der concat-Test läuft mit echtem ffmpeg — Projektstandard).

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/voice_concat.py services/local-api/tests/test_voice_concat.py
git commit -m "feat(short-creator): constructed voice track from per-line clips"
```

---

### Task VS2: VoiceSegment-Modell + Synthese pro Zeile mit Zeilen-Cache

**Files:**
- Modify: `services/local-api/src/laura/short_creator/board_models.py` (`VoiceSegment` vor `VoiceArtifact` :311; `VoiceArtifact.segments`)
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (`synthesize_script_voice` :2109-2207)
- Test: `services/local-api/tests/test_voice_per_line.py` (neu; Fake-Backend nach dem Muster der bestehenden synthesize-Tests — die faken den `VoiceBackend` bereits)

**Interfaces:**
- Consumes: `VoiceBackend.synthesize(text, out_path)` (voice.py:29), `concat_with_gaps`/`line_offsets`/`merge_word_timings`/`probe_duration_s`/`INTER_SCENE_GAP_S` (VS1), `_lines_in_storyline_order`, `script_hash`, Gate-B-Check (bleibt wörtlich stehen).
- Produces: `VoiceSegment{scene_number, chapter, line_hash, mp3_path, duration_s, offset_s}`; `VoiceArtifact.segments: list[VoiceSegment] | None`; Zeilen-Cache unter `{workspace_root}/voiceovers/lines/{line_hash}.mp3` (+`.timings.json`); Artefakt-Topfelder (`mp3_path`, `timings_path`, `voice_s`) zeigen auf die KONSTRUIERTE Gesamtspur. VS3 verlässt sich auf `segments`-Reihenfolge == Storyline-Reihenfolge.

- [ ] **Step 1: Modell**

In board_models.py vor `VoiceArtifact`:

```python
class VoiceSegment(BaseModel):
    """One script line's own clip inside the constructed track (spec 2026-08-06 §5.1)."""

    model_config = ConfigDict(extra="forbid")

    scene_number: int = Field(ge=1)
    chapter: int = Field(ge=1)
    line_hash: str
    mp3_path: str
    duration_s: float = Field(gt=0.0)
    offset_s: float = Field(ge=0.0)
```

`VoiceArtifact` bekommt nach `voice_s`:

```python
    # Per-line clips in STORYLINE playback order; None = legacy single-call track (every
    # board written before per-scene voice). build_cutlist sizes segment i to segments[i].
    segments: list[VoiceSegment] | None = None
```

- [ ] **Step 2: Failing Tests**

```python
"""Per-line synthesis: line cache, constructed track, merged sidecar, honest failure."""


class CountingBackend:
    """Fake VoiceBackend: writes a real ffmpeg tone per call so durations are probeable."""

    def __init__(self, seconds_per_call: float = 0.6, fail_texts: set[str] | None = None):
        self.calls: list[str] = []
        self.seconds = seconds_per_call
        self.fail_texts = fail_texts or set()

    def synthesize(self, text: str, out_path: Path) -> dict[str, Any]:
        self.calls.append(text)
        if text in self.fail_texts:
            return {"ok": False, "reason": "boom"}
        _tone(out_path, self.seconds)  # same helper as test_voice_concat
        timings = Path(str(out_path) + ".timings.json")
        timings.write_text(json.dumps({"words": [
            {"text": text.split()[0], "start_s": 0.0, "end_s": 0.3}]}), encoding="utf-8")
        return {"ok": True, "path": str(out_path), "timings_path": str(timings)}


def test_per_line_synthesis_builds_segments_and_track(...):
    # board with storyline (2 chapters, scenes 1+2) and script (one line per scene)
    result = tools["synthesize_script_voice"]()
    assert result["ok"] is True and result["lines"] == 2
    voice = board.load("voice")
    assert voice.segments is not None and len(voice.segments) == 2
    assert voice.segments[0].offset_s == pytest.approx(0.0)
    assert voice.segments[1].offset_s == pytest.approx(
        voice.segments[0].duration_s + INTER_SCENE_GAP_S, abs=0.05)
    assert Path(voice.mp3_path).exists() and Path(voice.timings_path).exists()


def test_line_cache_skips_unchanged_lines(...):
    tools["synthesize_script_voice"]()
    backend.calls.clear()
    # change ONE line's text, keep the other — resynthesis touches only the changed line
    tools["save_script_chapter"](2, [{"scene_number": 2, "text": "neuer text"}])
    tools["synthesize_script_voice"]()
    assert backend.calls == ["neuer text"]


def test_line_failure_is_named_and_retried_once(...):
    backend = CountingBackend(fail_texts={"kaputt"})
    # script line for scene 3 has text "kaputt"
    result = tools["synthesize_script_voice"]()
    assert result["ok"] is False
    assert "scene 3" in result["reason"]
    assert backend.calls.count("kaputt") == 2  # exactly one retry
```

Run: `uv run pytest tests/test_voice_per_line.py`
Expected: FAIL (heute ein einziger synthesize-Call, keine `segments`).

- [ ] **Step 3: synthesize_script_voice umbauen**

Der Kopf (Gate-B-Check :2129-2149, Storyline/Script-Loads, `ordered_lines`, `new_hash`, Cached-Kurzschluss :2162-2169, Backend/Asset/Projekt-Auflösung :2171-2179) bleibt WÖRTLICH stehen. Ab dem Synthese-Teil (:2181-2197) ersetzen durch:

```python
            workspace = Path(str(project["workspace_root"]))
            lines_dir = workspace / "voiceovers" / "lines"
            gap = INTER_SCENE_GAP_S

            clip_paths: list[Path] = []
            durations: list[float] = []
            per_line_words: list[list[dict[str, Any]]] = []
            metas: list[tuple[int, int, str]] = []  # (scene_number, chapter, line_hash)
            for line in ordered_lines:
                lh = hashlib.sha256(line.text.encode("utf-8")).hexdigest()
                clip = lines_dir / f"{lh}.mp3"
                timings = Path(str(clip) + ".timings.json")
                if not clip.is_file():
                    synth = backend.synthesize(line.text, clip)
                    if not synth.get("ok"):
                        synth = backend.synthesize(line.text, clip)  # exactly one retry
                    if not synth.get("ok"):
                        return {
                            "ok": False,
                            "reason": (
                                f"voice synthesis failed for scene {line.scene_number} "
                                f"(chapter {line.chapter}): "
                                f"{str(synth.get('reason') or 'synthesis failed')[:120]}"
                            ),
                        }
                clip_paths.append(clip)
                durations.append(probe_duration_s(clip))
                per_line_words.append(_read_words(str(timings)) if timings.is_file() else [])
                metas.append((line.scene_number, line.chapter, lh))

            offsets = line_offsets(durations, gap)
            out_path = workspace / "voiceovers" / f"{new_id()}.mp3"
            concat_with_gaps(clip_paths, gap, out_path)
            merged = merge_word_timings(per_line_words, offsets)
            timings_path: str | None = None
            if merged["words"]:
                timings_path = str(out_path) + ".timings.json"
                Path(timings_path).write_text(
                    json.dumps(merged, ensure_ascii=False), encoding="utf-8"
                )
            voice_s = probe_duration_s(out_path)
            artifact = VoiceArtifact(
                script_hash=new_hash,
                mp3_path=str(out_path),
                timings_path=timings_path,
                voice_s=voice_s,
                segments=[
                    VoiceSegment(
                        scene_number=scene, chapter=chap, line_hash=lh,
                        mp3_path=str(clip), duration_s=dur, offset_s=off,
                    )
                    for (scene, chap, lh), clip, dur, off in zip(
                        metas, clip_paths, durations, offsets, strict=True
                    )
                ],
                parents={
                    "storyline": _content_hash(storyline),
                    "script": _content_hash(script),
                },
            )
            version = board.save("voice", artifact)
            return {
                "ok": True,
                "cached": False,
                "version": version,
                "mp3_path": artifact.mp3_path,
                "voice_s": voice_s,
                "lines": len(clip_paths),
            }
```

Imports oben im Modul: `from .voice_concat import INTER_SCENE_GAP_S, concat_with_gaps, line_offsets, merge_word_timings, probe_duration_s` (plus `hashlib`, falls im Modul noch nicht importiert). `concat_with_gaps`/`probe_duration_s` werfen `RuntimeError` — der bestehende äußere `except Exception` des Tools fängt das als `ok: False`.

Der Docstring des Tools wird um einen Satz ergänzt: „Synthesizes PER LINE with an on-disk line cache (only changed lines hit the TTS API) and constructs the single track + merged sidecar from the clips."

- [ ] **Step 4: Grün + Regression**

Run: `uv run pytest tests/test_voice_per_line.py tests/test_production_tools.py`
Expected: PASS. Bestehende synthesize-Tests, die den EINEN Gesamttext-Call asserten, auf das Zeilen-Verhalten umstellen (die Zeilentexte statt des Gesamttexts) — Verhalten hat sich gewollt geändert; Cached-Kurzschluss-Tests bleiben unverändert gültig.

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/board_models.py services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_voice_per_line.py services/local-api/tests/test_production_tools.py
git commit -m "feat(short-creator): per-line voice synthesis with line cache and constructed track"
```

---

### Task VS3: build_cutlist — Segment = Clip-Länge

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (`build_cutlist` :2209-2410)
- Test: `services/local-api/tests/test_cutlist_per_scene_voice.py` (neu)

**Interfaces:**
- Consumes: `VoiceArtifact.segments` in Storyline-Reihenfolge (VS2), `INTER_SCENE_GAP_S`.
- Produces: Segment-i-Dauer == `segments[i].duration_s + gap` (letztes ohne gap); Kapazitäts-Hard-Guard; Legacy-Pfad (`segments is None`) byte-identisch zu heute.

- [ ] **Step 1: Failing Tests**

```python
def test_segments_sized_to_their_clips(...):
    # voice.segments: [1.2s, 0.8s], fps=30, gap=0.35
    result = tools["build_cutlist"]()
    cutlist = board.load("cutlist")
    d0 = (cutlist.segments[0].end_frame_exclusive - cutlist.segments[0].start_frame) / 30.0
    d1 = (cutlist.segments[1].end_frame_exclusive - cutlist.segments[1].start_frame) / 30.0
    assert d0 == pytest.approx(1.2 + 0.35, abs=1 / 30)
    assert d1 == pytest.approx(0.8, abs=1 / 30)          # last: no gap
    # sync invariant: video total == audio total (offsets construction)
    assert d0 + d1 == pytest.approx(1.2 + 0.35 + 0.8, abs=2 / 30)


def test_segment_count_drift_rejected(...):
    # storyline has 3 entries, voice.segments only 2 -> honest refusal
    result = tools["build_cutlist"]()
    assert result["ok"] is False and "synthesize_script_voice" in result["reason"]


def test_clip_longer_than_scene_rejected_with_scene_name(...):
    # segments[0].duration_s = 20.0 but scene 1 only holds 4.0s
    result = tools["build_cutlist"]()
    assert result["ok"] is False and "scene 1" in result["reason"]


def test_legacy_voice_without_segments_unchanged(...):
    # voice with segments=None -> chapter_audio_windows path, same cutlist as before
    ...
```

(Fixture-Faktur aus den bestehenden build_cutlist-Tests; der Legacy-Test darf ein bestehender, unverändert grüner Test sein — dann hier nur benennen, nicht duplizieren.)

- [ ] **Step 2: Fail verifizieren**

Run: `uv run pytest tests/test_cutlist_per_scene_voice.py`
Expected: FAIL.

- [ ] **Step 3: Segment-Pfad einbauen**

In `build_cutlist` nach dem Voice-Hash-Guard (:2266-2274):

```python
            voice_segments = voice.segments  # None = legacy single-track board
            if voice_segments is not None:
                expected = sum(len(c.scene_numbers) for c in storyline.arc)
                if len(voice_segments) != expected:
                    return {
                        "ok": False,
                        "reason": (
                            f"voice has {len(voice_segments)} line clips but the storyline "
                            f"references {expected} scene entries — run "
                            "synthesize_script_voice again so voice and cut agree"
                        ),
                    }
```

Im Kapitel-Loop: der Fensterauflösungs-Teil (erste Hälfte, :2288-2342) bleibt unverändert. Die Dauer-Berechnung (:2344-2350) wird konditional:

```python
                if voice_segments is not None:
                    seg_slice = voice_segments[order : order + len(resolved_scenes)]
                    durations = []
                    for idx, ((scene_number, src_start, src_end, _w, _r), seg) in enumerate(
                        zip(resolved_scenes, seg_slice, strict=True)
                    ):
                        is_last = order + idx == len(voice_segments) - 1
                        want = seg.duration_s + (0.0 if is_last else INTER_SCENE_GAP_S)
                        capacity = (src_end - src_start) / fps
                        if want > capacity + 1e-6:
                            return {
                                "ok": False,
                                "reason": (
                                    f"the line for scene {scene_number} speaks "
                                    f"{seg.duration_s:.1f}s but the scene only holds "
                                    f"{capacity:.1f}s — shorten that line "
                                    "(save_script_chapter), then re-run "
                                    "synthesize_script_voice"
                                ),
                            }
                        durations.append(want)
                else:
                    audio_window = audio_windows.get(chapter.chapter)
                    if audio_window is not None:
                        durations = _scale_chapter_durations(
                            base_durations, stretch_caps, audio_window[1] - audio_window[0]
                        )
                    else:
                        durations = base_durations
```

Achtung Reihenfolge-Kopplung: `order` zählt global über alle Kapitel (bestehende Variable) — der Slice nutzt exakt dieselbe Arc-Iteration wie VS2s `ordered_lines` (`_lines_in_storyline_order` läuft denselben Arc), dadurch ist `voice_segments[order + idx]` GENAU der Clip der Zeile dieses Szenen-Eintrags. Ein Kommentar an der Stelle hält das fest. Wichtig: `order` wird erst im zweiten Loop inkrementiert — der Slice-Start `order` referenziert den Stand VOR dem zweiten Loop des Kapitels; das stimmt, weil der zweite Loop des VORIGEN Kapitels `order` bereits um dessen Segmentzahl erhöht hat.

Der Fenster-Start bleibt: `raw_start = src_start + round(window.offset_s * fps)`; die bestehende Klemmung `start_frame = min(raw_start, src_end - dur_frames)` zieht den Start zurück, wenn das Fenster zu nah am Szenenende liegt — zusammen mit dem Kapazitäts-Guard oben bleibt `end_frame_exclusive <= src_end` immer wahr.

- [ ] **Step 4: Grün + Regression**

Run: `uv run pytest tests/test_cutlist_per_scene_voice.py tests/test_production_tools.py`
Expected: PASS; kein bestehender Cutlist-Test ändert sich (Legacy-Pfad unberührt).

- [ ] **Step 5: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/test_cutlist_per_scene_voice.py
git commit -m "feat(short-creator): cutlist segments sized to their own voice clips"
```

---

### Task VS4: Kapazitäts-Guard beim Script-Schreiben (pro Zeile)

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_tools.py` (`save_script_chapter`, beim bestehenden `capacity_warning` :2012-2023)
- Test: Erweiterung `services/local-api/tests/test_production_tools_scene_gate.py` (oder das bestehende save_script_chapter-Testmodul — dorthin, wo die capacity_warning-Tests liegen)

**Interfaces:**
- Consumes: `estimate_voice_seconds(words, language)` (bestehend), `_resolve_scene`, `fps` via `_fps(db, asset)` (Muster aus build_cutlist :2259).
- Produces: `save_script_chapter` lehnt eine Zeile hart ab, deren geschätzte Sprechdauer die Szenendauer klar sprengt (Faktor 1.15 + 0.5 s Puffer gegen die ±30%-Schätzunschärfe); knappe Fälle bleiben Warnung wie bisher.

- [ ] **Step 1: Failing Test**

```python
def test_line_grossly_over_scene_capacity_rejected(...):
    # scene 1 holds 4.0s; a 60-word German line (~24s estimated) must be refused
    result = tools["save_script_chapter"](1, [{"scene_number": 1, "text": long_text}])
    assert result["ok"] is False
    assert "scene 1" in result["reason"] and "4.0" in result["reason"]


def test_line_slightly_over_capacity_still_saves_with_warning(...):
    # estimate 4.3s against 4.0s capacity -> saved, capacity_warning present
    result = tools["save_script_chapter"](1, [{"scene_number": 1, "text": short_text}])
    assert result["ok"] is True and "capacity_warning" in result
```

- [ ] **Step 2: Fail verifizieren**

Run: `uv run pytest <testdatei> -k capacity`
Expected: der neue Reject-Test FAILt (heute nur Warnung).

- [ ] **Step 3: Guard einbauen**

In `save_script_chapter`, innerhalb der Transaktion nach dem Gate-S-Guard (GS2) und VOR dem Merge/Save — pro Zeile gegen ihre SZENE (nicht nur Kapitel-Summe):

```python
                asset_row = repos.get_asset(db, asset_id)
                fps = _fps(db, asset_row) if asset_row is not None else 30.0
                for line in new_lines:
                    resolved = _resolve_scene(db, asset_id, line.scene_number)
                    if resolved is None:
                        continue  # unknown scene: the storyline guard owns that failure
                    src_start, src_end, _t = resolved
                    scene_s = (src_end - src_start) / fps
                    est_s = estimate_voice_seconds(len(line.text.split()), language)
                    if est_s > scene_s * 1.15 + 0.5:
                        return {
                            "ok": False,
                            "reason": (
                                f"the line for scene {line.scene_number} would speak "
                                f"~{est_s:.1f}s but the scene only holds {scene_s:.1f}s — "
                                "shorten it or split the content across more scenes; "
                                "per-scene voice binds each line's video segment to its "
                                "own clip length"
                            ),
                        }
```

Die bestehende Kapitel-Summen-`capacity_warning` (:2012-2023) bleibt unverändert bestehen (sie fängt die Verteilung, der neue Guard die Einzelzeile).

- [ ] **Step 4: Grün + Commit**

Run: `uv run pytest tests/test_production_tools.py tests/test_production_tools_scene_gate.py`
Expected: PASS.

```bash
git add services/local-api/src/laura/short_creator/production_tools.py services/local-api/tests/<testdatei>
git commit -m "feat(short-creator): per-line scene-capacity guard at script save"
```

---

### Task VS5: Grounding + Secondbrain-Charter + volle Gates + Doku

**Files:**
- Modify: `services/local-api/src/laura/short_creator/production_orchestrator.py` (`build_production_task`: Fakten-Block pro gewählter Szene)
- Modify: Charter/Agenten-Prompt des `scene_author` (Fundort: `grep -n "scene_author" services/local-api/src/laura/short_creator/production_agents.py`)
- Modify: `docs/superpowers/specs/2026-08-06-scene-selection-per-scene-voice-design.md` (Status-Zeile „umgesetzt durch <plan>") und `tasks/todo.md` (Arc-Eintrag abhaken/ergänzen)
- Test: Erweiterung des build_production_task-Testmoduls (`grep -rn "build_production_task" services/local-api/tests`)

**Interfaces:**
- Consumes: `SceneSelection` mit bestätigten Kandidaten (Plan 1 — liegt Plan 1 noch nicht auf dem Branch, entfällt der Kandidaten-Zweig und der Fakten-Block wird aus `board.scene_reviews()` + `get_scene_transcript`-Maschinerie für die STORYLINE-Szenen gebaut; der Code unten behandelt beide Fälle).
- Produces: Task-Text mit `SCENE FACTS`-Abschnitt; zwei Charter-Zeilen.

- [ ] **Step 1: Failing Test**

```python
def test_task_carries_scene_facts_for_selected_scenes(...):
    # board with confirmed selection [2]: candidate description "n8n Flow im Bild",
    # snippet "wir bauen den flow"
    task = build_production_task(db, board, asset_id=..., task="t", target_seconds=60,
                                 message=None)
    assert "SCENE FACTS" in task
    assert "n8n Flow im Bild" in task and "wir bauen den flow" in task
```

- [ ] **Step 2: Fail verifizieren**

Run: `uv run pytest <build_production_task-Testmodul> -k scene_facts`
Expected: FAIL.

- [ ] **Step 3: Fakten-Block in build_production_task**

```python
    facts_lines: list[str] = []
    selection = board.load("scene_selection")
    if isinstance(selection, SceneSelection) and selection.confirmed_utc is not None:
        chosen = {
            c.scene_number: c
            for c in selection.candidates
            if c.scene_number in set(selection.selected_scene_numbers)
        }
        for number in sorted(chosen):
            cand = chosen[number]
            facts_lines.append(
                f"- scene {number}: SHOWS {cand.description} | SAYS "
                f"\"{cand.transcript_snippet}\""
            )
    else:
        for review in board.scene_reviews():
            facts_lines.append(
                f"- scene {review.scene_number}: SHOWS {review.description}"
            )
    if facts_lines:
        facts_block = (
            "SCENE FACTS (write every script line ABOUT its scene — what it shows and "
            "says; no free-floating marketing copy):\n" + "\n".join(facts_lines) + "\n"
        )
```

und den Block in den Task-String einfügen (bei den anderen Material-Abschnitten; die genaue Einfügestelle ist die SOURCE-MATERIAL-Sektion aus dem 2026-08-04-Arc — `grep -n "SOURCE MATERIAL" production_orchestrator.py`).

- [ ] **Step 4: Charter-Zeilen**

Beim `scene_author`-Systemprompt (production_agents.py) zwei Zeilen ergänzen:

```
- Every line is written FOR its scene: it must match what the scene SHOWS (SCENE FACTS)
  and may quote what is SAID there (get_scene_transcript). Never narrate things the
  scene does not show.
- Before writing product or proper names, verify them with brain_search when the tool
  is available — the vault knows the real names (Rowboat vs n8n class of mistakes).
```

- [ ] **Step 5: Volle Gates + Doku**

Run (aus `services/local-api`): `uv run pytest` und `uv run mypy` und `uv run ruff check .`
Run (aus `apps/desktop`, nur falls dieser Branch auch Plan-1-Frontend trägt): `pnpm typecheck && pnpm test && pnpm build`
Expected: alles grün — echte Summary-Zeilen in den Report.

Doku: Spec-Datei bekommt oben eine Status-Zeile („Umgesetzt: Plan 1 <datei>, Plan 2 <datei>, <datum>"); `tasks/todo.md` Arc-Eintrag aktualisieren.

- [ ] **Step 6: Commit**

```bash
git add services/local-api/src/laura/short_creator/production_orchestrator.py services/local-api/src/laura/short_creator/production_agents.py docs/superpowers/specs/2026-08-06-scene-selection-per-scene-voice-design.md tasks/todo.md services/local-api/tests/<testdatei>
git commit -m "feat(short-creator): scene-facts grounding + secondbrain charter"
```

---

## Manuelle Prüfliste (nach Merge, Live-App)

1. Voller Lauf mit Gate S: Auswahl → Script → Freigabe → Film; hörbar prüfen, dass jede Szene über IHRE Zeile läuft (kein n8n-Bild unter Rowboat-Text).
2. Follow-up „mach den Hook kürzer" → nur die Hook-Zeile wird neu synthetisiert (Backend-Log: ein TTS-Call).
3. Sprachwechsel „mach das in english" → alle Zeilen neu (Cache-Miss überall), Film konsistent englisch.
4. Alt-Session resumen → Legacy-Ein-Spur-Pfad rendert unverändert.
