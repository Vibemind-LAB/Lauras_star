"""Object storage: how finished material becomes reachable from other machines.

Laura addresses everything by an absolute path under ``workspace_root``. That is
right for the editor -- ffmpeg wants local files and frame-accurate work does not
survive a network hop -- but it is useless to anyone else. Marketing and sales-claw
run elsewhere; they were handed a catalogue that told them a video EXISTS and no way
to fetch it, so somebody copied files by hand.

This module puts a copy of the bytes into a bucket and hands back a key that means
the same thing on every machine. The local file stays exactly where it was: the copy
is an addition, never a replacement.

Deliberately stdlib-only (``urllib``), like ``ai/voiceover_backend.py`` -- no new
dependency for a feature that is off by default.

Three rules, each pinned by a test in ``tests/test_object_storage.py``:

* **Off unless configured.** ``build_store`` returns ``None`` without BOTH url and
  key. A desktop install and the whole test suite never reach for a network.
* **A failed upload must not fail the render.** ``publish`` swallows and logs.
  The video on disk is the valuable artefact; the bucket copy is a convenience,
  and losing an hour of rendering to a full bucket is the wrong trade.
* **Uploads stream.** The open file object is handed to ``urlopen`` with an explicit
  Content-Length, so a 5 GB render is never read into memory.
"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .config import Settings

_log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 900.0
"""Fifteen minutes. A multi-gigabyte render over a slow link is normal here; the
timeout guards against a hung server, not against a big file."""


@runtime_checkable
class ObjectStore(Protocol):
    """What the render and import paths need. Small on purpose -- a fake is three lines."""

    bucket: str

    def put(self, key: str, path: Path, *, content_type: str) -> str:
        """Upload ``path`` under ``key``; return the bucket-qualified key."""


class SupabaseStorage:
    """Supabase Storage over its HTTP API.

    ``POST /object/{bucket}/{key}`` creates, ``PUT`` overwrites. A re-render writes
    the same key, so a 409 is retried once as an update rather than reported as an
    error -- overwriting is what the caller meant.
    """

    def __init__(
        self,
        base_url: str,
        key: str,
        bucket: str,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._key = key
        self.bucket = bucket
        self.timeout = timeout

    def _request(self, method: str, key: str, path: Path, content_type: str) -> None:
        ziel = f"{self.base_url}/object/{self.bucket}/{key}"
        with path.open("rb") as strom:
            request = Request(  # noqa: S310 - fixed scheme, url comes from settings
                ziel,
                data=strom,
                method=method,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "Content-Type": content_type,
                    # Required: without it urllib would fall back to chunked encoding,
                    # which storage-api rejects.
                    "Content-Length": str(path.stat().st_size),
                },
            )
            with urlopen(request, timeout=self.timeout):  # noqa: S310
                pass

    def put(self, key: str, path: Path, *, content_type: str) -> str:
        try:
            self._request("POST", key, path, content_type)
        except HTTPError as fehler:
            if fehler.code != 409:
                raise
            # Already there: the same export rendered again. Overwrite.
            self._request("PUT", key, path, content_type)
        return f"{self.bucket}/{key}"


def build_store(settings: Settings) -> SupabaseStorage | None:
    """The configured store, or ``None`` when object storage is switched off.

    Half a configuration counts as off, not as a reason to try: a url without a key
    would produce a confusing 401 per render instead of a clear "not configured".
    """
    if not settings.storage_enabled:
        return None
    assert settings.storage_url and settings.storage_key  # narrowed by storage_enabled
    return SupabaseStorage(settings.storage_url, settings.storage_key, settings.storage_bucket)


def store_from_env() -> SupabaseStorage | None:
    """The store a job handler should use, or ``None`` when storage is off.

    Job handlers get a `JobContext` that carries the database but no settings, and the
    backends in this codebase read their configuration from the environment for the
    same reason (see `ai/voiceover_backend.py`). Deliberately NOT cached: a render
    takes minutes, so re-reading the environment costs nothing, and a cached value
    would survive a configuration change until the service restarts.
    """
    return build_store(Settings.load())


def guess_content_type(path: Path) -> str:
    typ, _ = mimetypes.guess_type(path.name)
    return typ or "application/octet-stream"


def publish(
    store: ObjectStore | None,
    key: str,
    path: Path,
    *,
    content_type: str | None = None,
) -> str | None:
    """Best-effort upload. Returns the bucket-qualified key, or ``None``.

    NEVER raises. Callers sit at the end of a render or an import, where the work is
    already done and persisted; a bucket that is full, unreachable or misconfigured
    must cost the convenience copy and nothing else. The failure is logged, not hidden.
    """
    if store is None:
        return None
    try:
        return store.put(key, path, content_type=content_type or guess_content_type(path))
    except Exception as fehler:  # noqa: BLE001 - see docstring: never fail the caller
        _log.warning("object storage: upload of %s failed (%s); local file kept", key, fehler)
        return None
