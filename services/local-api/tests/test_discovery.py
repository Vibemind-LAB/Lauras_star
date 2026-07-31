"""search_material: topic -> ranked assets + rough-cut scene hits across the whole project.

The discovery layer under auto-short (spec 2026-07-21-auto-short-design.md §1): semantic
when the [semantic] extra answers, lexical fallback otherwise, hits mapped onto each
asset's rough-cut scenes READ-ONLY — ranking must never create timelines as a side effect.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator import discovery

FPS = 30


def _db(tmp_path: Path) -> Database:
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db: Database = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _seed_asset_with_scenes(
    db: Database, project_id: str, name: str, *, segments: list[tuple[int, int, str]]
) -> str:
    """Asset + succeeded analysis run with *segments* (start_frame, end_frame, text) + a
    rough-cut timeline with one 1:1 clip over [0, 600) and two scenes [0,300)/[300,600)."""
    asset = repos.create_asset(
        db, project_id=project_id, type="video", display_name=name, source_path=f"/tmp/{name}"
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="t", config={}
    )
    repos.start_analysis_run(db, run["id"])
    for start, end, text in segments:
        repos.insert_segment_with_words(
            db,
            asset_id=asset["id"],
            run_id=run["id"],
            speaker_id=None,
            segment={
                "start_sample": start * 1600,
                "end_sample": end * 1600,
                "start_frame": start,
                "end_frame": end,
                "text": text,
                "confidence": 1.0,
            },
            words=[],
        )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    timeline = repos.create_timeline(
        db, project_id=project_id, name="Rough Cut", kind="rough_cut",
        created_from=asset["id"],
    )
    repos.add_timeline_clip(
        db, timeline_id=timeline["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=600,
        seq_in_frame=0, seq_out_frame_exclusive=600,
    )
    repos.replace_scenes(db, project_id, timeline["id"], [(0, 300), (300, 600)])
    return str(asset["id"])


def test_lexical_ranking_maps_hits_to_scenes_and_ranks_assets(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    strong = _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission"),
                  (320, 380, "agents execute the mission end to end")],
    )
    weak = _seed_asset_with_scenes(
        db, project["id"], "weak.mp4",
        segments=[(10, 60, "unrelated dashboard talk"),
                  (320, 380, "one mission mention only")],
    )

    out = discovery.search_material(db, project["id"], "mission")

    assert out["source"] == "lexical"
    assert [r["asset_id"] for r in out["ranking"]] == [strong, weak]
    top = out["ranking"][0]
    # scene 1 covers src [0,300), scene 2 covers [300,600)
    assert [h["scene_number"] for h in top["scene_hits"]] == [1, 2]
    assert "mission" in top["scene_hits"][0]["snippet"]
    assert out["skipped"] == []


def test_asset_without_rough_cut_is_skipped_not_created(
    tmp_path: Path, monkeypatch: Any
) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    # Asset with a transcript hit but NO rough-cut timeline at all.
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="raw.mp4",
        source_path="/tmp/raw.mp4",
    )
    run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="t", config={})
    repos.start_analysis_run(db, run["id"])
    repos.insert_segment_with_words(
        db, asset_id=asset["id"], run_id=run["id"], speaker_id=None,
        segment={"start_sample": 0, "end_sample": 16000, "start_frame": 0,
                 "end_frame": 50, "text": "mission talk", "confidence": 1.0},
        words=[],
    )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})

    out = discovery.search_material(db, project["id"], "mission")

    assert out["ranking"] == []
    assert out["skipped"] == [{"asset_id": str(asset["id"]), "reason": "no rough cut"}]
    # READ-ONLY invariant: discovery must not have created a timeline as a side effect.
    assert repos.get_asset_rough_cut(db, project["id"], str(asset["id"])) is None


def test_no_hits_is_an_empty_ranking_not_an_error(tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[(10, 60, "hello world")])

    out = discovery.search_material(db, project["id"], "quantum chromodynamics")

    assert out == {"source": "lexical", "ranking": [], "skipped": []}


def test_stale_run_segments_are_excluded_and_ranked_once(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A re-analysis leaves the OLD run's segments in transcript_segments — repos.search_transcript
    (the lexical path's backing query) must use only the asset's LATEST SUCCEEDED run, or the
    stale segment double-counts the score and shows up as a duplicate scene hit (the semantic
    path is immune: it deletes-before-reindexing)."""
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4",
        source_path="/tmp/a.mp4",
    )

    old_run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="1", config={})
    repos.start_analysis_run(db, old_run["id"])
    repos.insert_segment_with_words(
        db, asset_id=asset["id"], run_id=old_run["id"], speaker_id=None,
        segment={"start_sample": 16000, "end_sample": 32000, "start_frame": 10,
                 "end_frame": 60, "text": "mission old", "confidence": 1.0},
        words=[],
    )
    repos.finish_analysis_run(db, old_run["id"], status="succeeded", diagnostics={})

    new_run = repos.create_analysis_run(db, asset_id=asset["id"], pipeline_version="2", config={})
    repos.start_analysis_run(db, new_run["id"])
    repos.insert_segment_with_words(
        db, asset_id=asset["id"], run_id=new_run["id"], speaker_id=None,
        segment={"start_sample": 16000, "end_sample": 32000, "start_frame": 10,
                 "end_frame": 60, "text": "mission new", "confidence": 1.0},
        words=[],
    )
    repos.finish_analysis_run(db, new_run["id"], status="succeeded", diagnostics={})

    timeline = repos.create_timeline(
        db, project_id=project["id"], name="Rough Cut", kind="rough_cut",
        created_from=asset["id"],
    )
    repos.add_timeline_clip(
        db, timeline_id=timeline["id"], asset_id=asset["id"],
        src_in_frame=0, src_out_frame_exclusive=600,
        seq_in_frame=0, seq_out_frame_exclusive=600,
    )
    repos.replace_scenes(db, project["id"], timeline["id"], [(0, 300), (300, 600)])

    found = repos.search_transcript(db, project_id=project["id"], query="mission")
    assert [r["text"] for r in found] == ["mission new"]

    out = discovery.search_material(db, project["id"], "mission")
    assert len(out["ranking"]) == 1
    top = out["ranking"][0]
    assert len(top["scene_hits"]) == 1
    assert "mission new" in top["scene_hits"][0]["snippet"]


