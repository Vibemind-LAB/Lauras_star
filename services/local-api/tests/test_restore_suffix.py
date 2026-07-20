"""The full-suffix restore: bring back the longest archived suffix the board still matches.

Successor to the review-killed single-link restore (removed in 41ecc51). That one keyed a
render's identity on script text alone and resurrected a film cut from an abandoned cutlist
as stale=False — reproduced live. This walk keys every link on the content hashes of the
exact parent INSTANCES it was built from (spec 2026-07-20-provenance-chain-design.md §3), so
the killer case inverts into a feature: reverting the cutlist brings back ITS OWN render.
"""

from __future__ import annotations

from pathlib import Path

from laura.short_creator.board import Board
from laura.short_creator.board_models import (
    BoardMeta,
    Chapter,
    ContactSheet,
    ContactSheetTile,
    Cutlist,
    CutSegment,
    QaReport,
    RenderReport,
    Script,
    ScriptLine,
    Storyline,
    VoiceArtifact,
    content_hash,
)


def _board(tmp_path: Path) -> Board:
    return Board.create(
        tmp_path / "board",
        BoardMeta(
            session_id="s1",
            asset_id="a1",
            created_utc="2026-07-20T00:00:00+00:00",
            task="demo",
            language="English",
            target_seconds=174.0,
        ),
    )


def _script(text: str) -> Script:
    return Script(language="English", lines=[ScriptLine(chapter=1, scene_number=1, text=text)])


def _cut(order0_end: int = 240) -> Cutlist:
    return Cutlist(
        segments=[
            CutSegment(order=0, scene_number=1, start_frame=0, end_frame_exclusive=order0_end)
        ]
    )


def _sheet() -> ContactSheet:
    return ContactSheet(
        png_path="s.png",
        cols=1,
        rows=1,
        tiles=[ContactSheetTile(order=0, scene_number=1, frame=100, label="1")],
    )


def _seed_full_chain(board: Board, text: str) -> None:
    """storyline -> script -> voice -> cutlist -> sheet -> render -> qa.

    The derived artifacts deliberately stamp SUBSETS of the spec table (no ``storyline`` key
    in any ``parents`` dict below): the walk is generic over whatever parents an artifact
    recorded — it checks exactly those. The full spec-table stamps are asserted at the write
    sites (Task 4), where they are produced. The storyline itself IS saved (once, untouched
    for the rest of the test): ``resume_point`` requires it present to ever report "done",
    matching the real system where every derived write site sits downstream of one.
    """
    board.save(
        "storyline",
        Storyline(
            red_thread="r",
            arc=[
                Chapter(chapter=1, role="hook", message="m", scene_numbers=[1], target_seconds=10.0)
            ],
        ),
    )
    board.save("script", _script(text))
    script = board.load("script")
    assert script is not None
    voice = VoiceArtifact(
        script_hash="cache-key",
        mp3_path=f"voiceovers/{text[:8]}.mp3",
        parents={"script": content_hash(script)},
    )
    board.save("voice", voice)
    cur_voice = board.load("voice")
    assert cur_voice is not None
    board.save(
        "cutlist",
        _cut().model_copy(
            update={"parents": {"script": content_hash(script), "voice": content_hash(cur_voice)}}
        ),
    )
    cur_cut = board.load("cutlist")
    assert cur_cut is not None
    board.save(
        "contact_sheet", _sheet().model_copy(update={"parents": {"cutlist": content_hash(cur_cut)}})
    )
    board.save(
        "render_report",
        RenderReport(
            export_id=f"e-{text[:8]}",
            video_s=100.0,
            width=1920,
            height=1080,
            parents={"voice": content_hash(cur_voice), "cutlist": content_hash(cur_cut)},
        ),
    )
    cur_render = board.load("render_report")
    assert cur_render is not None
    board.save(
        "qa_report",
        QaReport(verdict="ship", findings=[], parents={"render_report": content_hash(cur_render)}),
    )


