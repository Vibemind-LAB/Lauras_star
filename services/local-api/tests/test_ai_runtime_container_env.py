from __future__ import annotations

import subprocess
from pathlib import Path

from laura.ai.docker_runtime import DockerAdapter
from laura.config import Settings
from laura.db import repos
from laura.db.database import create_database


def _db(tmp_path: Path):
    db = create_database(Settings(workspace_root=tmp_path, start_runner=False))
    db.migrate()
    return db


def test_ai_runtime_round_trips_container_env(tmp_path: Path) -> None:
    db = _db(tmp_path)
    runtime = repos.create_ai_runtime(
        db,
        kind="container",
        effect="voice",
        display_name="Voice Sidecar",
        container_image="laura-runtime-voice:local",
        container_name="laura-runtime-voice",
        port=8898,
        container_env={
            "LAURA_RUNTIME_MODE": "smoke",
            "LAURA_VOICE_MODEL_COMMAND": "python /models/run_voice.py",
        },
    )

    loaded = repos.get_ai_runtime(db, runtime["id"])

    assert loaded is not None
    assert loaded["container_env"] == {
        "LAURA_RUNTIME_MODE": "smoke",
        "LAURA_VOICE_MODEL_COMMAND": "python /models/run_voice.py",
    }


def test_docker_adapter_passes_container_env_and_normalizes_mounts(
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run(args: list[str]) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        if args[:3] == ["docker", "ps", "-a"]:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "container-id\n", "")

    adapter = DockerAdapter()
    monkeypatch.setattr(adapter, "_run", fake_run)

    health = adapter.start(
        {
            "container_name": "laura-runtime-liveportrait",
            "container_image": "laura-runtime-liveportrait:local",
            "port": 8899,
            "workspace_mount": "C:/Laura/workspace",
            "model_mount": "E:/LauraModels/liveportrait",
            "container_env": {
                "LAURA_RUNTIME_MODE": "smoke",
                "LAURA_MODEL_ROOT": "/models",
                "lowercase_ignored": "bad",
                "BAD-NAME": "bad",
            },
            "requires_gpu": False,
        }
    )

    docker_run = calls[-1]
    assert health.state == "starting"
    assert _contains_pair(docker_run, "-e", "LAURA_RUNTIME_MODE=smoke")
    assert _contains_pair(docker_run, "-e", "LAURA_MODEL_ROOT=/models")
    assert "lowercase_ignored=bad" not in docker_run
    assert "BAD-NAME=bad" not in docker_run
    assert _contains_pair(docker_run, "-v", "C:/Laura/workspace:/workspace")
    assert _contains_pair(docker_run, "-v", "E:/LauraModels/liveportrait:/models:ro")


def _contains_pair(items: list[str], flag: str, value: str) -> bool:
    return any(left == flag and right == value for left, right in _pairs(items))


def _pairs(items: list[str]) -> list[tuple[str, str]]:
    return [(items[idx], items[idx + 1]) for idx in range(len(items) - 1)]
