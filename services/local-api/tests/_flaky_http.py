"""A tiny threaded HTTP server for download tests.

Supports HTTP ``Range`` requests and can cut the connection after a fixed number of
bytes on the *first* full-file response, so a test can prove that a resumed download
completes. Not a fixture — a context manager that yields the URL.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


@contextmanager
def serve(content: bytes, *, cut_after: int | None = None) -> Iterator[str]:
    """Serve ``content`` at the yielded URL.

    If ``cut_after`` is set, the first request that starts at offset 0 writes only
    ``cut_after`` bytes and then drops the connection (simulating a flaky link). Any
    later request — including the Range request a resume sends — is served in full.
    """
    state = {"cut_used": False}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: object) -> None:  # silence test noise
            pass

        def do_GET(self) -> None:  # noqa: N802 - stdlib naming
            rng = self.headers.get("Range")
            start = 0
            if rng and rng.startswith("bytes="):
                start = int(rng.split("=", 1)[1].split("-", 1)[0])
            body = content[start:]
            do_cut = cut_after is not None and start == 0 and not state["cut_used"]

            if start > 0:
                self.send_response(206)
                self.send_header(
                    "Content-Range", f"bytes {start}-{len(content) - 1}/{len(content)}"
                )
            else:
                self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()

            if do_cut:
                self.wfile.write(body[: cut_after or 0])
                state["cut_used"] = True
                self.close_connection = True  # short read -> client errors out
                return
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        host, port = server.server_address
        yield f"http://{host}:{port}/file"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