def test_raising_index_degrades_to_lexical_not_an_exception(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """get_index() itself can raise (e.g. a down Qdrant server refuses the connection during
    client/collection construction) — that must degrade to lexical exactly like a None index,
    never propagate out of search_material."""

    def _raise() -> None:
        raise RuntimeError("connection refused")

    monkeypatch.setattr(discovery, "get_index", _raise)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    _seed_asset_with_scenes(
        db, project["id"], "a.mp4", segments=[(10, 60, "the agent farm plans the mission")]
    )

    out = discovery.search_material(db, project["id"], "mission")

    assert out["source"] == "lexical"
    assert out["ranking"]


def test_scene_hits_carry_segment_frames_for_the_overview(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Auto-overview builds its windows from these frames (spec 2026-07-31 §2).

    ``end_frame_exclusive`` is the segment's ``end_frame`` verbatim: that column is ALREADY
    an exclusive out-point (mapping.map_segment snaps it with snap_out_to_frame/CEIL), so any
    +/-1 here would be an off-by-one.
    """
    monkeypatch.setattr(discovery, "get_index", lambda: None)  # force lexical
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    _seed_asset_with_scenes(
        db, project["id"], "a.mp4", segments=[(10, 60, "the agent farm plans the mission")]
    )

    out = discovery.search_material(db, project["id"], "mission")

    hit = out["ranking"][0]["scene_hits"][0]
    assert hit["start_frame"] == 10
    assert hit["end_frame_exclusive"] == 60
    # The Phase-1 keys stay exactly as they were.
    assert hit["scene_number"] == 1
    assert "mission" in hit["snippet"]


def test_semantic_ranking_used_when_index_answers(tmp_path: Path, monkeypatch: Any) -> None:
    """Semantic path: gated exactly like tests/test_semantic.py (optional [semantic] extra)."""
    import pytest

    pytest.importorskip("fastembed")
    pytest.importorskip("qdrant_client")
    from laura.semantic import SemanticIndex

    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(
        db, project["id"], "strong.mp4",
        segments=[(10, 60, "the agent farm plans the mission")],
    )

    index = SemanticIndex(":memory:")
    index.index([
        {
            "id": "seg1",
            "text": "the agent farm plans the mission",
            "payload": {
                "project_id": project["id"],
                "asset_id": asset_id,
                "segment_id": "seg1",
                "asset_name": "strong.mp4",
                "text": "the agent farm plans the mission",
                "start_frame": 10,
                "end_frame": 60,
                "speaker_label": None,
            },
        },
    ])
    monkeypatch.setattr(discovery, "get_index", lambda: index)

    out = discovery.search_material(db, project["id"], "mission planning")

    assert out["source"] == "semantic"
    assert out["ranking"]
    assert out["ranking"][0]["asset_id"] == asset_id


def _seed_run_with_segments(
    db: Database,
    asset_id: str,
    *,
    pipeline_version: str,
    status: str,
    texts: list[str],
) -> str:
    """A run with one segment per text at [10,60). ``status`` 'running' leaves it unfinished
    (the live shape: the handler crashed before finish_analysis_run)."""
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version=pipeline_version, config={}
    )
    repos.start_analysis_run(db, run["id"])
    for text in texts:
        repos.insert_segment_with_words(
            db, asset_id=asset_id, run_id=run["id"], speaker_id=None,
            segment={"start_sample": 16000, "end_sample": 32000, "start_frame": 10,
                     "end_frame": 60, "text": text, "confidence": 1.0},
            words=[],
        )
    if status != "running":
        repos.finish_analysis_run(db, run["id"], status=status, diagnostics={})
    return str(run["id"])


def test_transcript_stranded_on_unfinished_run_is_still_found(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The live shape (workspace-livetest): the ONLY transcript hangs on a run frozen in
    'running' (the handler crashed on an unreachable Qdrant before finish_analysis_run),
    while a LATER succeeded run re-analysed scenes only and carries zero segments."""
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[])
    stranded = _seed_run_with_segments(
        db, asset_id, pipeline_version="1", status="running", texts=["mission stranded"]
    )
    _seed_run_with_segments(db, asset_id, pipeline_version="2", status="succeeded", texts=[])

    run = repos.get_latest_transcript_run(db, asset_id)
    assert run is not None
    assert run["id"] == stranded

    found = repos.search_transcript(db, project_id=project["id"], query="mission")
    assert [r["text"] for r in found] == ["mission stranded"]

    out = discovery.search_material(db, project["id"], "mission")
    assert [r["asset_id"] for r in out["ranking"]] == [asset_id]


def test_succeeded_run_wins_over_newer_unfinished_run(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A re-analysis in flight has already written part of its segments. It must NOT shadow
    the previous complete transcript — succeeded outranks unfinished regardless of age."""
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[])
    complete = _seed_run_with_segments(
        db, asset_id, pipeline_version="1", status="succeeded", texts=["mission complete"]
    )
    _seed_run_with_segments(
        db, asset_id, pipeline_version="2", status="running", texts=["mission partial"]
    )

    run = repos.get_latest_transcript_run(db, asset_id)
    assert run is not None
    assert run["id"] == complete

    found = repos.search_transcript(db, project_id=project["id"], query="mission")
    assert [r["text"] for r in found] == ["mission complete"]


def test_search_rows_never_mix_two_runs(tmp_path: Path, monkeypatch: Any) -> None:
    """The exclusion property: three runs carry matching segments, exactly one is chosen."""
    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[])
    _seed_run_with_segments(
        db, asset_id, pipeline_version="1", status="succeeded", texts=["mission one"]
    )
    newest = _seed_run_with_segments(
        db, asset_id, pipeline_version="2", status="succeeded",
        texts=["mission two", "mission three"],
    )
    _seed_run_with_segments(
        db, asset_id, pipeline_version="3", status="running", texts=["mission four"]
    )

    found = repos.search_transcript(db, project_id=project["id"], query="mission")

    assert len(found) == 2
    with db.connection() as conn:
        run_ids = {
            str(
                conn.execute(
                    "SELECT analysis_run_id FROM transcript_segments WHERE id=?",
                    (row["segment_id"],),
                ).fetchone()["analysis_run_id"]
            )
            for row in found
        }
    assert run_ids == {newest}


def test_asset_without_any_segments_resolves_to_none(tmp_path: Path) -> None:
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4",
        source_path="/tmp/a.mp4",
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="1", config={}
    )
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})

    assert repos.get_latest_transcript_run(db, str(asset["id"])) is None


def test_words_resolve_to_the_transcript_run_not_the_latest_run(
    tmp_path: Path,
) -> None:
    """The two former word readers (shorts_handlers.py, timelines.py) resolved the run by
    recency and then read words off it — for an asset whose transcript is stranded on an
    unfinished run while a later re-analysis is scene-only, that lookup finds zero words even
    though a transcript exists. repos.get_latest_transcript_run must resolve the word-bearing
    run instead; the old recency lookup is asserted here too, so the difference is explicit."""
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4",
        source_path="/tmp/a.mp4",
    )

    stranded_run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="1", config={}
    )
    repos.start_analysis_run(db, stranded_run["id"])
    repos.insert_segment_with_words(
        db, asset_id=asset["id"], run_id=stranded_run["id"], speaker_id=None,
        segment={"start_sample": 16000, "end_sample": 32000, "start_frame": 10,
                 "end_frame": 60, "text": "mission stranded", "confidence": 1.0},
        words=[
            {"idx": 0, "start_sample": 16000, "end_sample": 20000,
             "start_frame": 10, "end_frame": 20, "text": "mission"},
            {"idx": 1, "start_sample": 20000, "end_sample": 32000,
             "start_frame": 20, "end_frame": 60, "text": "stranded"},
        ],
    )
    # Never finished (the handler crashed) -- left 'running' forever, the exact stranded shape.

    scene_only_run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="2", config={}
    )
    repos.start_analysis_run(db, scene_only_run["id"])
    repos.finish_analysis_run(db, scene_only_run["id"], status="succeeded", diagnostics={})

    # The old recency lookup: latest run is the scene-only one, and it carries no words.
    latest = repos.get_latest_analysis_run(db, asset["id"])
    assert latest is not None
    assert latest["id"] == scene_only_run["id"]
    assert repos.list_words_for_run(db, asset["id"], latest["id"]) == []

    # The new transcript-run lookup: resolves the stranded run, which has the words.
    transcript_run = repos.get_latest_transcript_run(db, asset["id"])
    assert transcript_run is not None
    assert transcript_run["id"] == stranded_run["id"]
    words = repos.list_words_for_run(db, asset["id"], str(transcript_run["id"]))
    assert [w["text"] for w in words] == ["mission", "stranded"]


def test_reader_and_search_resolve_the_same_run(tmp_path: Path, monkeypatch: Any) -> None:
    """Coherence: discovery ranks the asset off the stranded run, so the scout's context
    reader must find that same transcript. Otherwise the chain contradicts itself — search
    says 'here is your material', get_scene_context says 'no transcript'."""
    from laura.short_creator import context

    monkeypatch.setattr(discovery, "get_index", lambda: None)
    db = _db(tmp_path)
    project = repos.create_project(
        db, name="p", rate_num=FPS, rate_den=1, drop_frame=False, workspace_root="/tmp/p"
    )
    asset_id = _seed_asset_with_scenes(db, project["id"], "a.mp4", segments=[])
    _seed_run_with_segments(
        db, asset_id, pipeline_version="1", status="running", texts=["mission stranded"]
    )
    _seed_run_with_segments(db, asset_id, pipeline_version="2", status="succeeded", texts=[])

    window = context.transcript_window(db, asset_id, center_frame=30)

    assert window["ok"] is True
    assert "mission stranded" in window["text"]
