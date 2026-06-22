"""Tests for the pure reel video-filter chain builder.

Overlay text is referenced via drawtext ``textfile=`` (a basename), never inline
``text='…'`` — see ``reel.py`` for why. These tests assert the chain *structure*;
the real-ffmpeg edge-case coverage (apostrophes, colons, %, unicode) lives in
``test_reel_render.py`` and ``test_reel_e2e.py``.
"""
from __future__ import annotations

from laura.render.reel import reel_video_chain


def test_vertical_no_text_contains_crop_and_scale() -> None:
    chain = reel_video_chain(vertical=True, font="X")
    assert "crop=ih*9/16:ih" in chain
    assert "scale=1080:1920" in chain
    assert "drawtext=" not in chain


def test_hook_uses_textfile_basename_single_drawtext() -> None:
    chain = reel_video_chain(
        vertical=True,
        hook_textfile="out.reel_hook.txt",
        font="X",
    )
    assert "textfile=out.reel_hook.txt" in chain
    assert "text='" not in chain  # never inline text
    assert chain.count("drawtext=") == 1


def test_both_textfiles_produce_two_drawtexts() -> None:
    chain = reel_video_chain(
        vertical=False,
        hook_textfile="h.txt",
        disclosure_textfile="d.txt",
        font="X",
    )
    assert chain.count("drawtext=") == 2
    assert "textfile=h.txt" in chain
    assert "textfile=d.txt" in chain


def test_no_flags_returns_empty_string() -> None:
    chain = reel_video_chain(vertical=False, font="X")
    assert chain == ""
