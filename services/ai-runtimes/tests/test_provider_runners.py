from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from providers import liveportrait_runner, musetalk_runner, piper_voice_runner  # noqa: E402


def test_liveportrait_runner_copies_newest_animation_output(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "LivePortrait"
    (repo / "animations").mkdir(parents=True)
    (repo / "inference.py").write_text("", encoding="utf-8")
    portrait = tmp_path / "portrait.png"
    driving = tmp_path / "driving.mp4"
    output = tmp_path / "out.mp4"
    portrait.write_bytes(b"portrait")
    driving.write_bytes(b"driving")
    calls: list[tuple[list[str], Path]] = []

    def fake_run(
        args: list[str],
        *,
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        calls.append((args, cwd))
        old = repo / "animations" / "old.mp4"
        new = repo / "animations" / "new.mp4"
        old.write_bytes(b"old")
        new.write_bytes(b"liveportrait")
        old.touch()
        new.touch()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setenv("LAURA_LIVEPORTRAIT_REPO", str(repo))
    monkeypatch.setenv("LAURA_LIVEPORTRAIT_EXTRA_ARGS", "--flag_crop_driving_video")
    monkeypatch.setenv("LAURA_LIVEPORTRAIT_OUTPUT_GLOB", "animations/*.mp4")
    monkeypatch.setattr(liveportrait_runner.subprocess, "run", fake_run)

    assert (
        liveportrait_runner.main(
            [
                "--portrait",
                str(portrait),
                "--driving",
                str(driving),
                "--output",
                str(output),
                "--model-root",
                str(tmp_path),
            ]
        )
        == 0
    )

    args, cwd = calls[0]
    assert cwd == repo
    assert args[:4] == [sys.executable, "inference.py", "-s", str(portrait)]
    assert args[4:6] == ["-d", str(driving)]
    assert "--flag_crop_driving_video" in args
    assert output.read_bytes() == b"liveportrait"


def test_musetalk_runner_writes_config_and_copies_newest_result(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "MuseTalk"
    repo.mkdir()
    result_dir = tmp_path / "results"
    video = tmp_path / "video.mp4"
    audio = tmp_path / "audio.wav"
    output = tmp_path / "lipsync.mp4"
    video.write_bytes(b"video")
    audio.write_bytes(b"audio")
    captured_config = ""
    calls: list[tuple[list[str], Path]] = []

    def fake_run(
        args: list[str],
        *,
        cwd: Path,
        check: bool,
    ) -> subprocess.CompletedProcess[str]:
        nonlocal captured_config
        calls.append((args, cwd))
        config_path = Path(args[args.index("--inference_config") + 1])
        captured_config = config_path.read_text(encoding="utf-8")
        first = result_dir / "first.mp4"
        nested = result_dir / "nested"
        nested.mkdir(parents=True, exist_ok=True)
        newest = nested / "newest.mp4"
        first.write_bytes(b"first")
        newest.write_bytes(b"musetalk")
        first.touch()
        newest.touch()
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setenv("LAURA_MUSETALK_REPO", str(repo))
    monkeypatch.setenv("LAURA_MUSETALK_RESULT_DIR", str(result_dir))
    monkeypatch.setenv("LAURA_MUSETALK_EXTRA_ARGS", "--skip_save_images")
    monkeypatch.setattr(musetalk_runner.subprocess, "run", fake_run)

    assert (
        musetalk_runner.main(
            [
                "--video",
                str(video),
                "--audio",
                str(audio),
                "--output",
                str(output),
                "--model-root",
                str(tmp_path),
            ]
        )
        == 0
    )

    args, cwd = calls[0]
    assert cwd == repo
    assert args[:4] == [sys.executable, "-m", "scripts.inference", "--inference_config"]
    assert "--result_dir" in args
    assert "--skip_save_images" in args
    assert f"video_path: {video.as_posix()}" in captured_config
    assert f"audio_path: {audio.as_posix()}" in captured_config
    assert output.read_bytes() == b"musetalk"


def test_piper_voice_runner_invokes_piper_with_request_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = tmp_path / "request.json"
    output = tmp_path / "voice.wav"
    request.write_text(json.dumps({"text": "Hallo Laura"}), encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run(args: list[str], *, check: bool) -> subprocess.CompletedProcess[str]:
        calls.append(args)
        output.write_bytes(b"RIFFpiper")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setenv("LAURA_PIPER_VOICE", "en_US-lessac-medium")
    monkeypatch.setenv("LAURA_PIPER_DATA_DIR", str(tmp_path / "piper"))
    monkeypatch.setattr(piper_voice_runner.subprocess, "run", fake_run)

    assert (
        piper_voice_runner.main(
            [
                "--request",
                str(request),
                "--output",
                str(output),
                "--model-root",
                str(tmp_path),
            ]
        )
        == 0
    )

    assert calls == [
        [
            sys.executable,
            "-m",
            "piper",
            "-m",
            "en_US-lessac-medium",
            "-f",
            str(output),
            "--data-dir",
            str(tmp_path / "piper"),
            "--",
            "Hallo Laura",
        ]
    ]
    assert output.read_bytes() == b"RIFFpiper"


def test_piper_voice_runner_rejects_missing_text(tmp_path: Path) -> None:
    request = tmp_path / "request.json"
    output = tmp_path / "voice.wav"
    request.write_text(json.dumps({"text": ""}), encoding="utf-8")

    with pytest.raises(ValueError, match="text"):
        piper_voice_runner.main(
            [
                "--request",
                str(request),
                "--output",
                str(output),
                "--model-root",
                str(tmp_path),
            ]
        )
