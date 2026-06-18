from __future__ import annotations

import json
from pathlib import Path

from laura.ai.provenance import write_ai_provenance_manifest


def test_write_ai_provenance_manifest_contains_required_fields(tmp_path: Path) -> None:
    media = tmp_path / "synthetic.mp4"
    media.write_bytes(b"synthetic-media")

    manifest_path = write_ai_provenance_manifest(
        media_path=media,
        asset_id="asset-1",
        project_id="project-1",
        ai_effect="lipsync",
        source={
            "timeline_id": "timeline-1",
            "seq_in_frame": 10,
            "seq_out_frame_exclusive": 40,
            "consent_id": "consent-1",
        },
    )

    assert manifest_path == media.with_name("synthetic.mp4.laura-provenance.json")
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["schema"] == "laura.ai.provenance.v1"
    assert data["asset_id"] == "asset-1"
    assert data["project_id"] == "project-1"
    assert data["synthetic"] is True
    assert data["ai_effect"] == "lipsync"
    assert data["media_path"] == str(media)
    assert data["media_sha256"]
    assert data["created_at"]
    assert data["source"] == {
        "timeline_id": "timeline-1",
        "seq_in_frame": 10,
        "seq_out_frame_exclusive": 40,
        "consent_id": "consent-1",
    }
