from __future__ import annotations

from pathlib import Path

from laura.render.audio import AudioOverlay
from laura.render.mp4 import render_clips_mp4


def _filter_complex(args: list[str]) -> str:
    idx = args.index("-filter_complex")
    return args[idx + 1]


def test_render_audio_overlay_filter_includes_offset_gain_and_fades(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_ffmpeg(args: list[str]) -> None:
        calls.append(args)

    monkeypatch.setattr("laura.render.mp4.run_ffmpeg", fake_run_ffmpeg)
    monkeypatch.setattr("laura.render.mp4._source_has_audio", lambda _path: True)

    render_clips_mp4(
        [(tmp_path / "v.mp4", 0, 90)],
        tmp_path / "out.mp4",
        rate_num=30,
        rate_den=1,
        audio_overlays=[
            AudioOverlay(
                path=tmp_path / "voice.wav",
                seq_in_frame=30,
                seq_out_frame_exclusive=90,
                asset_in_frame=15,
                gain_percent=125,
                fade_in_frames=6,
                fade_out_frames=9,
                mix_mode="replace_original",
                ducking_percent=0,
            )
        ],
    )

    assert len(calls) == 1
    filt = _filter_complex(calls[0])
    assert "volume=1.25" in filt
    assert "atrim=start=0.5:duration=2" in filt
    assert "afade=t=in:st=0:d=0.2" in filt
    assert "afade=t=out:st=1.7:d=0.3" in filt
    assert "adelay=1000.000000|1000.000000" in filt
    assert "volume=enable='between(t,1,3)':volume=0.0" in filt
    assert "-map" in calls[0]
    assert "[aout]" in calls[0]


def test_render_legacy_music_tracks_still_build_audio_filter(
    tmp_path: Path,
    monkeypatch,
) -> None:
    calls: list[list[str]] = []

    def fake_run_ffmpeg(args: list[str]) -> None:
        calls.append(args)

    monkeypatch.setattr("laura.render.mp4.run_ffmpeg", fake_run_ffmpeg)

    render_clips_mp4(
        [(tmp_path / "v.mp4", 0, 30)],
        tmp_path / "out.mp4",
        rate_num=30,
        rate_den=1,
        music_tracks=[(tmp_path / "music.wav", 0, 30, 80)],
    )

    filt = _filter_complex(calls[0])
    assert "volume=0.8" in filt
    assert "atrim=start=0:duration=1" in filt
    assert "adelay=0.000000|0.000000" in filt
