"""brain_tools: read-only second-brain vault access (Task 10, Transkript-Gates).

``LAURA_SECONDBRAIN_PATH`` gates the whole module — unset/missing means ``brain_root()`` is
``None`` and both tools degrade to explicit "not configured"/"not found" replies rather than
raising. All three behaviours from the task-10 brief are covered here: content search, stem
resolution (case-insensitive), the traversal guard, and the env-gated absence from
``build_production_tool_specs``'s tool list (env presence is exercised separately in
``test_production_tools_review.py``-style fixtures where the two tools are simply not asserted;
the negative case — env unset -> tools absent from specs — is the one this file must prove).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, SqliteDatabase
from laura.short_creator.board import Board
from laura.short_creator.board_models import BoardMeta
from laura.short_creator.brain_tools import brain_root, read_brain_note, search_second_brain
from laura.short_creator.production_tools import build_production_tool_specs


def _vault(tmp_path: Path) -> Path:
    root = tmp_path / "vault"
    root.mkdir()
    (root / "Product Names.md").write_text(
        "# Product Names\n\nThe flagship product is called Laura Editorial.\n", encoding="utf-8"
    )
    (root / "unrelated.md").write_text("Just some other note about lunch.\n", encoding="utf-8")
    return root


def _plant_escape_link(vault: Path, outside_dir: Path) -> bool:
    """Create ``vault/linked`` as a link whose RESOLVED target is *outside_dir*, so
    ``vault.rglob("*.md")`` finds a file that lives inside the link but escapes the vault once
    resolved — the actual case the traversal guard (``resolve()`` + ``is_relative_to``) exists
    to catch, unlike a merely-nonexistent ``../name``.

    Prefers a Windows directory junction (``mklink /J`` — no admin rights or Developer Mode
    needed, verified empirically on this machine); falls back to ``os.symlink`` for POSIX (or an
    elevated/Developer-Mode Windows). Returns ``False`` if neither could be created so callers can
    skip cleanly rather than fail on an environment that cannot build the fixture.
    """
    link = vault / "linked"
    try:
        proc = subprocess.run(  # noqa: S603, S607 — test-only, fixed args, no shell
            ["cmd", "/c", "mklink", "/J", str(link), str(outside_dir)],
            capture_output=True,
        )
        if proc.returncode == 0 and link.exists():
            return True
    except OSError:
        pass
    try:
        os.symlink(outside_dir, link, target_is_directory=True)
    except OSError:
        return False
    return link.exists()


# --- brain_root --------------------------------------------------------------------------------


def test_brain_root_none_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_SECONDBRAIN_PATH", raising=False)
    assert brain_root() is None


def test_brain_root_none_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", "")
    assert brain_root() is None


def test_brain_root_none_when_nonexistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(tmp_path / "does-not-exist"))
    assert brain_root() is None


def test_brain_root_reads_env_at_call_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _vault(tmp_path)
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))
    assert brain_root() == vault


# --- search_second_brain -----------------------------------------------------------------------


def test_search_finds_by_content(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = search_second_brain("laura editorial")

    assert result["ok"] is True
    notes = {r["note"] for r in result["results"]}
    assert "Product Names" in notes
    assert "unrelated" not in notes
    hit = next(r for r in result["results"] if r["note"] == "Product Names")
    assert "Laura Editorial" in hit["snippet"]
    assert hit["path"] == "Product Names.md"


def test_search_is_case_insensitive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = search_second_brain("LAURA EDITORIAL")

    assert result["ok"] is True
    assert any(r["note"] == "Product Names" for r in result["results"])


def test_search_respects_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    for i in range(5):
        (vault / f"note{i}.md").write_text("shared keyword here\n", encoding="utf-8")
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = search_second_brain("keyword", limit=2)

    assert result["ok"] is True
    assert len(result["results"]) == 2


def test_search_without_env_reports_not_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_SECONDBRAIN_PATH", raising=False)

    result = search_second_brain("anything")

    assert result == {"ok": False, "reason": "second brain not configured"}


def test_search_no_match_returns_empty_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _vault(tmp_path)
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = search_second_brain("nonexistent-term-xyz")

    assert result == {"ok": True, "results": []}


# --- read_brain_note ----------------------------------------------------------------------------


def test_read_note_by_exact_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = read_brain_note("Product Names")

    assert result["ok"] is True
    assert result["note"] == "Product Names"
    assert "Laura Editorial" in result["content"]


def test_read_note_stem_case_insensitive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = read_brain_note("PRODUCT names")

    assert result["ok"] is True
    assert result["note"] == "Product Names"


def test_read_note_content_capped_at_8000_chars(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = tmp_path / "vault"
    vault.mkdir()
    (vault / "long.md").write_text("x" * 9000, encoding="utf-8")
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = read_brain_note("long")

    assert result["ok"] is True
    assert len(result["content"]) == 8000


def test_read_note_unknown_name_not_found(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    vault = _vault(tmp_path)
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = read_brain_note("does-not-exist")

    assert result == {"ok": False, "reason": "note not found"}


def test_read_note_dotdot_name_is_not_a_path_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``../``-prefixed name never reaches outside the vault — but NOT because of the
    ``is_relative_to`` guard: ``read_brain_note`` never joins ``name`` onto ``root`` at all, it
    resolves by CASE-INSENSITIVE STEM against ``root.rglob("*.md")``, and ``Path("../secret").stem
    == "secret"`` — the ``..`` is simply discarded by ``Path.stem``. Since ``rglob`` can, by
    construction, never yield a path outside ``root`` in the first place, this test passes
    IDENTICALLY even with the ``is_relative_to`` check deleted; it pins the "name is never used to
    build a filesystem path" property, not the traversal guard. The guard's actual job — reading a
    path that IS inside the vault by directory listing but resolves OUTSIDE it via a link — is
    covered by ``test_read_note_traversal_guard_rejects_link_escape`` below.
    """
    vault = _vault(tmp_path)
    (tmp_path / "secret.md").write_text("classified\n", encoding="utf-8")
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = read_brain_note("../secret")

    assert result == {"ok": False, "reason": "note not found"}


