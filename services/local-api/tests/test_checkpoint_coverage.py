"""Coverage test: every synchronous timeline mutation route wraps its write in a checkpoint.

Checks only the four API files that own synchronous editorial mutations
(timelines.py, scenes.py, audio.py, overlays.py).  Creation/import routes
(import_timeline, timeline_from_shots, demo/project seeding helpers) live in
other files and are excluded by design — they build *new* timelines rather than
editing existing state, so there is nothing to undo back to.
"""

from __future__ import annotations

import ast
import pathlib

# The four API files that own synchronous editorial mutations.
EDIT_API_FILES = {
    "timelines.py",
    "scenes.py",
    "audio.py",
    "overlays.py",
}

API = pathlib.Path("src/laura/api")

# Repo write-helpers that mutate timeline_clips / scenes / timeline_audio_clips.
# Verified against repos.py: these are the functions with INSERT/UPDATE/DELETE on
# the three key tables.
WRITE_FNS = {
    "replace_timeline_clips",
    "add_timeline_clip",
    "delete_timeline_clip",
    "update_timeline_clip_role",
    "set_clip_transition",
    "replace_scenes",
    "update_scene_name",
    "set_scene_music",
    "clear_scene_music",
    "add_timeline_audio_clip",
    "update_timeline_audio_clip",
    "delete_timeline_audio_clip",
}

# Functions that are allowed to write without a checkpoint because they are
# construction/import operations (building NEW timelines, not editing existing ones).
EXEMPT_FUNCTIONS = {
    "import_timeline",     # timelines.py: POST /timelines/import → creates a new timeline
    "timeline_from_shots", # timelines.py: POST /from-shots → creates a new rough_cut
}


def test_every_api_timeline_write_is_in_a_checkpoint() -> None:
    offenders = []
    for path in API.rglob("*.py"):
        if path.name not in EDIT_API_FILES:
            continue
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                if node.name in EXEMPT_FUNCTIONS:
                    continue
                seg = ast.get_source_segment(src, node) or ""
                calls = {
                    n.func.attr
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                }
                if calls & WRITE_FNS and "timeline_checkpoint" not in seg:
                    offenders.append(f"{path}::{node.name}")
    assert offenders == [], f"timeline writes outside a checkpoint: {offenders}"
