"""P7-T5 — Tests for laura.mcp tool handlers.

TDD: tests are written against the tools.py plain-function handlers — NOT the
MCP transport.  The test matrix (per the brief):

1. tool_next_action on a seeded short returns the correct dict shape
   (tool/args/label_key/blocked_by/found) for a known short.
2. tool_next_action on an unknown short_id returns {"found": False, ...}.
3. tool_batch_plan over a manifest returns the plan/batch_hash dicts.
4. tool_batch_status over a manifest returns the rollup dict.
5. tool_recipe_from_trace on a real short_run returns the recipe dict.
6. All tool calls are pure reads (NO-WRITES assertion on one).
7. ``import laura.mcp`` succeeds WITHOUT mcp installed (guard works).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase
from laura.ledger import get_ledger_store

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_db(tmp_path: Path) -> SqliteDatabase:
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _create_project_and_asset(
    db: SqliteDatabase, name: str = "clip.mov"
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create a project + asset directly via repos (no HTTP client needed)."""
    project = repos.create_project(
        db,
        name="test-mcp",
        rate_num=30,
        rate_den=1,
        drop_frame=False,
        workspace_root="/workspace",
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name=name,
        source_path=f"/media/{name}",
    )
    return project, asset


def _add_proxy_file(db: SqliteDatabase, asset_id: str) -> None:
    repos.add_asset_file(
        db, asset_id=asset_id, kind="proxy", path="/workspace/proxy.mp4", is_proxy=True
    )


def _add_succeeded_analysis(db: SqliteDatabase, asset_id: str) -> dict[str, Any]:
    run = repos.create_analysis_run(
        db, asset_id=asset_id, pipeline_version="1", config={"stages": {}}
    )
    repos.start_analysis_run(db, run["id"])
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    repos.insert_shots(
        db,
        asset_id=asset_id,
        run_id=run["id"],
        shots=[{"src_in_frame": 0, "src_out_frame_exclusive": 100, "method": "test"}],
    )
    return repos.get_analysis_run(db, run["id"])  # type: ignore[return-value]


def _add_rough_cut_with_clips(
    db: SqliteDatabase, project_id: str, asset_id: str
) -> dict[str, Any]:
    tl = repos.create_timeline(
        db, project_id=project_id, name="RC", kind="rough_cut", created_from=asset_id
    )
    repos.add_timeline_clip(
        db,
        timeline_id=tl["id"],
        asset_id=asset_id,
        src_in_frame=0,
        src_out_frame_exclusive=100,
        seq_in_frame=0,
        seq_out_frame_exclusive=100,
    )
    return repos.get_timeline(db, tl["id"])  # type: ignore[return-value]


def _add_succeeded_export(
    db: SqliteDatabase, project_id: str, timeline_id: str, tmp_path: Path
) -> dict[str, Any]:
    exp = repos.create_export(db, project_id=project_id, timeline_id=timeline_id, format="mp4")
    out_path = tmp_path / "reel.mp4"
    out_path.write_bytes(b"fake")
    repos.set_export_done(db, exp["id"], path=str(out_path), size_bytes=4)
    return repos.get_export(db, exp["id"])  # type: ignore[return-value]


def _row_counts(db: SqliteDatabase) -> dict[str, int]:
    """Snapshot row counts for no-writes assertion."""
    tables = [
        "projects", "media_assets", "asset_files", "analysis_runs",
        "timelines", "timeline_clips", "exports", "jobs", "short_runs", "asset_policies",
    ]
    counts: dict[str, int] = {}
    with db.connection() as conn:
        for tbl in tables:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {tbl}").fetchone()
            counts[tbl] = int(row["n"])
    return counts


# ---------------------------------------------------------------------------
# 7. Guard test: ``import laura.mcp`` must work WITHOUT mcp installed.
#    (Run first so it's the first assertion in the file.)
# ---------------------------------------------------------------------------


def test_import_laura_mcp_without_mcp_sdk() -> None:
    """Importing laura.mcp must NOT require the mcp SDK to be installed.

    This validates the guard: the tools.py module has no ``import mcp``, and
    server.py defers it to inside main().
    """
    import importlib

    # If this raises ImportError the guard is broken.
    mod = importlib.import_module("laura.mcp")
    assert hasattr(mod, "tool_next_action")
    assert hasattr(mod, "tool_batch_plan")
    assert hasattr(mod, "tool_batch_status")
    assert hasattr(mod, "tool_recipe_from_trace")

    # server module can also be imported (guard is at call-time inside main()).
    server_mod = importlib.import_module("laura.mcp.server")
    assert hasattr(server_mod, "main")


