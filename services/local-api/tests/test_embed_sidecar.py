"""Tests for the visual-embedding sidecar adapter (``SidecarImageEmbedder``).

A threaded stub ``/embed`` worker stands in for the GPU container; the adapter is exercised
without fastembed or a real model. Frames + vectors travel as ``.npy`` blobs.
"""

from __future__ import annotations

import io
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import numpy as np
import pytest

from laura.analysis.sidecar import SidecarImageEmbedder
from laura.analysis.visual_embed import Embedder


class _EmbedHandler(BaseHTTPRequestHandler):
    vectors: np.ndarray = np.zeros((0, 512), dtype=np.float32)

    def log_message(self, *args: Any) -> None:  # silence
        pass

    def do_POST(self) -> None:  # noqa: N802
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if not self.path.startswith("/embed"):
            self.send_response(404)
            self.end_headers()
            return
        np.load(io.BytesIO(body))  # parse the (N,H,W,3) stack the adapter sent
        out = io.BytesIO()
        np.save(out, np.asarray(self.vectors, dtype=np.float32))
        data = out.getvalue()
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def embed_worker():
    handler = type("H", (_EmbedHandler,), {})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", handler
    finally:
        server.shutdown()
        thread.join(timeout=2)


def test_embed_frames_roundtrip(embed_worker):
    url, handler = embed_worker
    handler.vectors = np.array([[1.0, 2.0, 3.0], [4.0, 5.0, 6.0]], dtype=np.float32)
    emb = SidecarImageEmbedder(url, dims=3)
    frames = [np.zeros((4, 4, 3), dtype=np.uint8), np.ones((4, 4, 3), dtype=np.uint8)]
    out = emb.embed_frames(frames)
    assert out.shape == (2, 3)
    assert out.dtype == np.float32
    np.testing.assert_allclose(out, handler.vectors)
    assert emb.dims == 3  # learned from the response


def test_embed_empty_frames_makes_no_request(embed_worker):
    url, _ = embed_worker
    emb = SidecarImageEmbedder(url, dims=7)
    out = emb.embed_frames([])  # short-circuits before any HTTP
    assert out.shape == (0, 7)
    assert out.dtype == np.float32


def test_satisfies_embedder_protocol(embed_worker):
    url, _ = embed_worker
    emb = SidecarImageEmbedder(url)
    assert isinstance(emb, Embedder)  # name + dims + embed_frames present (runtime_checkable)
