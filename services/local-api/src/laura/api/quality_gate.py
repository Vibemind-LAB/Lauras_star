"""Quality gate helper for MP4 publish endpoints (P1-T2b).

Pure read of the persisted timeline_quality row — never recomputes quality.
Called by render-reel and render endpoints before enqueuing the export job.
"""

from __future__ import annotations

from fastapi import HTTPException, status

from ..db import repos
from ..db.database import Database


def evaluate_quality_gate(
    db: Database,
    timeline_id: str,
    min_quality: float | None,
) -> dict[str, object]:
    """Read the persisted quality row and decide whether to block or stamp.

    Args:
        db: Database handle.
        timeline_id: The timeline being published.
        min_quality: Optional caller-supplied lower bound (0.0–1.0). ``None``
            means "no gate — stamp verified when computed, unverified otherwise".

    Returns:
        A dict with keys ``quality_status`` (str) and ``quality_verified`` (bool)
        suitable for merging into the export ``options`` dict.

    Raises:
        HTTPException 422: When status is ``computed`` AND ``overall < min_quality``.
    """
    row = repos.get_timeline_quality(db, timeline_id)
    quality_status: str = row["status"] if row is not None else "pending"

    if (
        min_quality is not None
        and quality_status == "computed"
        and row is not None
        and (overall := row.get("overall")) is not None
        and float(overall) < min_quality
    ):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"rough-cut quality {float(overall):.2f} is below min_quality {min_quality:.2f}",
        )

    verified: bool = quality_status == "computed" and (
        min_quality is None
        or (
            row is not None
            and row.get("overall") is not None
            and float(row["overall"]) >= min_quality
        )
    )

    return {
        "quality_status": quality_status,
        "quality_verified": verified,
    }