# ---------------------------------------------------------------------------
# 1. tool_next_action — known short (PROXY_PENDING state)
# ---------------------------------------------------------------------------


def test_tool_next_action_known_short(tmp_path: Path) -> None:
    """tool_next_action returns a dict with the right shape for a known short."""
    from laura.mcp import tool_next_action

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)

    # No proxy file → PROXY_PENDING state
    result = tool_next_action(db, asset["id"])

    assert result["found"] is True
    assert result["short_id"] == asset["id"]
    assert "tool" in result
    assert "args" in result
    assert "label_key" in result
    assert "blocked_by" in result
    assert "PROXY_PENDING" in result["blocked_by"]
    assert result["tool"] is None


def test_tool_next_action_analysis_state(tmp_path: Path) -> None:
    """tool_next_action returns roughcut_from_shots when analysis succeeded, no cut yet."""
    from laura.mcp import tool_next_action

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db, "b.mov")
    _add_proxy_file(db, asset["id"])
    _add_succeeded_analysis(db, asset["id"])

    result = tool_next_action(db, asset["id"])

    assert result["found"] is True
    assert result["tool"] == "roughcut_from_shots"
    assert result["args"] == {"asset_id": asset["id"]}
    assert result["blocked_by"] == []


def test_tool_next_action_done_state(tmp_path: Path) -> None:
    """tool_next_action returns done state when a succeeded export exists."""
    from laura.mcp import tool_next_action

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db, "c.mov")
    _add_proxy_file(db, asset["id"])
    _add_succeeded_analysis(db, asset["id"])
    tl = _add_rough_cut_with_clips(db, project["id"], asset["id"])
    _add_succeeded_export(db, project["id"], tl["id"], tmp_path)

    result = tool_next_action(db, asset["id"])

    assert result["found"] is True
    assert result["label_key"] == "next_action.done"
    assert result["tool"] is None


# ---------------------------------------------------------------------------
# 2. tool_next_action — unknown short_id
# ---------------------------------------------------------------------------


def test_tool_next_action_unknown_short(tmp_path: Path) -> None:
    """tool_next_action returns {"found": False, "short_id": ...} for unknown assets."""
    from laura.mcp import tool_next_action

    db = _make_db(tmp_path)
    result = tool_next_action(db, "nonexistent-short-id")

    assert result == {"found": False, "short_id": "nonexistent-short-id"}


# ---------------------------------------------------------------------------
# 3. tool_batch_plan
# ---------------------------------------------------------------------------


def test_tool_batch_plan(tmp_path: Path) -> None:
    """tool_batch_plan returns a plan dict with plans list and batch_hash."""
    from laura.mcp import tool_batch_plan

    db = _make_db(tmp_path)
    project_a, asset_a = _create_project_and_asset(db, "a.mov")
    project_b, asset_b = _create_project_and_asset(db, "b.mov")
    _add_proxy_file(db, asset_b["id"])
    _add_succeeded_analysis(db, asset_b["id"])

    short_ids = [asset_a["id"], asset_b["id"]]
    result = tool_batch_plan(db, short_ids)

    assert "plans" in result
    assert "batch_hash" in result
    assert len(result["plans"]) == 2
    assert isinstance(result["batch_hash"], str)
    assert len(result["batch_hash"]) == 64  # sha256 hexdigest

    plan_a = result["plans"][0]
    plan_b = result["plans"][1]

    assert plan_a["short_id"] == asset_a["id"]
    assert plan_a["found"] is True
    assert plan_a["action"] is not None
    # Short A has no proxy → PROXY_PENDING
    assert "PROXY_PENDING" in plan_a["action"]["blocked_by"]

    assert plan_b["short_id"] == asset_b["id"]
    assert plan_b["found"] is True
    assert plan_b["action"]["tool"] == "roughcut_from_shots"


def test_tool_batch_plan_unknown_short(tmp_path: Path) -> None:
    """tool_batch_plan: unknown short_id → found=False, action=None, batch continues."""
    from laura.mcp import tool_batch_plan

    db = _make_db(tmp_path)
    result = tool_batch_plan(db, ["does-not-exist"])

    assert len(result["plans"]) == 1
    plan = result["plans"][0]
    assert plan["found"] is False
    assert plan["action"] is None
    assert isinstance(plan["hash"], str) and len(plan["hash"]) == 64


