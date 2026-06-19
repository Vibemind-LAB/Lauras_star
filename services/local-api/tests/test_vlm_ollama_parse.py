"""Plan C / Task C1 — OllamaVlmBackend._parse_verdict (pure; no Ollama needed)."""

from __future__ import annotations

from laura.analysis.vlm_ollama import _parse_verdict


def test_parse_wellformed() -> None:
    obj = {
        "smoothness": 0.3,
        "label": "jump_cut",
        "reason": "x",
        "suggested_fix": {
            "kind": "transition",
            "transition_style": "crossfade",
            "transition_frames": 6,
        },
    }
    v = _parse_verdict(obj)
    assert v.smoothness == 0.3 and v.label == "jump_cut" and v.reason == "x"
    assert v.suggested_fix.kind == "transition"
    assert (
        v.suggested_fix.transition_style == "crossfade" and v.suggested_fix.transition_frames == 6
    )


def test_parse_clamps_smoothness() -> None:
    hi = _parse_verdict({"smoothness": 1.5, "label": "smooth", "suggested_fix": {"kind": "none"}})
    lo = _parse_verdict({"smoothness": -0.2, "label": "smooth", "suggested_fix": {"kind": "none"}})
    assert hi.smoothness == 1.0 and lo.smoothness == 0.0


def test_parse_unknown_label_coerced_to_smooth() -> None:
    assert _parse_verdict({"label": "weird", "suggested_fix": {"kind": "none"}}).label == "smooth"


def test_parse_unknown_fix_kind_coerced_to_none() -> None:
    v = _parse_verdict({"label": "smooth", "suggested_fix": {"kind": "bogus"}})
    assert v.suggested_fix.kind == "none"


def test_parse_unknown_transition_style_defaults_crossfade() -> None:
    v = _parse_verdict(
        {"label": "jump_cut", "suggested_fix": {"kind": "transition", "transition_style": "weird"}}
    )
    assert v.suggested_fix.transition_style == "crossfade"


def test_parse_missing_fields_defaults() -> None:
    v = _parse_verdict({})
    assert 0.0 <= v.smoothness <= 1.0
    assert v.label == "smooth" and v.suggested_fix.kind == "none" and v.reason == ""


def test_parse_non_numeric_smoothness_defaults() -> None:
    v = _parse_verdict({"smoothness": "lots", "label": "smooth", "suggested_fix": {"kind": "none"}})
    assert 0.0 <= v.smoothness <= 1.0
