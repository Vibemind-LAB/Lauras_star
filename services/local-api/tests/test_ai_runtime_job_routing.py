from __future__ import annotations

from pathlib import Path

import pytest

from laura.ai.handlers import _backend_config_from_runtime, _backend_from_runtime
from laura.config import Settings
from laura.db import repos
from laura.db.database import Database, create_database


def _db(tmp_path: Path) -> Database:
    db = create_database(Settings(workspace_root=tmp_path, start_runner=False))
    db.migrate()
    return db


def test_backend_from_runtime_maps_stub_to_stub(tmp_path: Path) -> None:
    db = _db(tmp_path)
    runtime = repos.create_ai_runtime(
        db,
        kind="stub",
        effect="lipsync",
        display_name="Stub Lipsync",
    )

    assert _backend_from_runtime(db, runtime["id"], "vibevideo") == "stub"


def test_backend_from_runtime_maps_external_and_container_to_legacy_backend(tmp_path: Path) -> None:
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
        port=8899,
    )

    external_config = _backend_config_from_runtime(db, external["id"], None, "voice")
    container_config = _backend_config_from_runtime(db, container["id"], None, "reenact")

    assert _backend_from_runtime(db, external["id"], None, "voice") == "sidecar"
    assert external_config.name == "sidecar"
    assert external_config.base_url == "http://127.0.0.1:8898"
    assert _backend_from_runtime(db, container["id"], None, "reenact") == "liveportrait"
    assert container_config.name == "liveportrait"
    assert container_config.base_url == "http://127.0.0.1:8899"


def test_backend_from_runtime_keeps_legacy_fallback_without_runtime(tmp_path: Path) -> None:
    db = _db(tmp_path)

    assert _backend_from_runtime(db, None, "stub") == "stub"


def test_backend_config_rejects_wrong_effect_runtime(tmp_path: Path) -> None:
    db = _db(tmp_path)
    runtime = repos.create_ai_runtime(
        db,
        kind="stub",
        effect="voice",
        display_name="Stub Voice",
    )

    with pytest.raises(ValueError, match="runtime effect must be lipsync"):
        _backend_config_from_runtime(db, runtime["id"], None, "lipsync")


def test_backend_config_rejects_external_runtime_without_endpoint(tmp_path: Path) -> None:
    db = _db(tmp_path)
    runtime = repos.create_ai_runtime(
        db,
        kind="external_http",
        effect="voice",
        display_name="Voice HTTP",
    )

    with pytest.raises(ValueError, match="runtime has no base_url or port"):
        _backend_config_from_runtime(db, runtime["id"], None, "voice")