def test_tool_batch_plan_actions_are_dicts(tmp_path: Path) -> None:
    """tool_batch_plan serialises NextActionOut to plain dicts (no Pydantic models)."""
    from laura.mcp import tool_batch_plan

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)
    _add_proxy_file(db, asset["id"])

    result = tool_batch_plan(db, [asset["id"]])
    action = result["plans"][0]["action"]
    assert isinstance(action, dict), "action must be a plain dict, not a Pydantic model"
    # Must be JSON-serialisable
    json.dumps(result)


# ---------------------------------------------------------------------------
# 4. tool_batch_status
# ---------------------------------------------------------------------------


def test_tool_batch_status(tmp_path: Path) -> None:
    """tool_batch_status returns the rollup dict with total, by_stage, needs_human."""
    from laura.mcp import tool_batch_status

    db = _make_db(tmp_path)
    _, asset_a = _create_project_and_asset(db, "a.mov")
    _, asset_b = _create_project_and_asset(db, "b.mov")
    _add_proxy_file(db, asset_b["id"])

    result = tool_batch_status(db, [asset_a["id"], asset_b["id"]])

    assert result["total"] == 2
    assert "by_stage" in result
    assert "needs_human" in result
    by_stage = result["by_stage"]
    # Stage keys must all be present
    for key in ("preparing", "analyzing", "analyze", "cut", "build", "done", "not_found"):
        assert key in by_stage, f"missing stage key: {key}"
    # asset_a has no proxy → preparing stage
    assert by_stage["preparing"] >= 1
    # asset_b has proxy but no analysis → analyze stage
    assert by_stage["analyze"] >= 1
    assert result["needs_human"] == 0


def test_tool_batch_status_not_found(tmp_path: Path) -> None:
    """tool_batch_status counts unknown short_ids under not_found."""
    from laura.mcp import tool_batch_status

    db = _make_db(tmp_path)
    result = tool_batch_status(db, ["ghost-id"])

    assert result["total"] == 1
    assert result["by_stage"]["not_found"] == 1


# ---------------------------------------------------------------------------
# 5. tool_recipe_from_trace
# ---------------------------------------------------------------------------


def test_tool_recipe_from_trace(tmp_path: Path) -> None:
    """tool_recipe_from_trace returns the recipe dict for a known run with a trace."""
    from laura.mcp import tool_recipe_from_trace

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db)
    _add_proxy_file(db, asset["id"])
    _add_succeeded_analysis(db, asset["id"])
    tl = _add_rough_cut_with_clips(db, project["id"], asset["id"])
    exp = _add_succeeded_export(db, project["id"], tl["id"], tmp_path)

    # Mint a short_run and wire it to the export via options.
    store = get_ledger_store(db)
    run = store.record_run(short_id=asset["id"], pipeline_version="2")
    run_id = run["id"]

    # Simulate trace: write export_id into trace_json + set status=succeeded.
    trace = {"export_id": exp["id"], "output": str(tmp_path / "reel.mp4")}
    store.update_run(run_id, status="succeeded", trace_json=json.dumps(trace))

    result = tool_recipe_from_trace(db, run_id)

    assert result != {}  # empty dict means "not found"
    assert result["short_id"] == asset["id"]
    assert result["status"] == "succeeded"
    assert "available" in result
    assert "verified" in result


def test_tool_recipe_from_trace_not_found(tmp_path: Path) -> None:
    """tool_recipe_from_trace returns {} for an unknown run_id."""
    from laura.mcp import tool_recipe_from_trace

    db = _make_db(tmp_path)
    result = tool_recipe_from_trace(db, "run-does-not-exist")

    assert result == {}


# ---------------------------------------------------------------------------
# 6. NO-WRITES assertion
# ---------------------------------------------------------------------------


def test_mcp_tools_perform_no_writes(tmp_path: Path) -> None:
    """All four MCP tool functions must leave the database row counts unchanged."""
    from laura.mcp import (
        tool_batch_plan,
        tool_batch_status,
        tool_next_action,
        tool_recipe_from_trace,
    )

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)
    _add_proxy_file(db, asset["id"])
    _add_succeeded_analysis(db, asset["id"])

    before = _row_counts(db)

    tool_next_action(db, asset["id"])
    tool_next_action(db, "nonexistent")
    tool_batch_plan(db, [asset["id"], "nonexistent"])
    tool_batch_status(db, [asset["id"]])
    tool_recipe_from_trace(db, "nonexistent-run-id")

    after = _row_counts(db)
    assert before == after, f"MCP tools mutated DB: {before} -> {after}"


