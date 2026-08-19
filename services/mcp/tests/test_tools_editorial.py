from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from laura_mcp import tools_editorial
from laura_mcp.client import LauraError

from .conftest import make_client


def call(tool: str, handler: Any, /, **kwargs: Any) -> Any:
    client = make_client(handler)
    return tools_editorial.TOOLS[tool](client, **kwargs)


def test_get_timeline_by_id_includes_history() -> None:
    call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        if request.url.path == "/timelines/t1":
            assert request.method == "GET"
            return httpx.Response(200, json={"id": "t1", "fps": 25})
        elif request.url.path == "/timelines/t1/history":
            assert request.method == "GET"
            return httpx.Response(200, json=[{"op": "trim"}])
        raise AssertionError(f"Unexpected path: {request.url.path}")

    out = call("get_timeline", handler, timeline_id="t1")
    assert out == {"timeline": {"id": "t1", "fps": 25}, "history": [{"op": "trim"}]}
    assert call_count[0] == 2


def test_get_timeline_by_project_lists() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/projects/p1/timelines"
        return httpx.Response(200, json=[{"id": "t1"}, {"id": "t2"}])

    out = call("get_timeline", handler, project_id="p1")
    assert out == [{"id": "t1"}, {"id": "t2"}]


def test_get_timeline_rejects_both_and_neither() -> None:
    call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        raise AssertionError("no HTTP call expected")

    with pytest.raises(LauraError, match="exactly one"):
        call("get_timeline", handler, timeline_id="t1", project_id="p1")

    with pytest.raises(LauraError, match="exactly one"):
        call("get_timeline", handler)

    assert call_count[0] == 0


def test_edit_timeline_posts_operation_verbatim() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/timelines/t1/operations"
        body = json.loads(request.content)
        assert body == {"op": "trim", "clip_id": "c1", "new_in_frame": 10}
        return httpx.Response(200, json={"ok": True})

    out = call(
        "edit_timeline",
        handler,
        timeline_id="t1",
        operation={"op": "trim", "clip_id": "c1", "new_in_frame": 10},
    )
    assert out == {"ok": True}


def test_edit_timeline_rejects_unknown_op() -> None:
    call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        raise AssertionError("no HTTP call expected")

    with pytest.raises(LauraError, match="unknown op"):
        call("edit_timeline", handler, timeline_id="t1", operation={"op": "frobnicate"})

    assert call_count[0] == 0


def test_edit_scenes_split_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/timelines/t1/scenes/sc1/split"
        body = json.loads(request.content)
        assert body == {"frame": 120}
        return httpx.Response(200, json={"ok": True})

    out = call(
        "edit_scenes",
        handler,
        timeline_id="t1",
        action="split",
        args={"scene_id": "sc1", "frame": 120},
    )
    assert out == {"ok": True}


def test_edit_scenes_cut_at_frame_path() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/timelines/t1/cut-at-frame"
        body = json.loads(request.content)
        assert body == {"frame": 240}
        return httpx.Response(200, json={"ok": True})

    out = call(
        "edit_scenes", handler, timeline_id="t1", action="cut_at_frame", args={"frame": 240},
    )
    assert out == {"ok": True}


def test_edit_scenes_rename_patches_scene() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "PATCH"
        assert request.url.path == "/scenes/sc1"
        body = json.loads(request.content)
        assert body == {"name": "Intro"}
        return httpx.Response(200, json={"ok": True})

    out = call(
        "edit_scenes",
        handler,
        timeline_id="t1",
        action="rename",
        args={"scene_id": "sc1", "name": "Intro"},
    )
    assert out == {"ok": True}


def test_edit_scenes_rejects_unknown_action() -> None:
    call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        raise AssertionError("no HTTP call expected")

    with pytest.raises(LauraError, match="unknown action"):
        call("edit_scenes", handler, timeline_id="t1", action="reticulate", args={})

    assert call_count[0] == 0


def test_timeline_undo_and_redo_paths() -> None:
    call_count = [0]

    def handler(request: httpx.Request) -> httpx.Response:
        call_count[0] += 1
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body == {}
        if call_count[0] == 1:
            assert request.url.path == "/timelines/t1/undo"
        elif call_count[0] == 2:
            assert request.url.path == "/timelines/t1/redo"
        else:
            raise AssertionError(f"Unexpected call #{call_count[0]}")
        return httpx.Response(200, json={"ok": True})

    call("timeline_undo", handler, timeline_id="t1")
    call("timeline_undo", handler, timeline_id="t1", redo=True)

    assert call_count[0] == 2
