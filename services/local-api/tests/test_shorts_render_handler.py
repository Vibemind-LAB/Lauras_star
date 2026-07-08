"""Unit tests for render.shorts_render.handle_shorts_render.

These MUST NOT run real ffmpeg: ``render_clips_mp4`` is monkeypatched with a stub that
records its call and writes a tiny placeholder file to ``dest`` so the handler's
``os.path.getsize`` + ``set_export_done`` path works without encoding anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.jobs.runner import JobContext
from laura.render import shorts_render

_WORD_FRAMES = 5


def _ctx(db: SqliteDatabase, export_id: str) -> JobContext:
    return JobContext(
        job_id="job-test",
        kind="shorts.render",
        queue="export",
        payload={"export_id": export_id},
        db=db,
    )


def _seed(
    tmp_path: Path, *, with_words: bool = True
) -> tuple[SqliteDatabase, str, str]:
    """Project (workspace_root) + 30 fps asset + succeeded run (+ words) + ONE candidate.

    Returns (db, candidate_id, asset_id). The candidate covers source frames [0, 30).
    """
    workspace = tmp_path / "ws" / "project"
    workspace.mkdir(parents=True, exist_ok=True)
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()

    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(workspace),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video",
        display_name="a.mp4", source_path=str(workspace / "a.mp4"),
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.start_analysis_run(db, run["id"])
    if with_words:
        words: list[dict[str, Any]] = []
        for i in range(6):
            sf = i * _WORD_FRAMES
            words.append(
                {"idx": i, "start_sample": sf * 1600, "end_sample": (sf + _WORD_FRAMES) * 1600,
                 "start_frame": sf, "end_frame": sf + _WORD_FRAMES,
                 "text": f"word{i}", "confidence": 1.0, "is_punctuation": False}
            )
        repos.insert_segment_with_words(
            db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
            segment={"start_sample": 0, "end_sample": 30 * 1600,
                     "start_frame": 0, "end_frame": 30, "text": "a", "confidence": 1.0},
            words=words,
        )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})

    tl = repos.create_timeline(db, project_id=project["id"], name="rc", kind="rough_cut")
    repos.replace_shorts_candidates(
        db, project["id"], asset["id"], tl["id"],
        [{
            "start_frame": 0, "end_frame_exclusive": 30,
            "start_boundary": "word", "end_boundary": "word",
            "score": 0.9, "rejected": False, "reject_reason": None,
            "score_breakdown": {"hook": 0.5}, "qa_passed": True, "qa_issues": [],
        }],
    )
    candidate = repos.list_shorts_candidates_by_asset(db, asset["id"])[0]
    return db, candidate["id"], asset["id"]


def _patch_render(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace render_clips_mp4 with a recorder that writes a tiny placeholder to dest."""
    calls: list[dict[str, Any]] = []

    def _fake(clips: list[Any], dest: Path, **kwargs: Any) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"FAKE-MP4")
        calls.append({"clips": clips, "dest": dest, "kwargs": kwargs})

    monkeypatch.setattr(shorts_render, "render_clips_mp4", _fake)
    return calls


def test_render_calls_renderer_vertical_with_clip_and_captions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """handle_shorts_render passes vertical=True, the 1-clip tuple, and a caption_ass."""
    db, candidate_id, asset_id = _seed(tmp_path)
    calls = _patch_render(monkeypatch)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None

    exp = repos.create_export(
        db, project_id=asset["project_id"], timeline_id=None, format="mp4",
        options={"kind": "short", "candidate_id": candidate_id,
                 "captions": True, "hook_text": "Boom", "loudnorm": True},
    )

    result = shorts_render.handle_shorts_render(_ctx(db, exp["id"]))

    assert len(calls) == 1
    call = calls[0]
    # vertical center-crop requested.
    assert call["kwargs"]["vertical"] is True
    # Exactly one clip tuple: (source_path, start_frame, end_frame_exclusive).
    assert call["clips"] == [(Path(asset["source_path"]), 0, 30)]
    # Captions present (words seeded) and hook + loudnorm forwarded.
    assert call["kwargs"]["caption_ass"] is not None
    assert "[Events]" in call["kwargs"]["caption_ass"]
    assert call["kwargs"]["hook_text"] == "Boom"
    assert call["kwargs"]["loudnorm"] is True

    # Export marked done with the rendered path + size.
    done = repos.get_export(db, exp["id"])
    assert done is not None
    assert done["status"] == "ready"
    assert Path(done["path"]).exists()
    assert done["size_bytes"] > 0

    # Return contract.
    assert result["export_id"] == exp["id"]
    assert result["candidate_id"] == candidate_id
    assert result["frames"] == 30
    assert result["captions"] is True