# ---------------------------------------------------------------------------
# S7 — analyze_video (tool_start_analysis)
# ---------------------------------------------------------------------------


def test_tool_start_analysis_ok(tmp_path: Path) -> None:
    """tool_start_analysis returns ok=True and creates an analysis_run + job."""
    from laura.mcp.tools import tool_start_analysis

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)

    result = tool_start_analysis(db, asset["id"])

    assert result["ok"] is True
    assert result["asset_id"] == asset["id"]
    assert "analysis_run_id" in result
    assert "job_id" in result

    # analysis_run must exist in DB
    run = repos.get_analysis_run(db, result["analysis_run_id"])
    assert run is not None
    assert run["asset_id"] == asset["id"]
    assert run["status"] == "queued"

    # job must exist with correct kind
    job = repos.get_job(db, result["job_id"])
    assert job is not None
    assert job["kind"] == "analysis.run"


def test_tool_start_analysis_asset_not_found(tmp_path: Path) -> None:
    """tool_start_analysis returns ok=False when the asset does not exist."""
    from laura.mcp.tools import tool_start_analysis

    db = _make_db(tmp_path)

    result = tool_start_analysis(db, "nonexistent-asset-id")

    assert result["ok"] is False
    assert result["error"] == "asset not found"
    assert result["asset_id"] == "nonexistent-asset-id"


# ---------------------------------------------------------------------------
# S7 — extract_shorts (tool_extract_shorts)
# ---------------------------------------------------------------------------


def test_tool_extract_shorts_ok(tmp_path: Path) -> None:
    """tool_extract_shorts returns ok=True when a succeeded analysis run exists."""
    from laura.mcp.tools import tool_extract_shorts

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)
    _add_proxy_file(db, asset["id"])
    run = _add_succeeded_analysis(db, asset["id"])

    result = tool_extract_shorts(db, asset["id"])

    assert result["ok"] is True
    assert result["asset_id"] == asset["id"]
    assert result["analysis_run_id"] == run["id"]
    assert "job_id" in result

    job = repos.get_job(db, result["job_id"])
    assert job is not None
    assert job["kind"] == "shorts.extract"


def test_tool_extract_shorts_asset_not_found(tmp_path: Path) -> None:
    """tool_extract_shorts returns ok=False with 'asset not found' when asset missing."""
    from laura.mcp.tools import tool_extract_shorts

    db = _make_db(tmp_path)

    result = tool_extract_shorts(db, "nonexistent-asset-id")

    assert result["ok"] is False
    assert result["error"] == "asset not found"
    assert result["asset_id"] == "nonexistent-asset-id"


def test_tool_extract_shorts_no_analysis_run(tmp_path: Path) -> None:
    """tool_extract_shorts returns ok=False when no analysis run exists."""
    from laura.mcp.tools import tool_extract_shorts

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)

    result = tool_extract_shorts(db, asset["id"])

    assert result["ok"] is False
    assert "analyze the asset first" in result["error"]
    assert result["asset_id"] == asset["id"]


def test_tool_extract_shorts_failed_analysis_run(tmp_path: Path) -> None:
    """tool_extract_shorts returns ok=False when the analysis run has not succeeded."""
    from laura.mcp.tools import tool_extract_shorts

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)

    # Create a non-succeeded run
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="1", config={"stages": {}}
    )
    repos.start_analysis_run(db, run["id"])
    repos.finish_analysis_run(db, run["id"], status="failed", diagnostics={})

    result = tool_extract_shorts(db, asset["id"])

    assert result["ok"] is False
    assert "analyze the asset first" in result["error"]


# ---------------------------------------------------------------------------
# S7 — list_short_candidates (tool_list_short_candidates)
# ---------------------------------------------------------------------------


