"""Reels Stufe 2 - Formate (22.10.2026): Hochformat ``reel``, ``quadrat`` (1:1), ``quer`` (16:9).

``format`` an ``render-reel``, ``stills`` und ``titelkarte``. Ohne ``format`` (oder mit ``reel``)
bleiben Optionen, Rezept-Hash und der ``render_clips_mp4``-Aufruf byte-gleich.
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from laura.config import Settings
from laura.db import repos
from laura.ingest.probe import probe_asset
from laura.ledger import get_ledger_store
from laura.main import create_app
from laura.render.formate import FORMATE, groesse

from .test_reel_groesse import _job

# Rezept-Hash von POST render-reel mit leerem Koerper, gemessen auf origin/main b08e402
# VOR der Format-Aenderung (Basis-Lauf, siehe task-1-report.md).
_BASIS_HASH_LEER = "7ef7c3402ee398489952336c73e87a7353ddfef77c0c0ec3179a5a21e1230468"
# Dasselbe fuer den Koerper, den der Video-Claw (MOS reel_bauen) heute schickt.
_CLAW_KOERPER: dict[str, Any] = {
    "vertical": True, "captions": True, "caption_preset": "reels", "kennzeichnung": "auto",
    "caption_source": "gemischt", "max_bytes": 15_000_000, "loudnorm": True,
    "rahmung": "zuschnitt", "hook_text": "Hallo",
}
_BASIS_HASH_CLAW = "199a64a445e594d63e08dd0cd4b4d0f5ad0c1e9b8cd89c6ea1c3a0f1fb040d38"
# render_clips_mp4-Aufruf fuer die Optionen unten, gemessen auf b08e402 (Basis-Lauf).
_BASIS_AUFRUF = {
    "audio_overlays": None, "caption_ass": None, "caption_srt": None, "disclosure_text": "KI",
    "hook_text": "H", "music_tracks": None, "rate_den": 1, "rate_num": 30, "vertical": True,
    "video_transitions": None,
}


def _da(zeile: dict[str, Any] | None) -> dict[str, Any]:
    assert zeile is not None
    return zeile


def _export_zeile(db: Any, i: str) -> dict[str, Any]:
    return _da(repos.get_export(db, i))


def _asset_zeile(db: Any, i: str) -> dict[str, Any]:
    return _da(repos.get_asset(db, i))


def _timeline_zeile(db: Any, i: str) -> dict[str, Any]:
    return _da(repos.get_timeline(db, i))


def _job_zeile(db: Any, i: str) -> dict[str, Any]:
    return _da(repos.get_job(db, i))


def _have_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


# --- Tabelle -------------------------------------------------------------------------

def test_tabelle_und_groesse() -> None:
    assert FORMATE == {"reel": (1080, 1920), "quadrat": (1080, 1080), "quer": (1920, 1080)}
    assert groesse(None) == (1080, 1920) and groesse("reel") == (1080, 1920)
    assert groesse("quadrat") == (1080, 1080) and groesse("quer") == (1920, 1080)
    with pytest.raises(ValueError):
        groesse("9:16")


# --- API: Optionen und Rezept-Hash ----------------------------------------------------

def _client(tmp_path: Path) -> tuple[TestClient, Any]:
    app = create_app(Settings(workspace_root=tmp_path, token=None, start_runner=False))
    return TestClient(app), app


def _tl(client: TestClient, db: Any) -> str:
    pid = client.post("/projects", json={"name": "p", "sequence_rate_num": 30,
                                         "sequence_rate_den": 1}).json()["id"]
    return str(repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")["id"])


def _optionen_und_hash(client: TestClient, db: Any, tl: str,
                       koerper: dict[str, Any]) -> tuple[dict[str, Any], str]:
    r = client.post(f"/timelines/{tl}/render-reel", json=koerper)
    assert r.status_code == 202, r.text
    o = dict(_export_zeile(db, r.json()["export_id"])["options"])
    run = get_ledger_store(db).get_run(str(o.pop("short_run_id")))
    assert run is not None
    return o, str(run["recipe_hash"])


def _aufruf(tmp_path: Path, monkeypatch: Any, options: dict[str, Any]) -> dict[str, Any]:
    """kwargs des render_clips_mp4-Aufrufs im Render-Job (ohne Zielpfad)."""
    from laura.render import handlers as rh
    gesehen: dict[str, Any] = {}
    echt = monkeypatch.setattr

    def abfangen(ziel: Any, name: str, wert: Any, *a: Any, **k: Any) -> None:
        if ziel is rh and name == "render_clips_mp4":
            wert = lambda *aa, **kk: gesehen.update(kk)  # noqa: E731
        echt(ziel, name, wert, *a, **k)
    monkeypatch.setattr = abfangen
    try:
        _job(tmp_path, monkeypatch, options)
    finally:
        del monkeypatch.setattr  # wieder die Methode der Klasse (zweiter Aufruf faengt neu ab)
    return gesehen


def test_ohne_format_bleibt_hash_und_aufruf_gleich(tmp_path: Path, monkeypatch: Any) -> None:
    client, app = _client(tmp_path / "api")
    db = app.state.db
    tl = _tl(client, db)
    for koerper, basis in (({}, _BASIS_HASH_LEER), (_CLAW_KOERPER, _BASIS_HASH_CLAW)):
        ohne, h_ohne = _optionen_und_hash(client, db, tl, koerper)
        mit, h_mit = _optionen_und_hash(client, db, tl, {**koerper, "format": "reel"})
        assert "format" not in ohne and ohne == mit
        assert h_ohne == h_mit == basis

    opts = {"vertical": True, "hook_text": "H", "disclosure_text": "KI", "captions": False,
            "caption_preset": "reels", "caption_fontsize": 72, "caption_safe_margin": 250}
    ohne = _aufruf(tmp_path / "a", monkeypatch, opts)
    mit = _aufruf(tmp_path / "b", monkeypatch, {**opts, "format": "reel"})
    assert ohne == mit == _BASIS_AUFRUF


@pytest.mark.parametrize("fmt", ["quadrat", "quer"])
def test_render_reel_quadrat_und_quer_out_size(tmp_path: Path, monkeypatch: Any, fmt: str) -> None:
    client, app = _client(tmp_path / "api")
    o, _h = _optionen_und_hash(client, app.state.db, _tl(client, app.state.db), {"format": fmt})
    assert o["format"] == fmt
    kw = _aufruf(tmp_path / "job", monkeypatch, {"vertical": True, "format": fmt})
    assert kw["out_size"] == FORMATE[fmt] and kw["vertical"] is True


def test_format_erzwingt_umrahmung_auch_ohne_vertical(tmp_path: Path, monkeypatch: Any) -> None:
    # Ohne vertical=True wuerde mp4 nicht skalieren: ein Format verlangt aber die Zielgroesse.
    kw = _aufruf(tmp_path, monkeypatch, {"vertical": False, "format": "quer"})
    assert kw["out_size"] == (1920, 1080) and kw["vertical"] is True


@pytest.mark.parametrize("fmt, schrift, rand", [("reel", 72, 250), ("quadrat", 64, 100),
                                                ("quer", 54, 48)])
def test_untertitel_vorgaben_je_format(tmp_path: Path, fmt: str, schrift: int, rand: int) -> None:
    client, app = _client(tmp_path)
    db = app.state.db
    tl = _tl(client, db)
    o, _h = _optionen_und_hash(client, db, tl, {"format": fmt, "captions": True})
    assert (o["caption_fontsize"], o["caption_safe_margin"]) == (schrift, rand)
    # Ausdruecklich gesetzte Werte gewinnen - auch wenn sie der Hochformat-Vorgabe gleichen.
    o, _h = _optionen_und_hash(client, db, tl, {"format": fmt, "caption_fontsize": 72,
                                                "caption_safe_margin": 250})
    assert (o["caption_fontsize"], o["caption_safe_margin"]) == (72, 250)


@pytest.mark.parametrize("fmt, leinwand", [("quadrat", (1080, 1080)), ("quer", (1920, 1080))])
def test_untertitel_leinwand_aus_format(tmp_path: Path, monkeypatch: Any, fmt: str,
                                        leinwand: tuple[int, int]) -> None:
    from laura.render import handlers as rh
    gesehen: dict[str, Any] = {}
    monkeypatch.setattr(rh, "group_caption_lines", lambda words: [("Hallo", 0, 30)])
    monkeypatch.setattr(rh, "build_ass", lambda lines, **kw: gesehen.update(kw) or "ASS")
    _aufruf(tmp_path, monkeypatch, {"vertical": True, "format": fmt, "captions": True,
                                    "caption_preset": "reels"})
    assert (gesehen["play_w"], gesehen["play_h"]) == leinwand
    # Ohne Schrift-Optionen gilt die Vorgabe des Formats auch im Render-Job.
    assert (gesehen["fontsize"], gesehen["margin_v"]) == {"quadrat": (64, 100),
                                                         "quer": (54, 48)}[fmt]


@pytest.mark.parametrize("wert", ["9:16", "<x>", "Quer", ""])
def test_format_ungueltig_422(tmp_path: Path, wert: str) -> None:
    client, app = _client(tmp_path)
    db = app.state.db
    tl = _tl(client, db)
    pid = _timeline_zeile(db, tl)["project_id"]
    bild = repos.create_asset(db, project_id=pid, type="image", display_name="b.png",
                              source_path=str(tmp_path / "b.png"))
    assert client.post(f"/timelines/{tl}/render-reel", json={"format": wert}).status_code == 422
    assert client.post(f"/projects/{pid}/stills",
                       json={"asset_id": bild["id"], "format": wert}).status_code == 422
    assert client.post(f"/projects/{pid}/titelkarte",
                       json={"text": "x", "hintergrund": "#000000",
                             "format": wert}).status_code == 422


def test_bausteine_payload_ohne_format_unveraendert(tmp_path: Path) -> None:
    client, app = _client(tmp_path)
    db = app.state.db
    pid = _timeline_zeile(db, _tl(client, db))["project_id"]
    r = client.post(f"/projects/{pid}/titelkarte", json={"text": "x", "hintergrund": "#000000"})
    job = _job_zeile(db, r.json()["job_id"])
    assert "format" not in json.loads(job["payload_json"])
    r = client.post(f"/projects/{pid}/titelkarte",
                    json={"text": "x", "hintergrund": "#000000", "format": "quer"})
    assert json.loads(_job_zeile(db, r.json()["job_id"])["payload_json"])["format"] == "quer"


# --- echte Laeufe ----------------------------------------------------------------------

def _wh(pfad: Path) -> tuple[int, int]:
    out = subprocess.check_output(["ffprobe", "-v", "quiet", "-select_streams", "v:0",
                                   "-show_entries", "stream=width,height", "-of", "json",
                                   str(pfad)], text=True)
    s = json.loads(out)["streams"][0]
    return int(s["width"]), int(s["height"])


def _laufen(app: Any, job_id: str) -> dict[str, Any]:
    for _ in range(10):
        if not app.state.runner.run_once():
            break
    job = _job_zeile(app.state.db, job_id)
    assert job["status"] == "succeeded", job
    return dict(json.loads(job["result_json"]))


def _video_asset(db: Any, pid: str, pfad: Path) -> str:
    pr = probe_asset(pfad)
    a = repos.create_asset(db, project_id=pid, type="video", display_name=pfad.name,
                           source_path=str(pfad))
    repos.update_asset_probe(
        db, a["id"], type=pr.type, duration_frames=pr.duration_frames, rate_num=pr.rate_num,
        rate_den=pr.rate_den, audio_sample_rate=pr.audio_sample_rate,
        start_timecode=pr.start_timecode, width=pr.width, height=pr.height,
        codec_video=pr.codec_video, codec_audio=pr.codec_audio, is_vfr=pr.is_vfr, sha256=None)
    return str(a["id"])


@pytest.mark.skipif(not _have_ffmpeg(), reason="ffmpeg/ffprobe not on PATH")
@pytest.mark.parametrize("fmt, rahmung", [("quadrat", "zuschnitt"), ("quer", "zuschnitt"),
                                          ("quer", "einpassen")])
def test_echte_ausgabe_hat_formatgroesse(tmp_path: Path, fmt: str, rahmung: str) -> None:
    """Titelkarte + Standbild im Format, dazu Bildschirm-Material 16:9 (1280x720): das fertige
    Reel hat exakt die Formatgroesse (E4: einheitliche Groesse -> keine Mischleinwand)."""
    client, app = _client(tmp_path)
    db = app.state.db
    pid = client.post("/projects", json={"name": "p", "sequence_rate_num": 30,
                                         "sequence_rate_den": 1}).json()["id"]
    png = tmp_path / "bild.png"
    subprocess.check_call(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                           "testsrc2=size=800x600", "-frames:v", "1", str(png)])
    bild = repos.create_asset(db, project_id=pid, type="image", display_name="bild.png",
                              source_path=str(png))
    r = client.post(f"/projects/{pid}/stills", json={"asset_id": bild["id"], "dauer_s": 1,
                                                     "bewegung": "ruhig", "format": fmt})
    assert r.status_code == 202, r.text
    still = _asset_zeile(db, _laufen(app, r.json()["job_id"])["asset_id"])
    r = client.post(f"/projects/{pid}/titelkarte", json={
        "text": "Drei Fehler, die jeder kennt", "hintergrund": "#0f2422",
        "logo_asset_id": bild["id"], "dauer_s": 1, "format": fmt})
    assert r.status_code == 202, r.text
    karte = _asset_zeile(db, _laufen(app, r.json()["job_id"])["asset_id"])
    assert (still["width"], still["height"]) == FORMATE[fmt]
    assert (karte["width"], karte["height"]) == FORMATE[fmt]
    assert _wh(Path(still["source_path"])) == FORMATE[fmt] == _wh(Path(karte["source_path"]))

    if fmt == "quer" and rahmung == "zuschnitt":
        # Bildschirmaufnahme 16:9 -> quer: der Zuschnitt ist die Identitaet (nichts beschnitten).
        quelle = tmp_path / "bildschirm.mp4"
        subprocess.check_call(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i",
                               "testsrc=d=1:s=1280x720:r=30", "-f", "lavfi", "-i",
                               "anullsrc=r=48000:cl=stereo", "-shortest", "-c:v", "libx264",
                               "-pix_fmt", "yuv420p", "-c:a", "aac", str(quelle)])
        material = [_video_asset(db, pid, quelle)]
    else:
        material = []
    tl = repos.create_timeline(db, project_id=pid, name="cut", kind="rough_cut")
    pos = 0
    for aid in [str(karte["id"]), str(still["id"]), *material]:
        dauer = int(_asset_zeile(db, aid)["duration_frames"])
        repos.add_timeline_clip(db, timeline_id=tl["id"], asset_id=aid, src_in_frame=0,
                                src_out_frame_exclusive=dauer, seq_in_frame=pos,
                                seq_out_frame_exclusive=pos + dauer)
        pos += dauer
    r = client.post(f"/timelines/{tl['id']}/render-reel",
                    json={"format": fmt, "rahmung": rahmung, "hook_text": "Hook",
                          "logo_asset_id": bild["id"]})
    assert r.status_code == 202, r.text
    _laufen(app, r.json()["job_id"])
    exp = _export_zeile(db, r.json()["export_id"])
    assert exp["status"] == "ready", exp
    assert _wh(Path(exp["path"])) == FORMATE[fmt]


def test_logo_breite_nach_kurzer_seite(tmp_path: Path, monkeypatch: Any) -> None:
    """14 % von min(Breite, Hoehe): im Hochformat wie bisher (1080), quer nicht 1,8x groesser."""
    from laura.render import logo
    gesehen: list[str] = []
    for groessen in ([(1080, 1920), (400, 200)], [(1920, 1080), (400, 200)],
                     [(1080, 1080), (400, 200)]):
        werte = iter(groessen)
        monkeypatch.setattr(logo, "_breite_hoehe", lambda _p, w=werte: next(w))

        def fake(a: list[str]) -> None:
            gesehen.append(a[a.index("-filter_complex") + 1])
            Path(a[-1]).write_bytes(b"")
        monkeypatch.setattr(logo, "run_ffmpeg", fake)
        logo.einblenden(tmp_path / "v.mp4", tmp_path / "l.png", breite_prozent=14)
    assert all(g.startswith("[1:v]scale=152:76[l]") for g in gesehen), gesehen
