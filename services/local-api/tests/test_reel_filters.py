"""Tests for the pure reel video-filter chain builder."""
from __future__ import annotations

from laura.render.reel import reel_video_chain


def test_vertical_no_text_contains_crop_and_scale() -> None:
    chain = reel_video_chain(vertical=True, hook_text=None, disclosure_text=None, font="X")
    assert "crop=ih*9/16:ih" in chain
    assert "scale=1080:1920" in chain


def test_vertical_with_hook_text_escapes_and_single_drawtext() -> None:
    chain = reel_video_chain(
        vertical=True,
        hook_text="Hi: 50%",
        disclosure_text=None,
        font="X",
    )
    assert r"Hi\: 50\%" in chain
    assert chain.count("drawtext=") == 1


def test_both_texts_produce_two_drawtexts() -> None:
    chain = reel_video_chain(
        vertical=False,
        hook_text="Hook",
        disclosure_text="Disclosure",
        font="X",
    )
    assert chain.count("drawtext=") == 2


def test_no_flags_returns_empty_string() -> None:
    chain = reel_video_chain(vertical=False, hook_text=None, disclosure_text=None, font="X")
    assert chain == ""
