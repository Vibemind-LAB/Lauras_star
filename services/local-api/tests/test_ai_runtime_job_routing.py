from __future__ import annotations

from laura.ai.handlers import _backend_from_runtime
from laura.config import Settings
from laura.db import repos
from laura.db.database import create_database


def _db(tmp_path):
    db = create_database(Settings(workspace_root=tmp_path, start_runner=False))
    db.migrate()
    return db


def test_backend_from_runtime_maps_stub_to_stub(tmp_path):
    db = _db(tmp_path)
    runtime = repos.create_ai_runtime(
        db,
        kind="stub",
        effect="lipsync",
        display_name="Stub Lipsync",
    )

    assert _backend_from_runtime(db, runtime["id"], "vibevideo") == "stub"


def test_backend_from_runtime_maps_external_and_container_to_legacy_backend(tmp_path):
    db = _db(tmp_path)
    external = repos.create_ai_runtime(
        db,
        kind="external_http",
        effect="voice",
        display_name="Voice HTTP",
        base_url="http://127.0.0.1:8898",
    )
    container = repos.create_ai_runtime(
        db,
        kind="container",
        effect="reenact",
        display_name="LivePortrait",
        container_image="laura-runtime-liveportrait:local",
    )

    assert _backend_from_runtime(db, external["id"], None) == "sidecar"
    assert _backend_from_runtime(db, container["id"], None) == "liveportrait"


def test_backend_from_runtime_keeps_legacy_fallback_without_runtime(tmp_path):
    db = _db(tmp_path)

    assert _backend_from_runtime(db, None, "stub") == "stub"
