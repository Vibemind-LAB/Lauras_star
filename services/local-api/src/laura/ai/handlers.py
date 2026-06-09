"""Job handler: ai.reenact — consent-gated portrait reenactment.

SAFETY-CRITICAL: the consent gate is checked FIRST, before any DB writes or
file I/O.  If the consent record is missing or the payload is malformed the
handler raises immediately and creates nothing.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..db import repos
from ..jobs.runner import JobContext, JobHandler
from ..render.mp4 import render_clips_mp4
from ..sequences.flatten import flatten_sequence
from ..util import new_id
from .reenact_backend import resolve_reenact_backend


def handle_reenact(ctx: JobContext) -> dict[str, Any]:
    """Consent-gated reenact job.

    Steps (in order):
    1. Consent gate — raises immediately if consent_id is missing or unknown.
    2. Resolve timeline + project (rate_num / rate_den).
    3. Extract the driving range from base clip rows that overlap [seq_in, seq_out).
    4. Render the driving clip to a temporary MP4.
    5. Resolve and check the reenact backend.
    6. Animate the portrait asset, write to the project workspace.
    7. Register the output as a synthetic asset.
    8. Place a replace-overlay clip on the timeline.
    """
    payload = ctx.payload

    # ── 1. CONSENT GATE (must be first — creates nothing on failure) ──────────
    consent_id: str | None = payload.get("consent_id")
    if not consent_id:
        raise ValueError("ai.reenact: payload missing required key 'consent_id'")

    consent = repos.get_consent_record(ctx.db, consent_id)
    if consent is None:
        raise ValueError(
            f"ai.reenact: consent record not found: {consent_id!r} — "
            "refusing to create any asset or clip"
        )
    if consent.get("revoked_at"):
        raise ValueError(
            f"ai.reenact: consent {consent_id!r} has been revoked — "
            "refusing to create any asset or clip"
        )

    # ── 2. Resolve timeline + project ────────────────────────────────────────
    timeline_id: str = payload["timeline_id"]
    tl = repos.get_timeline(ctx.db, timeline_id)
    if tl is None:
        raise ValueError(f"ai.reenact: timeline not found: {timeline_id!r}")

    project = repos.get_project(ctx.db, tl["project_id"])
    if project is None:
        raise ValueError(f"ai.reenact: project not found: {tl['project_id']!r}")

    rate_num: int = int(project["sequence_rate_num"])
    rate_den: int = int(project["sequence_rate_den"])

    seq_in: int = int(payload["seq_in_frame"])
    seq_out: int = int(payload["seq_out_frame_exclusive"])

    # ── 3. Build the driving clip list from overlapping ORIGINAL base rows ────
    # Drive from original base footage ONLY — never from a prior synthetic
    # replace-overlay (provenance: a reenact must not be driven by another
    # reenact's output). So we resolve base rows directly, WITHOUT precedence.
    if tl.get("kind") == "sequence":
        base_rows = flatten_sequence(ctx.db, tl["id"])
    else:
        base_rows = [
            r
            for r in repos.list_timeline_clips(ctx.db, tl["id"])
            if r.get("role", "base") != "replace"
        ]

    driving_clips: list[tuple[Path, int, int]] = []
    for row in base_rows:
        row_seq_in = int(row["seq_in_frame"])
        row_seq_out = int(row["seq_out_frame_exclusive"])
        row_src_in = int(row["src_in_frame"])

        o_in = max(row_seq_in, seq_in)
        o_out = min(row_seq_out, seq_out)
        if o_in >= o_out:
            continue  # no overlap

        s_in = row_src_in + (o_in - row_seq_in)
        s_out = row_src_in + (o_out - row_seq_in)

        asset = repos.get_asset(ctx.db, row["asset_id"])
        if asset is None:
            raise ValueError(f"ai.reenact: asset not found: {row['asset_id']!r}")

        driving_clips.append((Path(asset["source_path"]), s_in, s_out))

    if not driving_clips:
        raise ValueError(
            f"ai.reenact: no base clips overlap driving range [{seq_in}, {seq_out})"
        )

    # ── 4. Render the driving range to a temp file ────────────────────────────
    workspace = Path(project["workspace_root"])
    tmp_dir = workspace / "tmp"
    synthetic_dir = workspace / "synthetic"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    synthetic_dir.mkdir(parents=True, exist_ok=True)

    # Bind both paths up front so out_path can be cleaned on ANY failure below
    # (no orphaned, unlabelled partial synthetic file may ever survive a crash).
    driving_tmp = tmp_dir / f"{new_id()}.driving.mp4"
    out_path = synthetic_dir / f"{new_id()}.mp4"
    try:
        render_clips_mp4(
            driving_clips,
            driving_tmp,
            rate_num=rate_num,
            rate_den=rate_den,
        )

        # ── 5. Resolve and validate backend ──────────────────────────────────
        backend = resolve_reenact_backend(payload.get("backend"))
        if not backend.available():
            raise RuntimeError(
                f"ai.reenact: reenact backend '{backend.name}' is not installed"
            )

        # ── 6. Portrait asset → animate ──────────────────────────────────────
        portrait_asset_id: str = payload["portrait_asset_id"]
        portrait = repos.get_asset(ctx.db, portrait_asset_id)
        if portrait is None:
            raise ValueError(
                f"ai.reenact: portrait asset not found: {portrait_asset_id!r}"
            )

        backend.reenact(
            driving_path=driving_tmp,
            portrait_path=Path(portrait["source_path"]),
            out_path=out_path,
            fps_num=rate_num,
            fps_den=rate_den,
        )
    except Exception:
        # Never leave an unlabelled partial synthetic file behind on failure.
        out_path.unlink(missing_ok=True)
        raise
    finally:
        # The temporary driving file is never needed after this block.
        driving_tmp.unlink(missing_ok=True)

    # ── 7. Register the output as a synthetic asset ───────────────────────────
    asset = repos.create_asset(
        ctx.db,
        project_id=tl["project_id"],
        type="video",
        display_name=f"reenact {seq_in}-{seq_out}",
        source_path=str(out_path),
        synthetic=True,
        ai_effect="reenact",
    )

    # ── 8. Place a replace-overlay clip on the timeline ───────────────────────
    repos.add_timeline_clip(
        ctx.db,
        timeline_id=tl["id"],
        asset_id=asset["id"],
        src_in_frame=0,
        src_out_frame_exclusive=seq_out - seq_in,
        seq_in_frame=seq_in,
        seq_out_frame_exclusive=seq_out,
        lane=1,
        role="replace",
    )

    return {
        "asset_id": asset["id"],
        "out_path": str(out_path),
        "seq_in_frame": seq_in,
        "seq_out_frame_exclusive": seq_out,
    }


def register_ai_handlers(registry: dict[str, JobHandler]) -> None:
    """Register all AI-stage job handlers into ``registry``."""
    registry["ai.reenact"] = handle_reenact
