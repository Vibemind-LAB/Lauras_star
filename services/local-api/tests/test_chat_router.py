"""Chat router: one turn, one tool call, never a crash (spec 2026-08-03).

Same seam design as scout.run_scout: runner is injectable, a bad reply gets exactly one
retry with the validation error appended, and a runner exception goes straight to the
deterministic fallback (a reply asking to rephrase) — the thread must never 500 on a turn.
"""
from __future__ import annotations

import json

import pytest

from laura.chat.router import TOOLS, compose_context, run_router
from laura.short_creator.providers import AgentConfig


def _config() -> AgentConfig:
    return AgentConfig(
        provider="openai-compat",
        agent_model="test",
        orchestrator_model="test",
        orchestration="magentic",
        escalate_provider="openai-compat",
        escalate_model="test",
        auto_escalate=False,
        qa_max_rounds=2,
        nine_router_base_url="http://localhost:20128/v1",
        nine_router_api_key=None,
        openai_base_url=None,
        openai_api_key="k",
    )


def test_valid_tool_call_passes_through() -> None:
    reply = json.dumps({"tool": "start_short", "args": {"topic": "Automatisierung"}})
    decision = run_router(
        _config(), context="", user_text="bau mir was", runner=lambda _t: reply
    )
    assert decision == {
        "tool": "start_short",
        "args": {"topic": "Automatisierung"},
        "fallback": False,
    }


def test_unknown_tool_gets_one_retry_then_fallback() -> None:
    calls: list[str] = []

    def runner(task: str) -> str:
        calls.append(task)
        return json.dumps({"tool": "explode", "args": {}})

    decision = run_router(_config(), context="", user_text="x", runner=runner)
    assert len(calls) == 2, "exactly one retry"
    assert "explode" in calls[1], "the retry names the validation error"
    assert decision["tool"] == "reply" and decision["fallback"] is True