def test_read_note_traversal_guard_rejects_link_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The REAL case the traversal guard exists for: a link INSIDE the vault whose resolved
    target is OUTSIDE it. ``rglob`` follows the link and finds ``vault/linked/secret.md`` (a
    stem match for "secret"), but ``.resolve()`` walks it out to ``outside/secret.md`` — the
    ``is_relative_to(root.resolve())`` check must reject that resolved path.

    Manually verified this test actually exercises the guard (not just the "no match" path): with
    the ``is_relative_to`` check in ``read_brain_note`` commented out, this test FAILED (the
    outside note's content came back as ``ok: True``); restored, it PASSES. See task-10-report.md
    fix-round section for the paste.
    """
    vault = _vault(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("classified\n", encoding="utf-8")
    if not _plant_escape_link(vault, outside_dir):
        pytest.skip("cannot create a directory junction/symlink on this system")
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = read_brain_note("secret")

    assert result == {"ok": False, "reason": "note not found"}


def test_search_never_leaks_a_result_outside_vault_via_link(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``search_second_brain`` has its own copy of the same guard (brain_tools.py's
    ``search_second_brain``, the ``resolved.is_relative_to(root_resolved)`` check) — untested
    until now. Same link fixture as the read-side test: a note whose content matches the query
    exists ONLY outside the vault, reachable inside it only via the escaping link. If the guard
    were absent, this search would return it.

    Manually verified this test actually exercises the guard the same way as the read-side test —
    commented the check out locally, saw this test fail, restored it, saw it pass.
    """
    vault = _vault(tmp_path)
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.md").write_text("wildly classified material\n", encoding="utf-8")
    if not _plant_escape_link(vault, outside_dir):
        pytest.skip("cannot create a directory junction/symlink on this system")
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))

    result = search_second_brain("classified material")

    assert result == {"ok": True, "results": []}


def test_read_note_without_env_not_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LAURA_SECONDBRAIN_PATH", raising=False)

    result = read_brain_note("anything")

    assert result == {"ok": False, "reason": "note not found"}


# --- registration: env-gated presence in build_production_tool_specs ---------------------------


def _seeded_board(tmp_path: Path) -> tuple[Board, Database, str]:
    """Minimal project + asset + empty board — just enough for build_production_tool_specs.

    Mirrors ``tests/test_production_agents.py``'s ``_seed_scene``/``_board``, trimmed down since
    these tests only need the tool NAME list, never a real scene to review.
    """
    settings = Settings(workspace_root=tmp_path / "ws", start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    project = repos.create_project(
        db, name="p", rate_num=30, rate_den=1, drop_frame=False,
        workspace_root=str(tmp_path / "ws" / "proj"),
    )
    asset = repos.create_asset(
        db, project_id=project["id"], type="video", display_name="a.mp4",
        source_path=str(tmp_path / "a.mp4"),
    )
    meta = BoardMeta(
        session_id="s1", asset_id=str(asset["id"]), created_utc="2026-08-04T00:00:00Z",
        task="overview short", target_seconds=20.0,
    )
    board = Board.create(tmp_path / "board", meta)
    return board, db, str(asset["id"])


def test_tools_absent_from_specs_without_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("LAURA_SECONDBRAIN_PATH", raising=False)
    board, db, asset_id = _seeded_board(tmp_path)

    specs = build_production_tool_specs(db, board, asset_id=asset_id)

    names = {s.name for s in specs}
    assert "search_second_brain" not in names
    assert "read_brain_note" not in names


def test_tools_present_in_specs_with_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    vault = _vault(tmp_path)
    monkeypatch.setenv("LAURA_SECONDBRAIN_PATH", str(vault))
    board, db, asset_id = _seeded_board(tmp_path)

    specs = build_production_tool_specs(db, board, asset_id=asset_id)

    names = {s.name for s in specs}
    assert "search_second_brain" in names
    assert "read_brain_note" in names