def test_render_without_words_still_succeeds_no_captions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No transcript words → render without captions (not an error)."""
    db, candidate_id, asset_id = _seed(tmp_path, with_words=False)
    calls = _patch_render(monkeypatch)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None

    exp = repos.create_export(
        db, project_id=asset["project_id"], timeline_id=None, format="mp4",
        options={"kind": "short", "candidate_id": candidate_id, "captions": True},
    )

    result = shorts_render.handle_shorts_render(_ctx(db, exp["id"]))

    assert calls[0]["kwargs"]["caption_ass"] is None
    assert result["captions"] is False
    done = repos.get_export(db, exp["id"])
    assert done is not None and done["status"] == "ready"


def test_captions_false_skips_caption_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """captions=False renders with no caption_ass even when words exist."""
    db, candidate_id, asset_id = _seed(tmp_path)
    calls = _patch_render(monkeypatch)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None

    exp = repos.create_export(
        db, project_id=asset["project_id"], timeline_id=None, format="mp4",
        options={"kind": "short", "candidate_id": candidate_id, "captions": False},
    )
    shorts_render.handle_shorts_render(_ctx(db, exp["id"]))
    assert calls[0]["kwargs"]["caption_ass"] is None


def test_reel_fit_option_forwarded_to_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reel_fit=True in export options is forwarded to render_clips_mp4 as reel_fit=True."""
    db, candidate_id, asset_id = _seed(tmp_path, with_words=False)
    calls = _patch_render(monkeypatch)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None

    exp = repos.create_export(
        db, project_id=asset["project_id"], timeline_id=None, format="mp4",
        options={"kind": "short", "candidate_id": candidate_id,
                 "captions": False, "reel_fit": True},
    )
    shorts_render.handle_shorts_render(_ctx(db, exp["id"]))

    assert len(calls) == 1
    assert calls[0]["kwargs"].get("reel_fit") is True
    # vertical must still be True regardless
    assert calls[0]["kwargs"]["vertical"] is True


def test_reel_fit_defaults_false_when_not_in_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When reel_fit is absent from options it defaults to False (center-crop path)."""
    db, candidate_id, asset_id = _seed(tmp_path, with_words=False)
    calls = _patch_render(monkeypatch)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None

    exp = repos.create_export(
        db, project_id=asset["project_id"], timeline_id=None, format="mp4",
        options={"kind": "short", "candidate_id": candidate_id, "captions": False},
    )
    shorts_render.handle_shorts_render(_ctx(db, exp["id"]))

    assert len(calls) == 1
    assert calls[0]["kwargs"].get("reel_fit") is False


def test_reel_blur_fill_option_forwarded_to_renderer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reel_blur_fill=True in options is forwarded to render_clips_mp4 as reel_blur_fill=True."""
    db, candidate_id, asset_id = _seed(tmp_path, with_words=False)
    calls = _patch_render(monkeypatch)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None

    exp = repos.create_export(
        db, project_id=asset["project_id"], timeline_id=None, format="mp4",
        options={"kind": "short", "candidate_id": candidate_id,
                 "captions": False, "reel_blur_fill": True},
    )
    shorts_render.handle_shorts_render(_ctx(db, exp["id"]))

    assert len(calls) == 1
    assert calls[0]["kwargs"].get("reel_blur_fill") is True
    # vertical must still be True regardless
    assert calls[0]["kwargs"]["vertical"] is True