def test_revise_and_revert_back_revives_the_whole_suffix_including_qa(tmp_path: Path) -> None:
    """The motivating case, end to end: A -> B -> back to A brings everything back."""
    board = _board(tmp_path)
    _seed_full_chain(board, "the rendered line")
    board.save("script", _script("a different draft"))
    board.save("script", _script("the rendered line"))
    assert board.load("voice") is None and board.load("qa_report") is None

    restored = board.restore_coherent_suffix()

    assert restored == ["voice", "cutlist", "contact_sheet", "render_report", "qa_report"]
    render = board.load("render_report")
    assert isinstance(render, RenderReport) and render.export_id == "e-the rend"
    assert board.resume_point([]) == "done"


def test_reverting_the_cutlist_brings_back_its_own_render(tmp_path: Path) -> None:
    """The review's killer case, inverted into a feature."""
    board = _board(tmp_path)
    _seed_full_chain(board, "one stable script")
    # A second cutlist round on the SAME script/voice, with its own sheet/render/qa.
    script = board.load("script")
    voice = board.load("voice")
    assert script is not None and voice is not None
    board.save(
        "cutlist",
        _cut(order0_end=480).model_copy(
            update={"parents": {"script": content_hash(script), "voice": content_hash(voice)}}
        ),
    )
    cut_v2 = board.load("cutlist")
    assert cut_v2 is not None
    board.save(
        "render_report",
        RenderReport(
            export_id="e-second-cut",
            video_s=200.0,
            width=1920,
            height=1080,
            parents={"voice": content_hash(voice), "cutlist": content_hash(cut_v2)},
        ),
    )
    # The user goes back to cutlist v1 (documented follow-up flow) — sheet/render/qa wiped.
    board.revert("cutlist", 1)
    assert board.load("render_report") is None

    restored = board.restore_coherent_suffix()

    assert "render_report" in restored
    render = board.load("render_report")
    assert isinstance(render, RenderReport)
    assert render.export_id == "e-one stab", "cutlist v1's OWN render, not the second cut's"


def test_partial_suffix_stops_at_the_first_unmatched_link(tmp_path: Path) -> None:
    """Voice matches, cutlist does not (voice was re-synthesized) -> only voice returns."""
    board = _board(tmp_path)
    _seed_full_chain(board, "steady text")
    script = board.load("script")
    assert script is not None
    # Re-synthesize: new mp3, same script -> cutlist's recorded voice hash no longer matches.
    board.save(
        "voice",
        VoiceArtifact(
            script_hash="cache-key",
            mp3_path="voiceovers/retake.mp3",
            parents={"script": content_hash(script)},
        ),
    )
    board.save("script", _script("elsewhere"))
    board.save("script", _script("steady text"))
    assert board.load("voice") is None

    restored = board.restore_coherent_suffix()

    assert restored == ["voice"]
    voice = board.load("voice")
    assert isinstance(voice, VoiceArtifact)
    assert voice.mp3_path == "voiceovers/retake.mp3", "newest matching archive wins"
    # The archived cutlist recorded the FIRST voice's hash; the restored voice is the retake,
    # so the cutlist's parents mismatch and the walk correctly ends after the voice.
    assert board.load("cutlist") is None


def test_empty_parents_never_restore(tmp_path: Path) -> None:
    """Pre-provenance archives are unknown, and unknown is not coherent."""
    board = _board(tmp_path)
    board.save("script", _script("old world"))
    board.save("render_report", RenderReport(export_id="e", video_s=9.0, width=1920, height=1080))
    board.save("script", _script("new world"))
    board.save("script", _script("old world"))
    assert board.load("render_report") is None

    assert board.restore_coherent_suffix() == []
    assert board.load("render_report") is None


def test_an_unreadable_archive_is_skipped_not_fatal(tmp_path: Path) -> None:
    board = _board(tmp_path)
    _seed_full_chain(board, "resilient")
    board.save("script", _script("other"))
    board.save("script", _script("resilient"))
    corrupt = board.root / "versions" / "voice.v1.json"
    corrupt.write_text("{ not json", encoding="utf-8")

    restored = board.restore_coherent_suffix()

    assert restored == [], "the only voice archive is corrupt; nothing below can match either"


def test_a_present_link_is_never_touched(tmp_path: Path) -> None:
    board = _board(tmp_path)
    _seed_full_chain(board, "hands off")

    assert board.restore_coherent_suffix() == []
