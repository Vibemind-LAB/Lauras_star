"""VE5 — visual query logic in :mod:`laura.analysis.visual_query`.

In-memory SQLite, a succeeded analysis run, synthetic frame embeddings written via
:meth:`SqliteVectorStore.replace_frame_embeddings`, and three short candidates
persisted via :func:`repos.replace_shorts_candidates`. Two candidates share a
near-identical visual region; one is distinct.

The image-image operations (``similar_segments``, ``deduplicate_shorts``,
``visual_hook``) need NO model. ``search_visual_moments`` is exercised with a
deterministic *fake* :class:`TextEmbedder` — the real CLIP text model is never
downloaded.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np

from laura.analysis.embeddings_store import FrameEmbedding, SqliteVectorStore
from laura.analysis.visual_query import (
    deduplicate_shorts,
    search_visual_moments,
    similar_segments,
    visual_hook,
)
from laura.config import Settings
from laura.db import repos
from laura.db.database import SqliteDatabase

RATE_NUM = 25
RATE_DEN = 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _vec(*values: float) -> np.ndarray:
    return np.array(values, dtype=np.float32)


# Three directions in a 3-D unit space.
_E_X = _vec(1.0, 0.0, 0.0)
_E_Y = _vec(0.0, 1.0, 0.0)
_E_Z = _vec(0.0, 0.0, 1.0)


def _make_db(tmp_path: Path) -> SqliteDatabase:
    settings = Settings(workspace_root=tmp_path, token=None, start_runner=False)
    db = SqliteDatabase(settings.db_path)
    db.migrate()
    return db


def _project_asset_run(db: SqliteDatabase) -> tuple[dict[str, Any], dict[str, Any], str]:
    """Create a project, an asset (25 fps, 1000 frames) and a succeeded run."""
    project = repos.create_project(
        db,
        name="ve5",
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        drop_frame=False,
        workspace_root="/workspace",
    )
    asset = repos.create_asset(
        db,
        project_id=project["id"],
        type="video",
        display_name="clip.mov",
        source_path="/media/clip.mov",
    )
    repos.update_asset_probe(
        db,
        asset["id"],
        type="video",
        duration_frames=1000,
        rate_num=RATE_NUM,
        rate_den=RATE_DEN,
        audio_sample_rate=48000,
        start_timecode=None,
        width=1920,
        height=1080,
        codec_video="h264",
        codec_audio="aac",
        is_vfr=False,
        sha256=None,
    )
    run = repos.create_analysis_run(
        db, asset_id=asset["id"], pipeline_version="1", config={"stages": {}}
    )
    repos.start_analysis_run(db, run["id"])
    repos.finish_analysis_run(db, run["id"], status="succeeded", diagnostics={})
    asset = repos.get_asset(db, asset["id"])  # refreshed with rate
    assert asset is not None
    return project, asset, run["id"]


def _store_embeddings(
    db: SqliteDatabase,
    asset_id: str,
    run_id: str,
    frame_vecs: dict[int, np.ndarray],
) -> None:
    store = SqliteVectorStore(db)
    items = [
        FrameEmbedding(
            asset_id=asset_id,
            analysis_run_id=run_id,
            frame=frame,
            model="fake-clip",
            vector=vec,
        )
        for frame, vec in frame_vecs.items()
    ]
    store.replace_frame_embeddings(asset_id, run_id, items)


def _seed_three_candidates(
    db: SqliteDatabase, project: dict[str, Any], asset: dict[str, Any]
) -> None:
    """A (0-100, X-region), B (200-300, X-region ~dup of A), C (400-500, Y-region)."""
    candidates = [
        {
            "start_frame": 0,
            "end_frame_exclusive": 100,
            "start_boundary": "sentence_end",
            "end_boundary": "sentence_end",
            "score": 0.9,
            "rejected": False,
            "reject_reason": None,
            "score_breakdown": {"transcript_safety": 0.9},
            "qa_passed": True,
            "qa_issues": [],
        },
        {
            "start_frame": 200,
            "end_frame_exclusive": 300,
            "start_boundary": "sentence_end",
            "end_boundary": "sentence_end",
            "score": 0.7,
            "rejected": False,
            "reject_reason": None,
            "score_breakdown": {"transcript_safety": 0.7},
            "qa_passed": True,
            "qa_issues": [],
        },
        {
            "start_frame": 400,
            "end_frame_exclusive": 500,
            "start_boundary": "sentence_end",
            "end_boundary": "sentence_end",
            "score": 0.5,
            "rejected": False,
            "reject_reason": None,
            "score_breakdown": {"transcript_safety": 0.5},
            "qa_passed": True,
            "qa_issues": [],
        },
    ]
    repos.replace_shorts_candidates(
        db,
        project_id=project["id"],
        asset_id=asset["id"],
        source_timeline_id="tl-fake",
        candidates=candidates,
    )


def _ids_by_start(db: SqliteDatabase, asset_id: str) -> dict[int, str]:
    """Map start_frame → candidate id (order_index follows insertion order)."""
    cands = repos.list_shorts_candidates_by_asset(db, asset_id)
    return {int(c["start_frame"]): c["id"] for c in cands}


def _seed_full(
    db: SqliteDatabase,
) -> tuple[dict[str, Any], dict[str, Any], str, dict[int, str]]:
    """Full fixture: project, asset, run, candidates A/B (X-region) + C (Y-region)."""
    project, asset, run_id = _project_asset_run(db)
    # Candidate A window [0,100): X-direction frames. B window [200,300): also X.
    # C window [400,500): Y-direction frames (distinct). Sprinkle samples so each
    # window has >=2 samples for continuity and a clean mean.
    frame_vecs = {
        0: _E_X, 25: _E_X, 50: _E_X, 75: _E_X,        # A → X
        200: _E_X, 225: _E_X, 250: _E_X, 275: _E_X,   # B → X (near-dup of A)
        400: _E_Y, 425: _E_Y, 450: _E_Y, 475: _E_Y,   # C → Y (distinct)
    }
    _store_embeddings(db, asset["id"], run_id, frame_vecs)
    _seed_three_candidates(db, project, asset)
    return project, asset, run_id, _ids_by_start(db, asset["id"])


class _FakeTextEmbedder:
    """Deterministic :class:`TextEmbedder` returning a fixed vector. No model."""

    def __init__(self, vector: np.ndarray, *, name: str = "fake-text") -> None:
        self._vector = np.asarray(vector, dtype=np.float32)
        self.name = name
        self.dims = int(self._vector.shape[0])

    def embed_text(self, text: str) -> np.ndarray:
        return self._vector


# ---------------------------------------------------------------------------
# similar_segments
# ---------------------------------------------------------------------------


def test_similar_segments_finds_near_duplicate_top1(tmp_path: Path) -> None:
    """The target's near-duplicate (same X-region) ranks top-1."""
    db = _make_db(tmp_path)
    _, asset, _, ids = _seed_full(db)

    result = similar_segments(db, asset["id"], ids[0])

    assert result["ok"] is True
    assert result["candidate_id"] == ids[0]
    similar = result["similar"]
    assert len(similar) == 2  # the two other candidates
    # B (start 200, X-region) is the near-duplicate → top-1 with score ~1.0
    assert similar[0]["candidate_id"] == ids[200]
    assert similar[0]["score"] > 0.99
    assert similar[0]["start_frame"] == 200
    assert similar[0]["end_frame_exclusive"] == 300
    # C (Y-region) is orthogonal → much lower
    assert similar[1]["candidate_id"] == ids[400]
    assert similar[0]["score"] > similar[1]["score"]


