"""Read-only access to the user's second-brain vault (Task 10, Transkript-Gates).

The production team (``scene_author`` in particular — see :mod:`.production_agents`) writes
marketing/product copy from a rough cut's transcript and VLM review alone, which has no way to
know a correct product name, a precise feature term, or a fact the raw footage does not spell
out. ``LAURA_SECONDBRAIN_PATH`` optionally points at the user's personal knowledge vault (a
directory of markdown notes) so the team can look facts up instead of guessing or inventing them
— the same "optional extra, env-gated" convention as the VLM backend (``LAURA_VLM_MODEL``,
:mod:`.describe`) and the voice backend (``LAURA_ELEVENLABS_API_KEY``, :mod:`.voice`): a Laura
install with the env var unset works exactly as before, nothing here is a hard dependency.

Read-only and stdlib-only, on purpose: this reaches into a directory the user did not create for
Laura, so the two tools built on top of :func:`brain_root` (:func:`search_second_brain`,
:func:`read_brain_note`) never write, never shell out, and never accept a path that resolves
outside the vault (:func:`read_brain_note`'s traversal guard: resolve, then require
``is_relative_to(brain_root().resolve())`` — a rejected or unresolved path degrades to the same
``{"ok": False, "reason": "note not found"}`` a genuinely missing note gets, so a probing name
never learns whether it escaped the vault or simply did not exist).

``brain_root()`` reads the env var at CALL time (``os.environ``, never cached at import) so tests
can ``monkeypatch.setenv``/``delenv`` it per-case, and so a running process picks up the var
without a restart. It returns ``None`` — never raises — when the var is unset, blank, or does not
point at an existing directory; :func:`.production_tools.build_production_tool_specs` reads that
``None`` to decide whether the two tools exist at all for this run, mirroring how a missing VLM
backend simply narrows the toolset rather than breaking it.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

_ENV_VAR = "LAURA_SECONDBRAIN_PATH"
_SNIPPET_RADIUS = 120
_MAX_NOTE_CHARS = 8000


def _not_found() -> dict[str, Any]:
    """A fresh ``{"ok": False, "reason": "note not found"}`` dict, never a shared reference.

    ``read_brain_note`` returns this from four different branches; a single module-level dict
    handed out by reference would let one caller's in-place mutation of its own "immutable"
    reply corrupt what every other caller sees next.
    """
    return {"ok": False, "reason": "note not found"}


def brain_root() -> Path | None:
    """The second-brain vault directory, or ``None`` if not configured.

    Reads ``LAURA_SECONDBRAIN_PATH`` fresh from ``os.environ`` on every call (never memoized) —
    unset, blank, or pointing at something that is not an existing directory all mean "no vault
    for this run", the same degrade-not-raise contract every optional backend in this package
    follows.
    """
    raw = os.environ.get(_ENV_VAR)
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def search_second_brain(query: str, limit: int = 8) -> dict[str, Any]:
    """Case-insensitive substring search over every note's name and content in the second brain.

    Use this to check whether a product name, feature term or fact you are about to write into
    the script is actually documented anywhere before treating it as known. Returns up to
    ``limit`` matches, each with the note's name, its path inside the vault, and a short snippet
    (~240 characters) centered on the first match — read the snippet, then call
    ``read_brain_note`` for the full note if you need more context.
    """
    root = brain_root()
    if root is None:
        return {"ok": False, "reason": "second brain not configured"}
    needle = query.strip().lower()
    if not needle:
        return {"ok": True, "results": []}
    root_resolved = root.resolve()
    results: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*.md")):
        if len(results) >= limit:
            break
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            continue  # symlink (or similar) escaping the vault — never surfaced
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        stem = path.stem
        idx = content.lower().find(needle)
        if idx == -1:
            if needle not in stem.lower():
                continue
            snippet = content[: 2 * _SNIPPET_RADIUS].strip()
        else:
            start = max(0, idx - _SNIPPET_RADIUS)
            end = min(len(content), idx + len(needle) + _SNIPPET_RADIUS)
            snippet = content[start:end].strip()
        try:
            relpath = resolved.relative_to(root_resolved).as_posix()
        except ValueError:
            relpath = path.name
        results.append({"note": stem, "path": relpath, "snippet": snippet})
    return {"ok": True, "results": results}


def read_brain_note(name: str) -> dict[str, Any]:
    """Read one second-brain note in full by name (case-insensitive, extension optional).

    Looks the name up against every note's file stem inside the vault, so ``"Product Names"``
    finds ``product names.md`` regardless of case. Content is capped at 8000 characters (the note
    itself is unmodified on disk — this is a read-only excerpt). A name that does not match any
    note, or that would resolve outside the vault, reports the SAME ``{"ok": False, "reason":
    "note not found"}`` either way — never leaks whether a path merely does not exist or was
    rejected as a traversal attempt.
    """
    root = brain_root()
    if root is None:
        return _not_found()
    root_resolved = root.resolve()
    wanted = Path(name).stem.lower()
    if not wanted:
        return _not_found()
    for path in sorted(root.rglob("*.md")):
        if path.stem.lower() != wanted:
            continue
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if not resolved.is_relative_to(root_resolved):
            continue  # traversal guard — never surface a path outside the vault
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return _not_found()
        return {"ok": True, "note": path.stem, "content": content[:_MAX_NOTE_CHARS]}
    return _not_found()