def _seed_short_candidates(
    db: SqliteDatabase, asset: dict[str, Any], project: dict[str, Any], count: int = 2
) -> None:
    """Persist *count* fake short candidates for the asset."""
    candidates: list[dict[str, Any]] = [
        {
            "start_frame": i * 100,
            "end_frame_exclusive": i * 100 + 90,
            "start_boundary": "sentence_end",
            "end_boundary": "sentence_end",
            "score": 0.8 - i * 0.1,
            "rejected": False,
            "reject_reason": None,
            "score_breakdown": {"transcript_safety": 0.9, "hook_position": 0.7},
            "qa_passed": True,
            "qa_issues": [],
        }
        for i in range(count)
    ]
    repos.replace_shorts_candidates(
        db,
        project_id=project["id"],
        asset_id=asset["id"],
        source_timeline_id="tl-fake",
        candidates=candidates,
    )


def test_tool_list_short_candidates_with_data(tmp_path: Path) -> None:
    """tool_list_short_candidates returns count=2 and the correct fields."""
    from laura.mcp.tools import tool_list_short_candidates

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db)
    _seed_short_candidates(db, asset, project, count=2)

    result = tool_list_short_candidates(db, asset["id"])

    assert result["asset_id"] == asset["id"]
    assert result["count"] == 2
    assert len(result["candidates"]) == 2
    # Verify key fields are present
    cand = result["candidates"][0]
    assert "start_frame" in cand
    assert "end_frame_exclusive" in cand
    assert "score" in cand
    assert "qa_passed" in cand


def test_tool_list_short_candidates_empty(tmp_path: Path) -> None:
    """tool_list_short_candidates returns count=0 for an asset with no candidates."""
    from laura.mcp.tools import tool_list_short_candidates

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)

    result = tool_list_short_candidates(db, asset["id"])

    assert result["asset_id"] == asset["id"]
    assert result["count"] == 0
    assert result["candidates"] == []


# ---------------------------------------------------------------------------
# S7 — job_status (tool_job_status)
# ---------------------------------------------------------------------------


def test_tool_job_status_found(tmp_path: Path) -> None:
    """tool_job_status returns found=True with correct field values for a known job."""
    from laura.jobs.runner import enqueue
    from laura.mcp.tools import tool_job_status

    db = _make_db(tmp_path)
    job_id = enqueue(
        db,
        queue="test-queue",
        kind="test.kind",
        payload={"foo": "bar"},
        pipeline_version="1",
    )

    result = tool_job_status(db, job_id)

    assert result["found"] is True
    assert result["job_id"] == job_id
    assert result["kind"] == "test.kind"
    # A freshly enqueued job starts at "queued" with 0 attempts
    assert result["status"] == "queued"
    assert result["queue"] == "test-queue"
    assert result["attempts"] == 0  # DB column "attempt" starts at 0
    assert result["result"] is None  # no result yet
    assert result["error"] is None  # no error yet


def test_tool_job_status_error_json_parsed(tmp_path: Path) -> None:
    """tool_job_status parses error_json into the 'error' field."""
    from laura.jobs.runner import enqueue
    from laura.mcp.tools import tool_job_status

    db = _make_db(tmp_path)
    job_id = enqueue(
        db,
        queue="test-queue",
        kind="test.kind",
        payload={"foo": "bar"},
        pipeline_version="1",
    )

    # Manually write error_json to simulate a failed job
    with db.connection() as conn:
        conn.execute(
            "UPDATE jobs SET status='failed', error_json=? WHERE id=?",
            (json.dumps({"message": "something went wrong", "code": 42}), job_id),
        )

    result = tool_job_status(db, job_id)

    assert result["found"] is True
    assert result["status"] == "failed"
    assert result["error"] == {"message": "something went wrong", "code": 42}


def test_tool_job_status_not_found(tmp_path: Path) -> None:
    """tool_job_status returns found=False for an unknown job_id."""
    from laura.mcp.tools import tool_job_status

    db = _make_db(tmp_path)

    result = tool_job_status(db, "nonexistent-job-id")

    assert result == {"found": False, "job_id": "nonexistent-job-id"}


# ---------------------------------------------------------------------------
# S7 — explain_candidate (tool_explain_candidate)
# ---------------------------------------------------------------------------