def test_similar_segments_respects_k(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _, asset, _, ids = _seed_full(db)

    result = similar_segments(db, asset["id"], ids[0], k=1)
    assert result["ok"] is True
    assert len(result["similar"]) == 1
    assert result["similar"][0]["candidate_id"] == ids[200]


def test_similar_segments_no_embeddings(tmp_path: Path) -> None:
    """No stored frame vectors → ok=False with a clear reason."""
    db = _make_db(tmp_path)
    project, asset, _run_id = _project_asset_run(db)
    _seed_three_candidates(db, project, asset)
    ids = _ids_by_start(db, asset["id"])

    result = similar_segments(db, asset["id"], ids[0])
    assert result["ok"] is False
    assert "embed_frames" in result["reason"]


def test_similar_segments_unknown_candidate(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _, asset, _, _ids = _seed_full(db)

    result = similar_segments(db, asset["id"], "no-such-candidate")
    assert result["ok"] is False
    assert result["reason"] == "candidate not found"


# ---------------------------------------------------------------------------
# deduplicate_shorts
# ---------------------------------------------------------------------------


def test_deduplicate_groups_near_duplicates(tmp_path: Path) -> None:
    """A and B (X-region) collapse into one group; C (Y-region) stays separate."""
    db = _make_db(tmp_path)
    _, asset, _, ids = _seed_full(db)

    result = deduplicate_shorts(db, asset["id"])

    assert result["ok"] is True
    # Two groups: {A keep, B dup} and {C keep, no dup}
    assert len(result["groups"]) == 2

    # Highest score (A, score 0.9) is processed first and becomes the keeper of B.
    first = result["groups"][0]
    assert first["keep"] == ids[0]
    assert first["duplicates"] == [ids[200]]

    second = result["groups"][1]
    assert second["keep"] == ids[400]
    assert second["duplicates"] == []

    assert set(result["kept"]) == {ids[0], ids[400]}
    assert result["dropped"] == [ids[200]]


def test_deduplicate_threshold_keeps_all_when_high(tmp_path: Path) -> None:
    """A threshold above the dup similarity keeps every candidate separate."""
    db = _make_db(tmp_path)
    _, asset, _, ids = _seed_full(db)

    # A and B are cosine ~1.0; a threshold > 1 means nothing groups.
    result = deduplicate_shorts(db, asset["id"], threshold=1.01)
    assert result["ok"] is True
    assert len(result["kept"]) == 3
    assert result["dropped"] == []


def test_deduplicate_no_embeddings(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    project, asset, _run_id = _project_asset_run(db)
    _seed_three_candidates(db, project, asset)

    result = deduplicate_shorts(db, asset["id"])
    assert result["ok"] is False
    assert "embed_frames" in result["reason"]


# ---------------------------------------------------------------------------
# visual_hook
# ---------------------------------------------------------------------------


def test_visual_hook_scores_in_unit_range(tmp_path: Path) -> None:
    """A candidate whose opening is a coherent X-region: continuity ~1, shift small."""
    db = _make_db(tmp_path)
    _, asset, _, ids = _seed_full(db)

    result = visual_hook(db, asset["id"], ids[0])

    assert result["ok"] is True
    assert result["candidate_id"] == ids[0]
    shift = result["visual_shift_at_start"]
    cont = result["opening_continuity"]
    hook = result["hook_score"]
    # hook_score is guaranteed [0, 1].
    assert 0.0 <= hook <= 1.0
    # Raw diagnostics are returned as-is (may sit outside [0,1]).
    # Opening is a flat X-region → high continuity.
    assert cont > 0.99
    # Verify the normalised formula:
    #   shift_norm = shift / 2, cont_clamped = max(0, cont)
    #   hook = clip(0.6*shift_norm + 0.4*cont_clamped, 0, 1)
    shift_norm = shift / 2.0
    cont_clamped = max(0.0, cont)
    expected = min(1.0, max(0.0, 0.6 * shift_norm + 0.4 * cont_clamped))
    assert abs(hook - expected) < 1e-6
    assert isinstance(result["explanation"], str) and result["explanation"]


def test_visual_hook_strong_shift_at_start(tmp_path: Path) -> None:
    """A hard cut entering the clip yields a high visual_shift_at_start."""
    db = _make_db(tmp_path)
    project, asset, run_id = _project_asset_run(db)
    # The start cut at 100 sits *between* samples: nearest-before (90) is Y,
    # nearest-after (110) is X → shift = 1 - cos(Y, X) = 1. A sample exactly on
    # the cut would make before==after (shift 0), so we deliberately avoid 100.
    frame_vecs = {
        90: _E_Y,
        110: _E_X, 125: _E_X, 150: _E_X,
    }
    _store_embeddings(db, asset["id"], run_id, frame_vecs)
    candidates = [
        {
            "start_frame": 100,
            "end_frame_exclusive": 200,
            "start_boundary": "sentence_end",
            "end_boundary": "sentence_end",
            "score": 0.8,
            "rejected": False,
            "reject_reason": None,
            "score_breakdown": {},
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
    cid = repos.list_shorts_candidates_by_asset(db, asset["id"])[0]["id"]

    result = visual_hook(db, asset["id"], cid)
    assert result["ok"] is True
    assert result["visual_shift_at_start"] > 0.99


def test_visual_hook_no_embeddings(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    project, asset, _run_id = _project_asset_run(db)
    _seed_three_candidates(db, project, asset)
    ids = _ids_by_start(db, asset["id"])

    result = visual_hook(db, asset["id"], ids[0])
    assert result["ok"] is False
    assert "embed_frames" in result["reason"]


def test_visual_hook_unknown_candidate(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _, asset, _, _ids = _seed_full(db)

    result = visual_hook(db, asset["id"], "no-such-candidate")
    assert result["ok"] is False
    assert result["reason"] == "candidate not found"


# ---------------------------------------------------------------------------
# search_visual_moments
# ---------------------------------------------------------------------------


def test_search_visual_moments_fake_embedder_top1(tmp_path: Path) -> None:
    """A fake text embedder near a known frame vector ranks that frame top-1."""
    db = _make_db(tmp_path)
    _, asset, _, _ids = _seed_full(db)

    # Query vector very close to the Y-region (C) frames.
    fake = _FakeTextEmbedder(_vec(0.0, 1.0, 0.0))
    result = search_visual_moments(db, asset["id"], "a yellow scene", text_embedder=fake)

    assert result["ok"] is True
    assert result["query"] == "a yellow scene"
    moments = result["moments"]
    assert moments  # non-empty
    top = moments[0]
    # The top frame must be one of the Y-region frames (400..475), score ~1.0.
    assert top["frame"] in (400, 425, 450, 475)
    assert top["score"] > 0.99
    # time_s computed from frame / fps (25 fps).
    assert abs(top["time_s"] - top["frame"] / 25.0) < 1e-6


def test_search_visual_moments_respects_k(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _, asset, _, _ids = _seed_full(db)
    fake = _FakeTextEmbedder(_vec(1.0, 0.0, 0.0))

    result = search_visual_moments(db, asset["id"], "x", k=3, text_embedder=fake)
    assert result["ok"] is True
    assert result["k"] == 3
    assert len(result["moments"]) == 3


def test_search_visual_moments_no_embeddings(tmp_path: Path) -> None:
    db = _make_db(tmp_path)
    _project, asset, _run_id = _project_asset_run(db)
    fake = _FakeTextEmbedder(_vec(1.0, 0.0, 0.0))

    result = search_visual_moments(db, asset["id"], "x", text_embedder=fake)
    assert result["ok"] is False
    assert "embed_frames" in result["reason"]


def test_search_visual_moments_no_embedder_and_unavailable(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """text_embedder=None + extra unavailable → ok=False with the text reason.

    We force ``visual_available`` to report False so the test never touches
    fastembed, regardless of whether the extra is installed in CI.
    """
    import laura.analysis.visual_query as vq

    monkeypatch.setattr(vq, "visual_available", lambda: False)

    db = _make_db(tmp_path)
    _, asset, _, _ids = _seed_full(db)

    result = search_visual_moments(db, asset["id"], "x", text_embedder=None)
    assert result["ok"] is False
    assert "text search unavailable" in result["reason"]


# ---------------------------------------------------------------------------
# visual_hook — adversarial: negative-cosine embeddings must stay in [0, 1]
# ---------------------------------------------------------------------------


def test_visual_hook_negative_cosine_stays_in_unit_range(tmp_path: Path) -> None:
    """hook_score must be in [0,1] even when adjacent/opening cosines are negative.

    Opposing unit vectors (e.g. X and -X) produce a cosine of -1, which previously
    caused hook_score to go below 0 or above 1 before the normalisation fix.
    """
    db = _make_db(tmp_path)
    project, asset, run_id = _project_asset_run(db)

    # Frame before cut (99): +X. Frames inside clip: −X (opposing direction).
    # cosine(+X, -X) = -1 → shift = 1 − (−1) = 2 (max possible).
    # continuity in opening window: all -X frames → cosine = 1 for within-window
    # pairs, but the raw _visual_continuity uses mean cosine of consecutive pairs;
    # with identical -X vectors that is still 1 (not negative).  To also exercise
    # negative continuity, mix the opening with an anti-correlated frame.
    _neg_x = _vec(-1.0, 0.0, 0.0)
    frame_vecs = {
        99: _E_X,       # frame just before the cut — yields maximum shift
        100: _neg_x,    # first frame of clip
        110: _E_X,      # opposing direction → continuity cosine = -1 for this pair
        120: _neg_x,
        150: _neg_x,
    }
    _store_embeddings(db, asset["id"], run_id, frame_vecs)
    candidates = [
        {
            "start_frame": 100,
            "end_frame_exclusive": 200,
            "start_boundary": "sentence_end",
            "end_boundary": "sentence_end",
            "score": 0.8,
            "rejected": False,
            "reject_reason": None,
            "score_breakdown": {},
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
    cid = repos.list_shorts_candidates_by_asset(db, asset["id"])[0]["id"]

    result = visual_hook(db, asset["id"], cid)
    assert result["ok"] is True
    hook = result["hook_score"]
    # The critical invariant: hook_score must always be in [0, 1].
    assert 0.0 <= hook <= 1.0, f"hook_score out of range: {hook}"
    # Raw diagnostics are returned unchanged (may be outside [0,1]).
    assert "visual_shift_at_start" in result
    assert "opening_continuity" in result


# ---------------------------------------------------------------------------
# search_visual_moments — dim-mismatch degrades gracefully (no crash)
# ---------------------------------------------------------------------------


def test_search_visual_moments_dim_mismatch_returns_ok_false(tmp_path: Path) -> None:
    """A text embedder returning a wrong-dimension vector must not raise.

    Frame embeddings are 3-D (_E_X etc.); injecting a 5-D query vector should
    return ``{"ok": False, "reason": ...}`` rather than crashing with ValueError.
    """
    db = _make_db(tmp_path)
    _, asset, _, _ids = _seed_full(db)

    # 5-D query vector, but stored frame embeddings are 3-D.
    wrong_dim_embedder = _FakeTextEmbedder(_vec(1.0, 0.0, 0.0, 0.0, 0.0), name="wrong-dim")
    result = search_visual_moments(
        db, asset["id"], "a scene", text_embedder=wrong_dim_embedder
    )

    assert result["ok"] is False, "Expected ok=False for dim mismatch, got ok=True"
    assert "dim" in result["reason"], f"Unexpected reason: {result['reason']}"
