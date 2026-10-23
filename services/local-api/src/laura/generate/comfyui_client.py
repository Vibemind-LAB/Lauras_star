"""Minimal ComfyUI HTTP client — submit a workflow, wait for it, download the output.

stdlib ``urllib`` only (mirrors :mod:`laura.analysis.sidecar`). Used by
:class:`~laura.generate.backend.ComfyUIVideoGenerateBackend` to run a text-to-video (LTX) workflow
on a local ComfyUI. The real round-trip is manual-to-verify (needs a running ComfyUI); tests drive
it against a fake server.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


class ComfyUIClient:
    """Talks to a ComfyUI server's ``/prompt``, ``/history/{id}`` and ``/view`` endpoints."""

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def submit(self, workflow: dict[str, Any]) -> str:
        """Queue an API-format *workflow*; return the ComfyUI ``prompt_id``."""
        body = json.dumps({"prompt": workflow}).encode("utf-8")
        req = urllib.request.Request(
            f"{self.base_url}/prompt",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI /prompt returned no prompt_id: {data}")
        return str(prompt_id)

    def _history(self, prompt_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/history/{urllib.parse.quote(prompt_id)}"
        with urllib.request.urlopen(url, timeout=self.timeout) as resp:
            data: dict[str, Any] = json.loads(resp.read().decode("utf-8"))
        return data

    def wait(
        self, prompt_id: str, *, timeout: float = 600.0, interval: float = 1.0
    ) -> dict[str, Any]:
        """Poll ``/history`` until the prompt has outputs; return the outputs dict.

        Raises :class:`TimeoutError` if it is not ready within *timeout* seconds.
        """
        deadline = time.monotonic() + timeout
        while True:
            entry = self._history(prompt_id).get(prompt_id)
            if entry and entry.get("outputs"):
                return dict(entry["outputs"])
            if time.monotonic() >= deadline:
                raise TimeoutError(f"ComfyUI prompt {prompt_id} not ready after {timeout}s")
            time.sleep(interval)

    def download(self, *, filename: str, subfolder: str, type_: str, out_path: Path) -> None:
        """Download an output file (``/view``) to *out_path*."""
        query = urllib.parse.urlencode(
            {"filename": filename, "subfolder": subfolder, "type": type_}
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(f"{self.base_url}/view?{query}", timeout=self.timeout) as resp:
            out_path.write_bytes(resp.read())
