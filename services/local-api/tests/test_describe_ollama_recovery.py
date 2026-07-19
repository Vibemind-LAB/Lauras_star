"""Local VLM 500s: recover once via a fresh load, and never overflow the context.

Two runs (cb068b06, 4a8624e2) degraded every scene review on Ollama HTTP 500s. The first
diagnosis — "an instance loaded by a text request cannot serve images" — was WRONG, and the
unload-retry built on it did not save the second run. Capturing the 500 body found the truth:

    GGML_ASSERT(a->ne[2] * 4 == b->ne[0]) failed

Three frames plus the review prompt overflow num_ctx 8192, and Ollama's vision path does not
truncate an overflowing context — it crashes the runner. Reproduced deterministically: same
frames + short prompt fine, + the 936-char review prompt crash, num_ctx 16384 fine. (The
earlier short-prompt repros "worked" only because they fit — they proved nothing about the
instance.)

The unload-retry stays: the GGML assert genuinely kills the runner, so the request AFTER a
crash can hit a half-restarted server, and one forced fresh load rides that out. Transport
errors and 4xx stay final — retrying a request the server rejects only doubles the damage.
"""

from __future__ import annotations

import io
import urllib.error
from typing import Any

import pytest

from laura.short_creator import describe as describe_mod
from laura.short_creator.describe import OllamaDescribeBackend


def _http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("http://x", code, "boom", hdrs=None, fp=io.BytesIO(b""))


class _Recorder:
    """Scripted _http_json: raise/return per call, record every request."""

    def __init__(self, script: list[Any]) -> None:
        self.script = list(script)
        self.calls: list[dict[str, Any] | None] = []

    def __call__(self, url: str, payload: dict[str, Any] | None = None, **_: Any) -> Any:
        self.calls.append(payload)
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step


def test_a_500_unloads_the_model_and_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """The run-killer: broken resident instance -> unload -> fresh load answers."""
    recorder = _Recorder(
        [
            _http_error(500),
            {"done": True},  # the unload call's reply (ignored)
            {"message": {"content": "a graph view"}},
        ]
    )
    monkeypatch.setattr(describe_mod, "_http_json", recorder)
    backend = OllamaDescribeBackend(model="qwen2.5vl:7b")

    out = backend.describe([b"jpg"], "what is on screen?")

    assert out == "a graph view"
    unload = recorder.calls[1]
    assert unload is not None and unload["keep_alive"] == 0, "the cure is the fresh load"
    assert unload["model"] == "qwen2.5vl:7b"


def test_a_second_500_is_final(monkeypatch: pytest.MonkeyPatch) -> None:
    """One recovery, not a loop — if the fresh load is also broken, degrade honestly."""
    recorder = _Recorder([_http_error(500), {"done": True}, _http_error(500)])
    monkeypatch.setattr(describe_mod, "_http_json", recorder)
    backend = OllamaDescribeBackend(model="m")

    assert backend.describe([b"jpg"], "p") == ""
    assert len(recorder.calls) == 3


def test_a_400_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """A request the server rejects as malformed does not get better on a fresh load."""
    recorder = _Recorder([_http_error(400)])
    monkeypatch.setattr(describe_mod, "_http_json", recorder)
    backend = OllamaDescribeBackend(model="m")

    assert backend.describe([b"jpg"], "p") == ""
    assert len(recorder.calls) == 1


def test_a_transport_error_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server unreachable: there is no instance to unload, and waiting is the agent's job."""
    recorder = _Recorder([urllib.error.URLError("refused")])
    monkeypatch.setattr(describe_mod, "_http_json", recorder)
    backend = OllamaDescribeBackend(model="m")

    assert backend.describe([b"jpg"], "p") == ""
    assert len(recorder.calls) == 1


def test_a_healthy_first_answer_makes_no_extra_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder = _Recorder([{"message": {"content": "fine"}}])
    monkeypatch.setattr(describe_mod, "_http_json", recorder)
    backend = OllamaDescribeBackend(model="m")

    assert backend.describe([b"jpg"], "p") == "fine"
    assert len(recorder.calls) == 1


def test_a_failing_unload_does_not_mask_the_recovery_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If even the unload call errors, still try the retry — it can only help."""
    recorder = _Recorder(
        [
            _http_error(500),
            urllib.error.URLError("unload refused"),
            {"message": {"content": "recovered"}},
        ]
    )
    monkeypatch.setattr(describe_mod, "_http_json", recorder)
    backend = OllamaDescribeBackend(model="m")

    assert backend.describe([b"jpg"], "p") == "recovered"


def test_the_context_window_holds_three_frames_plus_the_review_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Pins the num_ctx floor with its reason.

    8192 overflowed on the real workload (three frames + the review prompt), and Ollama's
    vision path does not truncate an overflowing context — it crashes the runner with a GGML
    shape assert and answers 500. Two whole runs degraded every single review on this. If a
    future edit lowers the window again, this is the test that says why it must not.
    """
    recorder = _Recorder([{"message": {"content": "ok"}}])
    monkeypatch.setattr(describe_mod, "_http_json", recorder)
    OllamaDescribeBackend(model="m").describe([b"jpg"] * 3, "p" * 1000)

    payload = recorder.calls[0]
    assert payload is not None
    assert payload["options"]["num_ctx"] >= 16384


def test_concurrent_describes_are_serialized(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 12GB local server serves one vision request at a time — pretending otherwise
    starves every caller.

    Live finding (run 85f0f884): autogen executes batched tool calls on a thread pool, so five
    review_scene calls hit Ollama CONCURRENTLY. Not one describe error was logged — the
    parallel load made ``available()``'s tags probe time out, and five of six scenes silently
    degraded before a single frame was ever sent. The whole downstream chain then built on
    default windows.
    """
    import threading

    active = 0
    overlapped: list[bool] = []
    lock = threading.Lock()

    def slow_http(url: str, payload: dict[str, Any] | None = None, **_: Any) -> Any:
        nonlocal active
        with lock:
            active += 1
            overlapped.append(active > 1)
        try:
            import time

            time.sleep(0.05)
            return {"message": {"content": "seen"}}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(describe_mod, "_http_json", slow_http)
    backend = OllamaDescribeBackend(model="m")

    threads = [
        threading.Thread(target=lambda: backend.describe([b"jpg"], "p")) for _ in range(5)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not any(overlapped), "two describes ran at once — the local server cannot do that"


def test_available_shares_the_same_serialization(monkeypatch: pytest.MonkeyPatch) -> None:
    """available() probing WHILE another thread's describe loads the model is exactly the
    timeout that silently degraded five scenes — it must wait its turn instead."""
    import threading

    active = 0
    overlapped: list[bool] = []
    lock = threading.Lock()

    def slow_http(url: str, payload: dict[str, Any] | None = None, **_: Any) -> Any:
        nonlocal active
        with lock:
            active += 1
            overlapped.append(active > 1)
        try:
            import time

            time.sleep(0.05)
            if url.endswith("/api/tags"):
                return {"models": [{"name": "m"}]}
            return {"message": {"content": "seen"}}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(describe_mod, "_http_json", slow_http)
    backend = OllamaDescribeBackend(model="m")

    describer = threading.Thread(target=lambda: backend.describe([b"jpg"], "p"))
    prober = threading.Thread(target=backend.available)
    describer.start()
    prober.start()
    describer.join()
    prober.join()

    assert not any(overlapped)