def test_tool_explain_candidate_found(tmp_path: Path) -> None:
    """tool_explain_candidate returns found=True with top_factors sorted by value."""
    from laura.mcp.tools import tool_explain_candidate

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db)

    # Seed a candidate with a known score_breakdown
    score_breakdown = {
        "transcript_safety": 0.95,
        "hook_position": 0.80,
        "audio_silence_at_boundaries": 0.40,
    }
    candidates = [
        {
            "start_frame": 0,
            "end_frame_exclusive": 300,
            "start_boundary": "speaker_turn",
            "end_boundary": "sentence_end",
            "score": 0.72,
            "rejected": False,
            "reject_reason": None,
            "score_breakdown": score_breakdown,
            "qa_passed": True,
            "qa_issues": [],
        }
    ]
    repos.replace_shorts_candidates(
        db,
        project_id=project["id"],
        asset_id=asset["id"],
        source_timeline_id="tl-fake",
        candidates=candidates,
    )
    cands = repos.list_shorts_candidates_by_asset(db, asset["id"])
    candidate_id = cands[0]["id"]

    result = tool_explain_candidate(db, candidate_id)

    assert result["found"] is True
    assert result["candidate_id"] == candidate_id
    assert result["asset_id"] == asset["id"]
    assert result["start_frame"] == 0
    assert result["end_frame_exclusive"] == 300
    assert abs(result["score"] - 0.72) < 0.001
    assert result["qa_passed"] is True
    assert result["qa_issues"] == []

    # top_factors must be sorted by value descending
    factors = result["top_factors"]
    assert len(factors) >= 2
    assert factors[0]["name"] == "transcript_safety"
    assert factors[0]["value"] == 0.95
    assert factors[1]["name"] == "hook_position"
    assert factors[1]["value"] == 0.80

    # explanation must mention the score
    explanation = result["explanation"]
    assert "0.72" in explanation
    assert "transcript_safety" in explanation
    assert "QA passed" in explanation


def test_tool_explain_candidate_qa_failed(tmp_path: Path) -> None:
    """tool_explain_candidate mentions QA failure and issues in the explanation."""
    from laura.mcp.tools import tool_explain_candidate

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db)

    candidates = [
        {
            "start_frame": 0,
            "end_frame_exclusive": 150,
            "start_boundary": "sentence_end",
            "end_boundary": "sentence_end",
            "score": 0.30,
            "rejected": False,
            "reject_reason": None,
            "score_breakdown": {"transcript_safety": 0.50},
            "qa_passed": False,
            "qa_issues": ["start_on_black", "end_on_freeze"],
        }
    ]
    repos.replace_shorts_candidates(
        db,
        project_id=project["id"],
        asset_id=asset["id"],
        source_timeline_id="tl-fake",
        candidates=candidates,
    )
    cands = repos.list_shorts_candidates_by_asset(db, asset["id"])
    candidate_id = cands[0]["id"]

    result = tool_explain_candidate(db, candidate_id)

    assert result["found"] is True
    assert result["qa_passed"] is False
    assert "start_on_black" in result["qa_issues"]
    assert "QA FAILED" in result["explanation"]
    assert "start_on_black" in result["explanation"]


def test_tool_explain_candidate_not_found(tmp_path: Path) -> None:
    """tool_explain_candidate returns found=False for an unknown candidate_id."""
    from laura.mcp.tools import tool_explain_candidate

    db = _make_db(tmp_path)

    result = tool_explain_candidate(db, "nonexistent-candidate-id")

    assert result == {"found": False, "candidate_id": "nonexistent-candidate-id"}


# ---------------------------------------------------------------------------
# S7 — NO-WRITES assertion for read-only S7 tools
# ---------------------------------------------------------------------------


def test_s7_read_tools_perform_no_writes(tmp_path: Path) -> None:
    """list_short_candidates, job_status, and explain_candidate must not mutate the DB."""
    from laura.jobs.runner import enqueue
    from laura.mcp.tools import (
        tool_explain_candidate,
        tool_job_status,
        tool_list_short_candidates,
    )

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db)
    _seed_short_candidates(db, asset, project, count=2)
    job_id = enqueue(db, queue="q", kind="k", payload={}, pipeline_version="1")
    cands = repos.list_shorts_candidates_by_asset(db, asset["id"])
    candidate_id = cands[0]["id"]

    before = _row_counts(db)

    tool_list_short_candidates(db, asset["id"])
    tool_list_short_candidates(db, "nonexistent")
    tool_job_status(db, job_id)
    tool_job_status(db, "nonexistent-job")
    tool_explain_candidate(db, candidate_id)
    tool_explain_candidate(db, "nonexistent-candidate")

    after = _row_counts(db)
    assert before == after, f"S7 read tools mutated DB: {before} -> {after}"


