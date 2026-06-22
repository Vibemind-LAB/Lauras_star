from __future__ import annotations

from pathlib import Path

import pytest

from laura.render.sync import (
    MediaSyncError,
    assert_media_sync,
    assert_or_fix_media_sync,
)


def test_assert_media_sync_accepts_matching_video_and_audio(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_probe(_path: Path) -> dict[str, object]:
        return {
            "streams": [
                {"codec_type": "video", "nb_frames": "30", "r_frame_rate": "30/1"},
                {"codec_type": "audio", "duration": "1.0"},
            ]
        }

    monkeypatch.setattr("laura.render.sync.probe", fake_probe)

    report = assert_media_sync(
        tmp_path / "out.mp4",
        expected_frames=30,
        rate_num=30,
        rate_den=1,
        require_video=True,
    )

    assert report.video_frames == 30
    assert report.audio_frames == 30
    assert report.max_abs_drift_frames == 0


def test_assert_media_sync_rejects_video_frame_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_probe(_path: Path) -> dict[str, object]:
        return {"streams": [{"codec_type": "video", "nb_frames": "42"}]}

    monkeypatch.setattr("laura.render.sync.probe", fake_probe)

    with pytest.raises(MediaSyncError, match="video frame drift"):
        assert_media_sync(
            tmp_path / "out.mp4",
            expected_frames=30,
            rate_num=30,
            rate_den=1,
            tolerance_frames=1,
            require_video=True,
        )


def test_assert_media_sync_rejects_audio_duration_drift(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fake_probe(_path: Path) -> dict[str, object]:
        return {
            "streams": [
                {"codec_type": "video", "nb_frames": "30"},
                {"codec_type": "audio", "duration": "1.5"},
            ]
        }

    monkeypatch.setattr("laura.render.sync.probe", fake_probe)

    with pytest.raises(MediaSyncError, match="audio duration drift"):
        assert_media_sync(
            tmp_path / "out.mp4",
            expected_frames=30,
            rate_num=30,
            rate_den=1,
            tolerance_frames=1,
            require_video=True,
        )


def test_assert_or_fix_media_sync_repairs_once_then_rechecks(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_probe(_path: Path) -> dict[str, object]:
        calls.append("probe")
        if calls.count("probe") == 1:
            return {"streams": [{"codec_type": "video", "nb_frames": "42"}]}
        return {"streams": [{"codec_type": "video", "nb_frames": "30"}]}

    def fake_fix(path: Path, **kwargs: object) -> None:
        calls.append(f"fix:{path.name}:{kwargs['expected_frames']}")

    monkeypatch.setattr("laura.render.sync.probe", fake_probe)
    monkeypatch.setattr("laura.render.sync.fix_media_duration", fake_fix)

    report = assert_or_fix_media_sync(
        tmp_path / "out.mp4",
        expected_frames=30,
        rate_num=30,
        rate_den=1,
        tolerance_frames=1,
        require_video=True,
        fix=True,
    )

    assert report.video_frames == 30
    assert calls == ["probe", "fix:out.mp4:30", "probe"]


def test_assert_or_fix_media_sync_does_not_fix_when_disabled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixed: list[bool] = []

    def fake_probe(_path: Path) -> dict[str, object]:
        return {"streams": [{"codec_type": "video", "nb_frames": "42"}]}

    def fake_fix(_path: Path, **_kwargs: object) -> None:
        fixed.append(True)

    monkeypatch.setattr("laura.render.sync.probe", fake_probe)
    monkeypatch.setattr("laura.render.sync.fix_media_duration", fake_fix)

    with pytest.raises(MediaSyncError, match="video frame drift"):
        assert_or_fix_media_sync(
            tmp_path / "out.mp4",
            expected_frames=30,
            rate_num=30,
            rate_den=1,
            tolerance_frames=1,
            require_video=True,
            fix=False,
        )
    assert fixed == []
