"""Delete one production session and everything it produced — never its input.

A session's output is scattered over three places: the run directory
(``<workspace_root>/agent-runs/<session_id>/``, holding the board, its version archive and the
contact sheets), the shared ``voiceovers/`` directory (the constructed track and its timings
sidecar, named inside the board's voice artifacts) and the ``exports`` table (the rendered mp4,
named inside the board's render reports). Nothing links the latter two back to the session in
SQL, so the board is what makes them findable — including through its ``versions/`` archive,
because a session that rendered five times owns five exports, not one.

The input side — media assets and their files, scenes, transcripts, analyses, the project — is
never touched. Deleting a production removes what the production made; the footage it was made
from belongs to the project, not to the run.

Artifact collection reads the board's JSON as plain dicts rather than through the typed models
on purpose: the sessions most in need of deleting are the half-written ones, and a cleanup that
refuses to run on a corrupt board is a cleanup that cannot do its job.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Keys whose values name something on disk or in the exports table. `mp3_path`/`timings_path`
# come from VoiceArtifact, `export_id` from RenderReport, `path` from ContactSheet.
_EXPORT_KEYS = ("export_id",)
_PATH_KEYS = ("mp3_path", "timings_path")


@dataclass
class SessionArtifacts:
    """What one session's board says it produced."""

    export_ids: set[str] = field(default_factory=set)
    media_paths: set[Path] = field(default_factory=set)


def _walk(node: Any, found: SessionArtifacts) -> None:
    if isinstance(node, dict):
        for key, value in node.items():
            if key in _EXPORT_KEYS and isinstance(value, str) and value:
                found.export_ids.add(value)
            elif key in _PATH_KEYS and isinstance(value, str) and value:
                found.media_paths.add(Path(value))
            else:
                _walk(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk(item, found)


def collect_session_artifacts(run_dir: Path) -> SessionArtifacts:
    """Every export id and media path named anywhere under *run_dir*'s board.

    Walks the current artifacts AND ``versions/``: each render wrote its own export, each voice
    synthesis its own track, and only the newest of each is reachable from the board's surface.
    An unreadable or non-JSON file is skipped — a board too broken to parse still has its
    directory removed by the caller, which is the bulk of what there is to clean.
    """
    found = SessionArtifacts()
    if not run_dir.is_dir():
        return found
    for path in sorted(run_dir.rglob("*.json")):
        try:
            _walk(json.loads(path.read_text(encoding="utf-8")), found)
        except (OSError, ValueError):
            continue
    return found


def _inside(path: Path, root: Path) -> bool:
    """True when *path* really sits under *root* — the guard that keeps a board's stray
    absolute path from turning a cleanup into an arbitrary delete."""
    try:
        path.resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


def remove_files(paths: set[Path], *, workspace_root: Path) -> list[str]:
    """Unlink every path that lies inside *workspace_root*; return what was actually removed."""
    removed: list[str] = []
    for path in sorted(paths):
        if not _inside(path, workspace_root):
            continue
        try:
            if path.is_file():
                path.unlink()
                removed.append(str(path))
        except OSError:
            continue
    return removed


def remove_tree(directory: Path, *, workspace_root: Path) -> bool:
    """Remove the whole run directory, if it is inside the workspace. True when it is gone."""
    if not _inside(directory, workspace_root) or not directory.is_dir():
        return False
    shutil.rmtree(directory, ignore_errors=True)
    return not directory.exists()