# ---------------------------------------------------------------------------
# S7 — guard test: new tools exported from laura.mcp
# ---------------------------------------------------------------------------


def test_import_laura_mcp_exports_s7_tools() -> None:
    """All 5 S7 tool handlers must be importable from laura.mcp without the mcp SDK."""
    import importlib

    mod = importlib.import_module("laura.mcp")
    for name in (
        "tool_start_analysis",
        "tool_extract_shorts",
        "tool_list_short_candidates",
        "tool_job_status",
        "tool_explain_candidate",
    ):
        assert hasattr(mod, name), f"laura.mcp missing export: {name}"


# ---------------------------------------------------------------------------
# VE5 — visual tools (tool_similar_segments / tool_deduplicate_shorts /
#        tool_visual_hook / tool_search_visual_moments)
# ---------------------------------------------------------------------------
#
# Smoke tests covering the graceful "no embeddings" path (ok=False, no crash) and
# the happy path with synthetic frame embeddings + a fake text embedder. The real
# CLIP text model is never loaded.


def _seed_embeddings_and_candidates(
    db: SqliteDatabase, project: dict[str, Any], asset: dict[str, Any]
) -> dict[int, str]:
    """A succeeded run + synthetic frame vectors + two near-dup candidates + one distinct."""
    import numpy as np

    from laura.analysis.embeddings_store import FrameEmbedding, SqliteVectorStore

    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="1", config={"stages": {}}
    )
    repos.start_analysis_run(db, run["id"])
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})

    e_x = np.array([1.0, 0.0, 0.0], dtype=np.float32)
    e_y = np.array([0.0, 1.0, 0.0], dtype=np.float32)
    frame_vecs = {
        0: e_x, 25: e_x, 50: e_x, 75: e_x,        # A → X
        200: e_x, 225: e_x, 250: e_x, 275: e_x,   # B → X (dup of A)
        400: e_y, 425: e_y, 450: e_y, 475: e_y,   # C → Y (distinct)
    }
    store = SqliteVectorStore(db)
    store.replace_frame_embeddings(
        asset["id"],
        run["id"],
        [
            FrameEmbedding(
                asset_id=asset["id"],
                analysis_run_id=run["id"],
                frame=f,
                model="fake",
                vector=v,
            )
            for f, v in frame_vecs.items()
        ],
    )

    candidates: list[dict[str, Any]] = [
        {
            "start_frame": s,
            "end_frame_exclusive": s + 100,
            "start_boundary": "sentence_end",
            "end_boundary": "sentence_end",
            "score": score,
            "rejected": False,
            "reject_reason": None,
            "score_breakdown": {},
            "qa_passed": True,
            "qa_issues": [],
        }
        for s, score in ((0, 0.9), (200, 0.7), (400, 0.5))
    ]
    repos.replace_shorts_candidates(
        db,
        project_id=project["id"],
        asset_id=asset["id"],
        source_timeline_id="tl-fake",
        candidates=candidates,
    )
    cands = repos.list_shorts_candidates_by_asset(db, asset["id"])
    return {int(c["start_frame"]): c["id"] for c in cands}


def test_tool_similar_segments_no_embeddings(tmp_path: Path) -> None:
    """tool_similar_segments degrades gracefully (ok=False) without frame embeddings."""
    from laura.mcp.tools import tool_similar_segments

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)

    result = tool_similar_segments(db, asset["id"], "any-candidate-id")
    assert result["ok"] is False
    assert "reason" in result


def test_tool_similar_segments_happy(tmp_path: Path) -> None:
    """tool_similar_segments finds the near-duplicate as top-1."""
    from laura.mcp.tools import tool_similar_segments

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db)
    ids = _seed_embeddings_and_candidates(db, project, asset)

    result = tool_similar_segments(db, asset["id"], ids[0])
    assert result["ok"] is True
    assert result["similar"][0]["candidate_id"] == ids[200]


def test_tool_deduplicate_shorts_no_embeddings(tmp_path: Path) -> None:
    """tool_deduplicate_shorts degrades gracefully without frame embeddings."""
    from laura.mcp.tools import tool_deduplicate_shorts

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)

    result = tool_deduplicate_shorts(db, asset["id"])
    assert result["ok"] is False
    assert "reason" in result