def test_reel_blur_fill_defaults_false_when_not_in_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When reel_blur_fill is absent from options it defaults to False."""
    db, candidate_id, asset_id = _seed(tmp_path, with_words=False)
    calls = _patch_render(monkeypatch)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None

    exp = repos.create_export(
        db, project_id=asset["project_id"], timeline_id=None, format="mp4",
        options={"kind": "short", "candidate_id": candidate_id, "captions": False},
    )
    shorts_render.handle_shorts_render(_ctx(db, exp["id"]))

    assert len(calls) == 1
    assert calls[0]["kwargs"].get("reel_blur_fill") is False


def test_missing_candidate_sets_export_error_and_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A candidate_id that does not exist → export error + ValueError, renderer never called."""
    db, _candidate_id, asset_id = _seed(tmp_path)
    calls = _patch_render(monkeypatch)
    asset = repos.get_asset(db, asset_id)
    assert asset is not None

    exp = repos.create_export(
        db, project_id=asset["project_id"], timeline_id=None, format="mp4",
        options={"kind": "short", "candidate_id": "does-not-exist", "captions": True},
    )

    with pytest.raises(ValueError, match="candidate not found"):
        shorts_render.handle_shorts_render(_ctx(db, exp["id"]))

    assert calls == []  # renderer never reached
    errored = repos.get_export(db, exp["id"])
    assert errored is not None
    assert errored["status"] == "error"
    assert "candidate not found" in (errored["error"] or "")


def test_multi_candidate_render_concats_segments_and_offsets_captions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """candidate_ids renders several scenes as ONE short: clips concat in order, captions of the
    second segment shifted by the first segment's duration (voice stays aligned per scene)."""
    db, _cid, asset_id = _seed(tmp_path)  # candidate [0,30) + words 0..30
    asset = repos.get_asset(db, asset_id)
    assert asset is not None
    run = repos.get_latest_analysis_run(db, asset_id)
    assert run is not None

    # Words for the SECOND scene [60, 90) — offsetting must land them at local frames 30..60.
    words2: list[dict[str, Any]] = []
    for i in range(6):
        sf = 60 + i * _WORD_FRAMES
        words2.append(
            {"idx": i, "start_sample": sf * 1600, "end_sample": (sf + _WORD_FRAMES) * 1600,
             "start_frame": sf, "end_frame": sf + _WORD_FRAMES,
             "text": f"late{i}", "confidence": 1.0, "is_punctuation": False}
        )
    repos.insert_segment_with_words(
        db, asset_id=asset_id, run_id=run["id"], speaker_id=None,
        segment={"start_sample": 60 * 1600, "end_sample": 90 * 1600,
                 "start_frame": 60, "end_frame": 90, "text": "b", "confidence": 1.0},
        words=words2,
    )
    candidate = repos.list_shorts_candidates_by_asset(db, asset_id)[0]
    repos.replace_shorts_candidates(
        db, asset["project_id"], asset_id, candidate["source_timeline_id"],
        [
            {"start_frame": 0, "end_frame_exclusive": 30,
             "start_boundary": "word", "end_boundary": "word", "score": 0.9,
             "rejected": False, "reject_reason": None, "score_breakdown": {},
             "qa_passed": True, "qa_issues": []},
            {"start_frame": 60, "end_frame_exclusive": 90,
             "start_boundary": "word", "end_boundary": "word", "score": 0.8,
             "rejected": False, "reject_reason": None, "score_breakdown": {},
             "qa_passed": True, "qa_issues": []},
        ],
    )
    c1, c2 = repos.list_shorts_candidates_by_asset(db, asset_id)[:2]
    calls = _patch_render(monkeypatch)

    exp = repos.create_export(
        db, project_id=asset["project_id"], timeline_id=None, format="mp4",
        options={"kind": "short", "candidate_ids": [c1["id"], c2["id"]],
                 "captions": True, "reel_fit": True, "reel_blur_fill": True},
    )
    out = shorts_render.handle_shorts_render(_ctx(db, exp["id"]))

    assert out["segments"] == 2
    assert out["frames"] == 60
    clips = calls[0]["clips"]
    assert [(c[1], c[2]) for c in clips] == [(0, 30), (60, 90)]
    assert calls[0]["kwargs"].get("reel_fit") is True
    assert calls[0]["kwargs"].get("reel_blur_fill") is True
    # Second scene's captions start at local frame 30 == 1.0s -> the ASS carries a 0:00:01 cue.
    ass = calls[0]["kwargs"].get("caption_ass")
    assert ass and "0:00:01" in ass
