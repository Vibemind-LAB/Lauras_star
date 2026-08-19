from __future__ import annotations

import json
from typing import Any

import httpx

from laura_mcp import tools_analysis

from .conftest import make_client


def call(tool: str, handler: Any, /, **kwargs: Any) -> Any:
    client = make_client(handler)
    return tools_analysis.TOOLS[tool](client, **kwargs)


def test_analyze_asset_posts_analysis() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/assets/a1/analysis"
        body = json.loads(request.content)
        assert body == {}
        return httpx.Response(202, json={"job_id": "j1"})

    out = call("analyze_asset", handler, asset_id="a1")
    assert out["job_id"] == "j1"


def test_get_transcript_plain() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/assets/a1/transcript"
        return httpx.Response(200, json={
            "segments": [
                {"start_frame": 0, "end_frame": 100, "text": "hello"},
                {"start_frame": 100, "end_frame": 200, "text": "world"},
            ]
        })

    out = call("get_transcript", handler, asset_id="a1")
    assert out["segments"] == [
        {"start_frame": 0, "end_frame": 100, "text": "hello"},
        {"start_frame": 100, "end_frame": 200, "text": "world"},
    ]


def test_get_transcript_filters_frame_range() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/assets/a1/transcript"
        return httpx.Response(200, json={
            "segments": [
                {"start_frame": 0, "end_frame": 100, "text": "before"},
                {"start_frame": 100, "end_frame": 200, "text": "middle"},
                {"start_frame": 200, "end_frame": 300, "text": "after"},
            ]
        })

    out = call("get_transcript", handler, asset_id="a1",
               start_frame=100, end_frame_exclusive=200)
    # Only the middle segment overlaps [100, 200)
    assert out["segments"] == [
        {"start_frame": 100, "end_frame": 200, "text": "middle"},
    ]


def test_get_shots_and_scenes_merges_two_gets() -> None:
    call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        if request.url.path == "/assets/a1/shots":
            return httpx.Response(200, json={"shots": [{"id": "s1"}]})
        elif request.url.path == "/projects/p1/assets/a1/rough-cut":
            return httpx.Response(200, json={"sequences": [{"id": "seq1"}]})
        raise AssertionError(f"Unexpected path: {request.url.path}")

    out = call("get_shots_and_scenes", handler, project_id="p1", asset_id="a1")
    assert "shots" in out
    assert "rough_cut" in out
    assert out["shots"] == {"shots": [{"id": "s1"}]}
    assert out["rough_cut"] == {"sequences": [{"id": "seq1"}]}
    assert call_count[0] == 2


def test_search_material_posts_query() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/search"
        body = json.loads(request.content)
        assert body == {
            "project_id": "p1",
            "query": "pricing",
            "mode": "semantic",
            "limit": 10,
        }
        return httpx.Response(200, json=[
            {
                "segment_id": "seg1",
                "asset_id": "a1",
                "asset_name": "video1",
                "start_frame": 10,
                "end_frame": 20,
                "text": "pricing",
                "speaker_label": "unknown",
                "score": 0.95,
            }
        ])

    out = call("search_material", handler, project_id="p1", query="pricing")
    assert len(out) == 1
    assert out[0]["text"] == "pricing"
    assert out[0]["score"] == 0.95


def test_get_transcript_bare_list_response() -> None:
    segments = [
        {"start_frame": 0, "end_frame": 100, "text": "before"},
        {"start_frame": 100, "end_frame": 200, "text": "middle"},
        {"start_frame": 200, "end_frame": 300, "text": "after"},
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/assets/a1/transcript"
        return httpx.Response(200, json=segments)

    # Test without frame args: full list returned
    out = call("get_transcript", handler, asset_id="a1")
    assert out == segments

    # Test with frame args: filtering applied to bare list
    out_filtered = call("get_transcript", handler, asset_id="a1",
                       start_frame=100, end_frame_exclusive=200)
    assert out_filtered == [
        {"start_frame": 100, "end_frame": 200, "text": "middle"},
    ]