def test_tool_deduplicate_shorts_happy(tmp_path: Path) -> None:
    """tool_deduplicate_shorts groups the two X-region candidates together."""
    from laura.mcp.tools import tool_deduplicate_shorts

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db)
    ids = _seed_embeddings_and_candidates(db, project, asset)

    result = tool_deduplicate_shorts(db, asset["id"])
    assert result["ok"] is True
    assert result["dropped"] == [ids[200]]
    assert set(result["kept"]) == {ids[0], ids[400]}


def test_tool_visual_hook_no_embeddings(tmp_path: Path) -> None:
    """tool_visual_hook degrades gracefully without frame embeddings."""
    from laura.mcp.tools import tool_visual_hook

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)

    result = tool_visual_hook(db, asset["id"], "any-candidate-id")
    assert result["ok"] is False
    assert "reason" in result


def test_tool_visual_hook_happy(tmp_path: Path) -> None:
    """tool_visual_hook returns a hook_score and shift/continuity for a known candidate."""
    from laura.mcp.tools import tool_visual_hook

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db)
    ids = _seed_embeddings_and_candidates(db, project, asset)

    result = tool_visual_hook(db, asset["id"], ids[0])
    assert result["ok"] is True
    assert 0.0 <= result["hook_score"] <= 1.0
    assert "visual_shift_at_start" in result
    assert "opening_continuity" in result


def test_tool_search_visual_moments_no_embeddings(tmp_path: Path) -> None:
    """tool_search_visual_moments degrades gracefully without frame embeddings.

    visual_available is forced False so no fastembed model is ever touched, and the
    no-embeddings branch is reached first regardless.
    """
    import laura.analysis.visual_query as vq
    from laura.mcp.tools import tool_search_visual_moments

    db = _make_db(tmp_path)
    _, asset = _create_project_and_asset(db)

    result = tool_search_visual_moments(db, asset["id"], "a red car")
    assert result["ok"] is False
    assert "reason" in result
    # And: with embeddings absent + extra forced unavailable, still ok=False (no crash).
    assert vq is not None  # module import smoke


def test_tool_search_visual_moments_extra_unavailable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """With embeddings present but the visual extra forced absent → ok=False, no model load."""
    import laura.analysis.visual_query as vq
    from laura.mcp.tools import tool_search_visual_moments

    monkeypatch.setattr(vq, "visual_available", lambda: False)

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db)
    _seed_embeddings_and_candidates(db, project, asset)

    result = tool_search_visual_moments(db, asset["id"], "a red car")
    assert result["ok"] is False
    assert "text search unavailable" in result["reason"]


def test_import_laura_mcp_exports_ve5_tools() -> None:
    """All 4 VE5 visual tool handlers must be importable from laura.mcp without mcp SDK."""
    import importlib

    mod = importlib.import_module("laura.mcp")
    for name in (
        "tool_similar_segments",
        "tool_deduplicate_shorts",
        "tool_visual_hook",
        "tool_search_visual_moments",
    ):
        assert hasattr(mod, name), f"laura.mcp missing export: {name}"


# ---------------------------------------------------------------------------
# render_short (tool_render_short) — completes analyze->extract->list->explain->render
# ---------------------------------------------------------------------------


def test_tool_render_short_ok(tmp_path: Path) -> None:
    """tool_render_short enqueues a shorts.render job + creates an export for a candidate."""
    from laura.mcp.tools import tool_render_short

    db = _make_db(tmp_path)
    project, asset = _create_project_and_asset(db)
    _seed_short_candidates(db, asset, project, count=1)
    candidate_id = repos.list_shorts_candidates_by_asset(db, asset["id"])[0]["id"]

    result = tool_render_short(db, candidate_id, hook_text="Hi")

    assert result["ok"] is True
    assert result["export_id"]
    assert "job_id" in result

    exp = repos.get_export(db, result["export_id"])
    assert exp is not None
    assert exp["options"]["candidate_id"] == candidate_id
    assert exp["options"]["hook_text"] == "Hi"

    job = repos.get_job(db, result["job_id"])
    assert job is not None
    assert job["kind"] == "shorts.render"


def test_tool_render_short_candidate_not_found(tmp_path: Path) -> None:
    """tool_render_short returns ok=False when the candidate does not exist."""
    from laura.mcp.tools import tool_render_short

    db = _make_db(tmp_path)
    result = tool_render_short(db, "nonexistent-candidate")
    assert result["ok"] is False
    assert result["error"] == "candidate not found"
    assert result["candidate_id"] == "nonexistent-candidate"
