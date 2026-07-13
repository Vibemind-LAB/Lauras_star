"""Versioned artifact store for v2 production sessions (the "Production Board").

Layout under ``<workspace>/agent-runs/<session_id>/board/``::

    meta.json                       BoardMeta
    scene_reviews/scene_<n>.json    one SceneReview per scene
    storyline.json .. qa_report.json  singleton artifacts (see _SINGLETONS)
    versions/<stem>.v<k>.json       append-only archive of every replaced version

Writes are atomic (tmp + replace) and pydantic-validated.  Invalidation always
runs *downstream* along ``_CHAIN`` — never upstream — so cached upstream work
(above all: scene reviews) survives every adjust/revert.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel

from laura.short_creator.board_models import (
    BoardMeta,
    Cutlist,
    QaReport,
    RenderReport,
    SceneReview,
    Script,
    Storyline,
    VoiceArtifact,
)

_CHAIN: tuple[str, ...] = ("storyline", "script", "voice", "cutlist", "render_report", "qa_report")
_SINGLETONS: dict[str, type[BaseModel]] = {
    "storyline": Storyline,
    "script": Script,
    "voice": VoiceArtifact,
    "cutlist": Cutlist,
    "render_report": RenderReport,
    "qa_report": QaReport,
}


def downstream_of(name: str) -> tuple[str, ...]:
    """Artifacts invalidated by a change to ``name`` (chain order preserved)."""
    if name == "scene_reviews":
        return _CHAIN
    return _CHAIN[_CHAIN.index(name) + 1 :]


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


class Board:
    """One production session's artifact store."""

    def __init__(self, root: Path) -> None:
        self.root = root

    @classmethod
    def create(cls, root: Path, meta: BoardMeta) -> Board:
        (root / "scene_reviews").mkdir(parents=True, exist_ok=True)
        (root / "versions").mkdir(parents=True, exist_ok=True)
        _write_atomic(root / "meta.json", meta.model_dump_json(indent=2))
        return cls(root)

    @classmethod
    def open(cls, root: Path) -> Board:
        if not (root / "meta.json").is_file():
            raise FileNotFoundError(f"no board at {root}")
        return cls(root)

    def meta(self) -> BoardMeta:
        raw = (self.root / "meta.json").read_text(encoding="utf-8")
        return BoardMeta.model_validate_json(raw)

    # -- scene reviews ---------------------------------------------------

    def save_scene_review(self, review: SceneReview) -> int:
        stem = f"scene_{review.scene_number}"
        path = self.root / "scene_reviews" / f"{stem}.json"
        current_version = 0
        if path.is_file():
            old = SceneReview.model_validate_json(path.read_text(encoding="utf-8"))
            current_version = old.version
            self._archive(stem, old.version, path)
        version = max([current_version, *self.versions(stem)]) + 1
        stamped = review.model_copy(update={"version": version})
        _write_atomic(path, stamped.model_dump_json(indent=2))
        return version

    def scene_reviews(self) -> list[SceneReview]:
        folder = self.root / "scene_reviews"
        reviews = [
            SceneReview.model_validate_json(p.read_text(encoding="utf-8"))
            for p in folder.glob("scene_*.json")
        ]
        return sorted(reviews, key=lambda r: r.scene_number)

    # -- shared internals --------------------------------------------------

    def versions(self, stem: str) -> list[int]:
        """Archived version numbers for a singleton name or ``scene_<n>`` stem."""
        prefix = f"{stem}.v"
        out: list[int] = []
        for p in (self.root / "versions").glob(f"{stem}.v*.json"):
            digits = p.name[len(prefix) : -len(".json")]
            if digits.isdigit():
                out.append(int(digits))
        return sorted(out)

    def _archive(self, stem: str, version: int, path: Path) -> None:
        dest = self.root / "versions" / f"{stem}.v{version}.json"
        _write_atomic(dest, path.read_text(encoding="utf-8"))
