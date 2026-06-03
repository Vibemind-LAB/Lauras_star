"""A tiny threaded HTTP server for download tests.

Supports HTTP ``Range`` requests and can cut the connection after a fixed number of
bytes on the *first* full-file response, so a test can prove that a resumed download
completes. Not a fixture — a context manager that yields the URL.
"""

from __future__ import annotations

import socket
import struct
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

# Small chunk size used for chunked-transfer cut responses so httpx can yield
# partial data before the RST arrives.  Must be well below the httpx iter_raw()
# buffer so at least one chunk is delivered.
_CUT_CHUNK = 1024


@contextmanager
def serve(
    content: bytes,
    *,
    cut_after: int | None = None,
    ignore_range: bool = False,
) -> Iterator[str]:
    """Serve ``content`` at the yielded URL.

    If ``cut_after`` is set, the first request that starts at offset 0 sends only
    ``cut_after`` bytes using chunked transfer encoding, then forces a TCP RST so
    the client receives the partial data *and* an error.  Any later request —
    including the Range request a resume sends — is served in full.

    If ``ignore_range`` is True, any ``Range`` header in the request is ignored:
    the server always responds ``200`` with the full body and full
    ``Content-Length``, simulating a server that has no range-request support.
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

            if ignore_range and rng:
                # Behave as a server with no range support: ignore the offset
                # and send the full body with status 200.
                start = 0

            body = content[start:]
            do_cut = cut_after is not None and start == 0 and not state["cut_used"]

            if do_cut:
                state["cut_used"] = True
                # Chunked TE: send small complete chunks so httpx yields them,
                # then RST (SO_LINGER l_linger=0) to make iter_raw raise ReadError.
                self.send_response(200)
                self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                limit = min(cut_after or 0, len(body))
                sent = 0
                while sent < limit:
                    n = min(_CUT_CHUNK, limit - sent)
                    piece = body[sent : sent + n]
                    self.wfile.write(f"{len(piece):x}\r\n".encode())
                    self.wfile.write(piece)
                    self.wfile.write(b"\r\n")
                    sent += n
                self.wfile.flush()
                # Force RST (not graceful FIN) so httpx raises rather than
                # treating EOF as a valid end-of-chunked-body.
                self.connection.setsockopt(
                    socket.SOL_SOCKET,
                    socket.SO_LINGER,
                    struct.pack("HH", 1, 0),  # Winsock linger: two u_short fields
                )
                self.connection.close()
                self.close_connection = True
                return

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
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        addr = server.server_address
        host = str(addr[0])
        port = int(addr[1])
        yield f"http://{host}:{port}/file"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