def test_missing_required_arg_is_a_validation_error() -> None:
    replies = iter([
        json.dumps({"tool": "propose_import", "args": {"urls": []}}),
        json.dumps({"tool": "propose_import", "args": {"urls": ["https://x/a.mp4"]}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "propose_import" and decision["fallback"] is False


def test_start_short_accepts_optional_language() -> None:
    reply = json.dumps({
        "tool": "start_short",
        "args": {"topic": "VibeMind", "language": "English"},
    })
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: reply)
    assert decision["args"]["language"] == "English" and decision["fallback"] is False


def test_start_short_language_validation_rejects_garbage() -> None:
    replies = iter([
        json.dumps({"tool": "start_short",
                    "args": {"topic": "t", "language": "en-US_123!"}}),
        json.dumps({"tool": "start_short", "args": {"topic": "t", "language": "English"}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["args"]["language"] == "English" and decision["fallback"] is False


def test_start_short_language_validation_rejects_a_single_character() -> None:
    """I1: the old floor (``{0,31}`` after the required leading letter) admitted a 1-char
    language like "E" — below ``BoardMeta.language``'s own ``min_length=2``. The chat path has
    no HTTP request model to catch it first, so a 1-char value used to sail through validation
    here and only blow up as an unguarded ``BoardMeta`` ``ValidationError`` mid-background-job
    (a corpse session). The floor now matches the schema: a retry, not a crash."""
    replies = iter([
        json.dumps({"tool": "start_short", "args": {"topic": "t", "language": "E"}}),
        json.dumps({"tool": "start_short", "args": {"topic": "t", "language": "English"}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["args"]["language"] == "English" and decision["fallback"] is False


def test_start_overview_accepts_optional_language() -> None:
    reply = json.dumps({
        "tool": "start_overview",
        "args": {"topic": "VibeMind", "language": "Spanish"},
    })
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: reply)
    assert decision["args"]["language"] == "Spanish" and decision["fallback"] is False


def test_system_prompt_carries_the_language_rule() -> None:
    from laura.chat.router import _SYSTEM_PROMPT

    assert '"English" by DEFAULT' in _SYSTEM_PROMPT
    assert "auf Deutsch" in _SYSTEM_PROMPT   # explicit-mention example verbatim
    assert '"language": "German"' in _SYSTEM_PROMPT  # the override example survives


def test_system_prompt_carries_the_loose_project_mention_rule() -> None:
    """Live incident 2026-08-07: 'aus Drive Vibemind' was misread as a Google-Drive URL
    request instead of switch_project, because the prompt had no guidance for a loosely
    mentioned project name against the (now-added) 'Projekte:' roster line."""
    from laura.chat.router import _SYSTEM_PROMPT

    assert '"Projekte:"' in _SYSTEM_PROMPT
    assert "aus Drive Vibemind" in _SYSTEM_PROMPT
    assert "Google-Drive" in _SYSTEM_PROMPT
    assert '{"tool": "switch_project", "args": {"ref": "Drive VibeMind"}}' in _SYSTEM_PROMPT


def test_runner_exception_goes_straight_to_fallback() -> None:
    calls: list[str] = []

    def runner(task: str) -> str:
        calls.append(task)
        raise TimeoutError("model down")

    decision = run_router(_config(), context="", user_text="x", runner=runner)
    assert len(calls) == 1, "exception path never retries"
    assert decision["tool"] == "reply" and decision["fallback"] is True
    assert decision["args"]["text"], "the fallback reply carries readable text"


def test_compose_context_compacts_cards_and_caps_at_20() -> None:
    messages = [
        {"role": "user", "kind": "text", "content": {"text": f"m{i}"}} for i in range(25)
    ] + [
        {"role": "assistant", "kind": "approval_request",
         "content": {"action_type": "import_urls", "status": "pending",
                     "payload": {"urls": ["https://x/a"]}}},
        {"role": "assistant", "kind": "action",
         "content": {"tool": "start_short", "outcome": "done",
                     "refs": {"session_id": "s9"}, "args": {}}},
    ]
    ctx = compose_context(project={"name": "Drive-Test", "id": "p1"}, running_jobs=1,
                          messages=messages)
    assert "m4" not in ctx and "m24" in ctx, "only the last 20 messages"
    assert "import_urls" in ctx and "pending" in ctx
    assert "s9" in ctx, "action refs survive compaction (follow_up needs them)"
    assert "Drive-Test" in ctx


def test_compose_context_lists_asset_names() -> None:
    """Live finding 2026-08-05: without the project's video names in context, the router
    cannot ground an asset_ref ('Zeig mir das Transkript der Bildschirmaufnahme' → the
    model asks back instead of routing review_transcript) — its rules forbid inventing
    names, so the roster must be IN the context."""
    ctx = compose_context(
        project={"name": "P", "id": "p1"},
        running_jobs=0,
        messages=[],
        asset_names=["Bildschirmaufnahme 2026-05-21", "n8n Farm"],
    )
    assert "Videos: Bildschirmaufnahme 2026-05-21, n8n Farm" in ctx


def test_compose_context_omits_videos_line_without_assets() -> None:
    names: list[str] | None
    for names in (None, []):
        ctx = compose_context(
            project={"name": "P", "id": "p1"}, running_jobs=0, messages=[], asset_names=names
        )
        assert "Videos:" not in ctx


def test_compose_context_renders_active_session_line() -> None:
    ctx = compose_context(
        project={"name": "P", "id": "p1"}, running_jobs=0, messages=[],
        asset_names=["A"], active_session={"id": "s1", "state": "awaiting-approval"},
    )
    lines = ctx.splitlines()
    videos_idx = next(i for i, line in enumerate(lines) if line.startswith("Videos:"))
    assert lines[videos_idx + 1] == "Active production session: s1 (awaiting-approval)"


def test_compose_context_omits_session_line_when_none() -> None:
    ctx = compose_context(project={"name": "P", "id": "p1"}, running_jobs=0, messages=[])
    assert "Active production session" not in ctx


def test_compose_context_places_session_line_after_project_when_no_videos() -> None:
    """FE3: without a video roster (no project, or a project with no assets), the session
    line still has a deterministic slot right after the project line rather than drifting
    to wherever the Videos line would have been."""
    ctx = compose_context(
        project={"name": "P", "id": "p1"}, running_jobs=0, messages=[],
        active_session={"id": "s1", "state": "running"},
    )
    lines = ctx.splitlines()
    project_idx = next(i for i, line in enumerate(lines) if line.startswith("Project:"))
    assert lines[project_idx + 1] == "Active production session: s1 (running)"


def test_compose_context_appends_scene_gate_line_when_pending() -> None:
    """GS4: an open Gate-S proposal on the active session renders one more line right after
    the session line — the "select_scenes" system-prompt rule keys off this exact text."""
    ctx = compose_context(
        project={"name": "P", "id": "p1"}, running_jobs=0, messages=[],
        active_session={
            "id": "s1", "state": "in-progress",
            "scene_gate": {"recommended": [2, 4, 5], "candidates": [1, 2, 3, 4, 5]},
        },
    )
    lines = ctx.splitlines()
    session_idx = next(
        i for i, line in enumerate(lines) if line.startswith("Active production session:")
    )
    assert lines[session_idx + 1] == (
        "Szenen-Vorschlag offen: empfohlen [2, 4, 5] von Kandidaten [1, 2, 3, 4, 5]"
    )


def test_compose_context_omits_scene_gate_line_when_absent() -> None:
    ctx = compose_context(
        project={"name": "P", "id": "p1"}, running_jobs=0, messages=[],
        active_session={"id": "s1", "state": "in-progress"},
    )
    assert "Szenen-Vorschlag offen" not in ctx


def test_compose_context_appends_pending_visual_selection_with_recommendations() -> None:
    proposal_hash = "a" * 64
    ctx = compose_context(
        project={"name": "P", "id": "p1"}, running_jobs=0, messages=[],
        active_session={
            "id": "s1", "state": "in-progress",
            "visual_selection_gate": {
                "proposal_hash": proposal_hash,
                "recommended_candidate_ids": ["beat-0-candidate-0", "beat-1-candidate-2"],
            },
        },
    )
    assert (
        "Visual-Auswahl offen: proposal_hash=" + proposal_hash
        + " empfohlen ['beat-0-candidate-0', 'beat-1-candidate-2']"
    ) in ctx


def _recommended_scene_selections() -> list[dict[str, object]]:
    return [
        {
            "rough_cut_order": order,
            "candidate_id": f"scene-{order}-candidate-0",
            "included": True,
            "requested_duration_s": 5,
        }
        for order in range(4)
    ]


def test_compose_context_appends_ordered_v2_visual_scene_decisions() -> None:
    proposal_hash = "a" * 64
    recommendations = _recommended_scene_selections()
    ctx = compose_context(
        project={"name": "P", "id": "p1"},
        running_jobs=0,
        messages=[],
        active_session={
            "id": "s1",
            "state": "awaiting-approval",
            "visual_selection_gate": {
                "proposal_hash": proposal_hash,
                "recommended_selections": recommendations,
            },
        },
    )

    assert (
        f"Visual-Auswahl offen: proposal_hash={proposal_hash} "
        f"empfohlene Szenenentscheidungen {recommendations}"
    ) in ctx


def test_compose_context_appends_pending_contact_sheet_hash() -> None:
    sheet_hash = "b" * 64
    ctx = compose_context(
        project={"name": "P", "id": "p1"}, running_jobs=0, messages=[],
        active_session={
            "id": "s1", "state": "in-progress",
            "contact_sheet_gate": {"contact_sheet_hash": sheet_hash},
        },
    )
    assert "Kontaktbogen-Freigabe offen: contact_sheet_hash=" + sheet_hash in ctx


def test_compose_context_lists_all_projects_marking_the_active_one() -> None:
    """Live incident 2026-08-07: the router had no roster of existing projects, so a loosely
    mentioned project name ('aus Drive Vibemind') couldn't be verified and was misread as a
    Google-Drive URL request. The roster must name every project and mark the active one."""
    ctx = compose_context(
        project={"name": "Drive VibeMind", "id": "p1"}, running_jobs=0, messages=[],
        all_projects=[
            {"id": "p1", "name": "Drive VibeMind"},
            {"id": "p2", "name": "LiveTest TalkingHead"},
            {"id": "p3", "name": "LiveTest Shorts"},
        ],
    )
    assert "Projekte: Drive VibeMind* | LiveTest TalkingHead | LiveTest Shorts" in ctx


def test_compose_context_lists_projects_even_without_an_active_project() -> None:
    """The exact live-incident shape: a fresh conversation has NO bound project yet (the
    top-bar selection is client-only), but the roster must still be present so a loosely
    mentioned project name is resolvable on the very first turn."""
    ctx = compose_context(
        project=None, running_jobs=0, messages=[],
        all_projects=[{"id": "p1", "name": "Drive VibeMind"}],
    )
    assert "Projekte: Drive VibeMind" in ctx
    assert "Drive VibeMind*" not in ctx  # nothing is active yet


def test_compose_context_omits_projects_line_when_none_given() -> None:
    ctx = compose_context(project={"name": "P", "id": "p1"}, running_jobs=0, messages=[])
    assert "Projekte:" not in ctx


def test_compose_context_caps_projects_line_at_15() -> None:
    all_projects = [{"id": f"p{i}", "name": f"Project {i}"} for i in range(20)]
    ctx = compose_context(
        project=None, running_jobs=0, messages=[], all_projects=all_projects,
    )
    line = next(line for line in ctx.splitlines() if line.startswith("Projekte:"))
    assert line.count("|") == 14  # 15 names -> 14 separators
    assert "Project 14" in line and "Project 15" not in line


def test_every_tool_is_reachable() -> None:
    assert frozenset({
        "reply", "create_project", "switch_project", "propose_import",
        "start_short", "start_overview", "follow_up", "revert",
        "review_transcript", "correct_transcript", "confirm_transcript", "approve_script",
        "select_scenes", "select_visuals", "approve_contact_sheet", "discuss",
    }) == TOOLS


def test_new_tools_in_toolset() -> None:
    for t in ("review_transcript", "correct_transcript", "confirm_transcript", "approve_script"):
        assert t in TOOLS


def test_single_key_tool_shape_is_normalized_without_retry() -> None:
    """gpt-4o live drift (2026-08-05): the model answers {"review_transcript": {...}} instead
    of {"tool": ..., "args": ...} — in an app thread already carrying clarify history it did
    so on BOTH attempts, turning every turn into the fallback. Normalize on the first pass."""
    calls: list[str] = []

    def runner(task: str) -> str:
        calls.append(task)
        return json.dumps({"review_transcript": {"asset_ref": "Bildschirmaufnahme"}})

    decision = run_router(_config(), context="", user_text="x", runner=runner)
    assert len(calls) == 1, "normalized shape must not burn the retry"
    assert decision == {
        "tool": "review_transcript",
        "args": {"asset_ref": "Bildschirmaufnahme"},
        "fallback": False,
    }


def test_single_key_unknown_tool_is_not_normalized() -> None:
    replies = iter([
        json.dumps({"explode": {"x": 1}}),
        json.dumps({"tool": "reply", "args": {"text": "ok"}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "reply" and decision["fallback"] is False


def test_single_key_shape_with_bad_args_still_validates() -> None:
    replies = iter([
        json.dumps({"review_transcript": {}}),
        json.dumps({"tool": "review_transcript", "args": {"asset_ref": "a"}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "review_transcript" and decision["fallback"] is False


def test_review_transcript_requires_asset_ref() -> None:
    replies = iter([
        json.dumps({"tool": "review_transcript", "args": {"asset_ref": ""}}),
        json.dumps({"tool": "review_transcript", "args": {"asset_ref": "a1"}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "review_transcript" and decision["fallback"] is False


def test_correct_transcript_requires_nonempty_corrections() -> None:
    calls: list[str] = []

    def runner(task: str) -> str:
        calls.append(task)
        if len(calls) == 1:
            return json.dumps(
                {"tool": "correct_transcript", "args": {"asset_ref": "a", "corrections": []}}
            )
        return json.dumps({
            "tool": "correct_transcript",
            "args": {
                "asset_ref": "a",
                "corrections": [{"segment_index": 3, "text": "Claude Code"}],
            },
        })

    decision = run_router(_config(), context="", user_text="x", runner=runner)
    assert len(calls) == 2, "exactly one retry"
    assert "corrections" in calls[1], "the retry names the validation error"
    assert decision == {
        "tool": "correct_transcript",
        "args": {
            "asset_ref": "a",
            "corrections": [{"segment_index": 3, "text": "Claude Code"}],
        },
        "fallback": False,
    }


def test_correct_transcript_segment_index_rejects_bool_and_zero() -> None:
    replies = iter([
        json.dumps({
            "tool": "correct_transcript",
            "args": {"asset_ref": "a", "corrections": [{"segment_index": True, "text": "x"}]},
        }),
        json.dumps({
            "tool": "correct_transcript",
            "args": {"asset_ref": "a", "corrections": [{"segment_index": 0, "text": "x"}]},
        }),
        json.dumps({
            "tool": "correct_transcript",
            "args": {"asset_ref": "a", "corrections": [{"segment_index": 1, "text": "x"}]},
        }),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    # only one retry is granted; the third (valid) reply is never reached, so the router
    # falls back — this proves both the bool-as-int and the zero segment_index were rejected.
    assert decision["tool"] == "reply" and decision["fallback"] is True


def test_correct_transcript_requires_nonempty_text() -> None:
    replies = iter([
        json.dumps({
            "tool": "correct_transcript",
            "args": {"asset_ref": "a", "corrections": [{"segment_index": 1, "text": ""}]},
        }),
        json.dumps({
            "tool": "correct_transcript",
            "args": {"asset_ref": "a", "corrections": [{"segment_index": 1, "text": "ok"}]},
        }),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "correct_transcript" and decision["fallback"] is False


def test_confirm_transcript_requires_asset_ref() -> None:
    replies = iter([
        json.dumps({"tool": "confirm_transcript", "args": {}}),
        json.dumps({"tool": "confirm_transcript", "args": {"asset_ref": "a1"}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "confirm_transcript" and decision["fallback"] is False


def test_approve_script_requires_session_ref() -> None:
    replies = iter([
        json.dumps({"tool": "approve_script", "args": {}}),
        json.dumps({"tool": "approve_script", "args": {"session_ref": "s1"}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "approve_script" and decision["fallback"] is False


def test_select_scenes_requires_nonempty_int_list() -> None:
    replies = iter([
        json.dumps({"tool": "select_scenes", "args": {"scene_numbers": []}}),
        json.dumps({"tool": "select_scenes", "args": {"scene_numbers": [2, 5]}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision == {
        "tool": "select_scenes", "args": {"scene_numbers": [2, 5]}, "fallback": False,
    }


def test_select_visuals_routes_current_recommendations() -> None:
    proposal_hash = "a" * 64
    recommendations = ["beat-0-candidate-0", "beat-1-candidate-2"]
    reply = json.dumps({
        "tool": "select_visuals",
        "args": {
            "proposal_hash": proposal_hash,
            "selected_candidate_ids": recommendations,
        },
    })
    decision = run_router(
        _config(),
        context=(
            f"Visual-Auswahl offen: proposal_hash={proposal_hash} "
            f"empfohlen {recommendations}"
        ),
        user_text="deine Auswahl passt",
        runner=lambda _task: reply,
    )
    assert decision == {
        "tool": "select_visuals",
        "args": {
            "proposal_hash": proposal_hash,
            "selected_candidate_ids": recommendations,
        },
        "fallback": False,
    }


def test_select_visuals_routes_current_recommended_scene_selections() -> None:
    proposal_hash = "a" * 64
    recommendations = _recommended_scene_selections()
    reply = json.dumps(
        {
            "tool": "select_visuals",
            "args": {"proposal_hash": proposal_hash, "selections": recommendations},
        }
    )

    decision = run_router(
        _config(),
        context=(
            f"Visual-Auswahl offen: proposal_hash={proposal_hash} "
            f"empfohlene Szenenentscheidungen {recommendations}"
        ),
        user_text="die Auswahl passt",
        runner=lambda _task: reply,
    )

    assert decision == {
        "tool": "select_visuals",
        "args": {"proposal_hash": proposal_hash, "selections": recommendations},
        "fallback": False,
    }


@pytest.mark.parametrize(
    "invalid_selections",
    [
        [],
        [{"rough_cut_order": True, "candidate_id": "candidate-a", "included": True,
          "requested_duration_s": 5}],
        [{"rough_cut_order": 0, "candidate_id": "", "included": True,
          "requested_duration_s": 5}],
        [{"rough_cut_order": 0, "candidate_id": "candidate-a", "included": 1,
          "requested_duration_s": 5}],
        [{"rough_cut_order": 0, "candidate_id": "candidate-a", "included": True,
          "requested_duration_s": 11}],
    ],
    ids=("empty", "bool-order", "empty-id", "non-bool-included", "duration-range"),
)
def test_select_visuals_requires_nonempty_strict_scene_selections(
    invalid_selections: list[dict[str, object]],
) -> None:
    valid = {
        "tool": "select_visuals",
        "args": {
            "proposal_hash": "a" * 64,
            "selections": _recommended_scene_selections(),
        },
    }
    replies = iter(
        [
            json.dumps(
                {
                    "tool": "select_visuals",
                    "args": {
                        "proposal_hash": "a" * 64,
                        "selections": invalid_selections,
                    },
                }
            ),
            json.dumps(valid),
        ]
    )

    decision = run_router(
        _config(), context="", user_text="x", runner=lambda _task: next(replies)
    )

    assert decision == {**valid, "fallback": False}


def test_select_visuals_requires_hash_and_nonempty_candidate_ids() -> None:
    valid = {
        "tool": "select_visuals",
        "args": {"proposal_hash": "a" * 64, "selected_candidate_ids": ["candidate-a"]},
    }
    replies = iter([
        json.dumps({
            "tool": "select_visuals",
            "args": {"proposal_hash": "short", "selected_candidate_ids": []},
        }),
        json.dumps(valid),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "select_visuals" and decision["fallback"] is False


@pytest.mark.parametrize("invalid_hash", ["A" * 64, "g" * 64], ids=("uppercase", "nonhex"))
def test_select_visuals_requires_lowercase_sha256_hash(invalid_hash: str) -> None:
    valid = {
        "tool": "select_visuals",
        "args": {
            "proposal_hash": "a" * 64,
            "selections": _recommended_scene_selections(),
        },
    }
    replies = iter(
        [
            json.dumps(
                {
                    "tool": "select_visuals",
                    "args": {
                        "proposal_hash": invalid_hash,
                        "selections": _recommended_scene_selections(),
                    },
                }
            ),
            json.dumps(valid),
        ]
    )

    decision = run_router(
        _config(), context="", user_text="x", runner=lambda _task: next(replies)
    )

    assert decision == {**valid, "fallback": False}


def test_approve_contact_sheet_routes_current_hash() -> None:
    sheet_hash = "b" * 64
    reply = json.dumps({
        "tool": "approve_contact_sheet",
        "args": {"contact_sheet_hash": sheet_hash},
    })
    decision = run_router(
        _config(),
        context=f"Kontaktbogen-Freigabe offen: contact_sheet_hash={sheet_hash}",
        user_text="Kontaktbogen freigeben",
        runner=lambda _task: reply,
    )
    assert decision == {
        "tool": "approve_contact_sheet",
        "args": {"contact_sheet_hash": sheet_hash},
        "fallback": False,
    }


def test_approve_contact_sheet_requires_64_character_hash() -> None:
    replies = iter([
        json.dumps({"tool": "approve_contact_sheet", "args": {"contact_sheet_hash": "short"}}),
        json.dumps({"tool": "approve_contact_sheet", "args": {"contact_sheet_hash": "b" * 64}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "approve_contact_sheet" and decision["fallback"] is False


def test_system_prompt_carries_visual_and_contact_sheet_gate_rules() -> None:
    from laura.chat.router import _SYSTEM_PROMPT

    assert "Visual-Auswahl offen" in _SYSTEM_PROMPT
    assert "select_visuals" in _SYSTEM_PROMPT
    assert "Kontaktbogen-Freigabe offen" in _SYSTEM_PROMPT
    assert "approve_contact_sheet" in _SYSTEM_PROMPT


def test_select_scenes_rejects_non_list() -> None:
    replies = iter([
        json.dumps({"tool": "select_scenes", "args": {"scene_numbers": "2"}}),
        json.dumps({"tool": "select_scenes", "args": {"scene_numbers": [2]}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "select_scenes" and decision["fallback"] is False


def test_select_scenes_rejects_bool_and_zero() -> None:
    replies = iter([
        json.dumps({"tool": "select_scenes", "args": {"scene_numbers": [True]}}),
        json.dumps({"tool": "select_scenes", "args": {"scene_numbers": [0]}}),
        json.dumps({"tool": "select_scenes", "args": {"scene_numbers": [2]}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    # only one retry is granted; the third (valid) reply is never reached, so the router
    # falls back — this proves both the bool-as-int and the zero scene_number were rejected.
    assert decision["tool"] == "reply" and decision["fallback"] is True


def test_system_prompt_carries_the_select_scenes_rule() -> None:
    """The rule text + both examples must appear verbatim (spec 2026-08-06 task GS4)."""
    from laura.chat.router import _SYSTEM_PROMPT

    assert "select_scenes" in _SYSTEM_PROMPT
    assert "Szenen-Vorschlag offen" in _SYSTEM_PROMPT
    assert '"nimm 2 und 5 statt 4" -> [2, 5]' in _SYSTEM_PROMPT
    assert '"passt so" / "nimm deine Auswahl" ' in _SYSTEM_PROMPT


def test_discuss_is_a_known_tool_with_text_arg() -> None:
    assert "discuss" in TOOLS
    reply = json.dumps({"tool": "discuss", "args": {"text": "warum ist szene 2 so lang?"}})
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: reply)
    assert decision == {
        "tool": "discuss",
        "args": {"text": "warum ist szene 2 so lang?"},
        "fallback": False,
    }


def test_discuss_requires_nonempty_text() -> None:
    replies = iter([
        json.dumps({"tool": "discuss", "args": {"text": ""}}),
        json.dumps({"tool": "discuss", "args": {"text": "ok"}}),
    ])
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: next(replies))
    assert decision["tool"] == "discuss" and decision["fallback"] is False


def test_discuss_single_key_shape_normalizes() -> None:
    reply = json.dumps({"discuss": {"text": "die captions sind zu klein"}})
    decision = run_router(_config(), context="", user_text="x", runner=lambda _t: reply)
    assert decision["tool"] == "discuss" and decision["fallback"] is False


def test_system_prompt_carries_the_priority_and_proposal_rules() -> None:
    """The prompt work IS the feature for routing quality — pin the load-bearing strings
    so a later prompt edit cannot silently drop them (live incident 2026-08-05: a free
    critique containing 'Transkript' was answered with a transcript card)."""
    from laura.chat.router import _SYSTEM_PROMPT

    assert "discuss" in _SYSTEM_PROMPT
    assert "warum steht das im transkript" in _SYSTEM_PROMPT  # negative example verbatim
    assert "Vorschlag:" in _SYSTEM_PROMPT                      # yes-after-proposal rule
    assert "mach Szene 2 kürzer" in _SYSTEM_PROMPT             # adjustment example
    assert "mach das in english" in _SYSTEM_PROMPT             # language-follow-up example (M3)


def test_build_one_shot_runner_is_public() -> None:
    from laura.chat.router import build_one_shot_runner

    runner = build_one_shot_runner(_config())
    assert callable(runner)


def test_build_one_shot_runner_defaults_to_the_router_system_prompt() -> None:
    """C1 (2026-08-05 final review): an unqualified call must keep its pre-fix behavior — the
    router's own JSON-tool-call prompt — so every existing caller (the router itself) is
    unaffected by the new seam."""
    from laura.chat.router import _SYSTEM_PROMPT, build_one_shot_runner

    runner = build_one_shot_runner(_config())
    assert runner.system_message == _SYSTEM_PROMPT  # type: ignore[attr-defined]


def test_build_one_shot_runner_threads_a_custom_system_message() -> None:
    """C1: a caller running a DIFFERENT one-shot task (discuss's grounded-answer persona, not
    a tool router) must be able to swap the system message — the whole point of the fix is
    that this no longer defaults to the router's "reply with EXACTLY one JSON object" prompt."""
    from laura.chat.router import build_one_shot_runner

    custom = "You are a completely different persona."
    runner = build_one_shot_runner(_config(), system_message=custom)
    assert runner.system_message == custom  # type: ignore[attr-defined]
