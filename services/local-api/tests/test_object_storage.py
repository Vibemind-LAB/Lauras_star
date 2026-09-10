"""The object store: how finished material leaves this machine.

Laura's assets are files under ``workspace_root``, addressed by an absolute path.
That path is worthless to anyone else -- marketing and sales-claw run elsewhere and
were handed a catalogue entry they could read but no video they could fetch. The
store uploads the bytes and hands back a key that is meaningful from any machine.

The rules pinned here are the ones that decide whether this is safe to switch on:

* OFF unless configured. A desktop install and this whole test suite must never
  reach for a network because a feature exists.
* A failed upload must NOT fail the render. The video on disk is the valuable
  thing; the copy in the bucket is a convenience, and losing an hour of rendering
  because a bucket was full would be the wrong trade.
* Uploads stream. A 5 GB render must not be read into memory to be sent.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from laura.config import Settings
from laura.storage import SupabaseStorage, build_store


def _settings(tmp_path: Path, **overrides: Any) -> Settings:
    basis: dict[str, Any] = {"workspace_root": tmp_path, "start_runner": False}
    basis.update(overrides)
    return Settings(**basis)


def test_storage_is_off_without_configuration(tmp_path: Path) -> None:
    settings = _settings(tmp_path)

    assert settings.storage_enabled is False
    assert build_store(settings) is None


def test_storage_needs_both_url_and_key(tmp_path: Path) -> None:
    """Half a configuration is a misconfiguration, not a reason to try."""
    nur_url = _settings(tmp_path, storage_url="http://example.invalid/storage/v1")
    nur_key = _settings(tmp_path, storage_key="k")

    assert build_store(nur_url) is None
    assert build_store(nur_key) is None


def test_store_from_env_does_not_import_a_dotenv_into_the_process(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """Reading configuration must not INSTALL configuration.

    `store_from_env` first went through `Settings.load()`, which calls `_load_dotenv()`
    and layers every `.env` from the cwd upward into `os.environ` via setdefault. Since
    it runs once per render, a full suite run imported the repo-root `.env` -- including
    LAURA_VLM_MODEL -- into the live process, and `test_stub_vlm_backend` then failed on
    a global it asserts to be unset. The failure appeared far away from its cause, which
    is exactly why this is pinned here.
    """
    from laura.storage import store_from_env

    marke = "LAURA_TEST_DOTENV_CANARY"
    monkeypatch.delenv(marke, raising=False)
    monkeypatch.delenv("LAURA_STORAGE_URL", raising=False)
    monkeypatch.delenv("LAURA_STORAGE_KEY", raising=False)
    (tmp_path / ".env").write_text(f"{marke}=vergiftet\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert store_from_env() is None

    assert marke not in os.environ, "store_from_env pulled a .env into the process"


def test_store_from_env_reads_its_own_three_variables(monkeypatch: Any) -> None:
    monkeypatch.setenv("LAURA_STORAGE_URL", "http://example.invalid/storage/v1/")
    monkeypatch.setenv("LAURA_STORAGE_KEY", "k")
    monkeypatch.setenv("LAURA_STORAGE_BUCKET", "eigener")

    from laura.storage import store_from_env

    store = store_from_env()

    assert store is not None
    assert store.bucket == "eigener"
    assert store.base_url == "http://example.invalid/storage/v1", "trailing slash trimmed"


def test_store_is_built_when_configured(tmp_path: Path) -> None:
    settings = _settings(
        tmp_path, storage_url="http://example.invalid/storage/v1", storage_key="k"
    )

    store = build_store(settings)

    assert isinstance(store, SupabaseStorage)
    assert store.bucket == "laura"


def test_put_sends_the_file_and_returns_its_key(tmp_path: Path, monkeypatch: Any) -> None:
    datei = tmp_path / "clip.mp4"
    datei.write_bytes(b"\x00\x01\x02\x03")
    gesehen: dict[str, Any] = {}

    def fake_open(request: Any, timeout: float = 0) -> Any:
        gesehen["url"] = request.full_url
        gesehen["method"] = request.get_method()
        gesehen["headers"] = {k.lower(): v for k, v in request.headers.items()}
        # `data` is the open file object, not bytes -- that is the streaming contract.
        gesehen["data_is_stream"] = hasattr(request.data, "read")
        return _FakeResponse(200)

    monkeypatch.setattr("laura.storage.urlopen", fake_open)
    store = SupabaseStorage("http://example.invalid/storage/v1", "geheim", "laura")

    key = store.put("exports/e1.mp4", datei, content_type="video/mp4")

    assert key == "laura/exports/e1.mp4"
    assert gesehen["url"] == "http://example.invalid/storage/v1/object/laura/exports/e1.mp4"
    assert gesehen["method"] == "POST"
    assert gesehen["headers"]["authorization"] == "Bearer geheim"
    assert gesehen["headers"]["content-type"] == "video/mp4"
    assert gesehen["headers"]["content-length"] == "4"
    assert gesehen["data_is_stream"] is True


def test_put_retries_as_upsert_when_the_object_exists(tmp_path: Path, monkeypatch: Any) -> None:
    """A re-render of the same export must overwrite, not fail with 409."""
    from urllib.error import HTTPError

    datei = tmp_path / "clip.mp4"
    datei.write_bytes(b"x")
    versuche: list[str] = []

    def fake_open(request: Any, timeout: float = 0) -> Any:
        versuche.append(request.get_method())
        if len(versuche) == 1:
            raise HTTPError(request.full_url, 409, "Duplicate", {}, None)  # type: ignore[arg-type]
        return _FakeResponse(200)

    monkeypatch.setattr("laura.storage.urlopen", fake_open)
    store = SupabaseStorage("http://example.invalid/storage/v1", "k", "laura")

    key = store.put("exports/e1.mp4", datei, content_type="video/mp4")

    assert key == "laura/exports/e1.mp4"
    assert versuche == ["POST", "PUT"], "409 must be retried as an update, once"


def test_publish_never_raises(tmp_path: Path, monkeypatch: Any) -> None:
    """The render is the valuable artefact; a broken bucket must not destroy it."""
    from laura.storage import publish

    datei = tmp_path / "clip.mp4"
    datei.write_bytes(b"x")

    def boom(request: Any, timeout: float = 0) -> Any:
        raise OSError("bucket on fire")

    monkeypatch.setattr("laura.storage.urlopen", boom)
    store = SupabaseStorage("http://example.invalid/storage/v1", "k", "laura")

    assert publish(store, "exports/e1.mp4", datei, content_type="video/mp4") is None


def test_publish_without_a_store_is_a_no_op(tmp_path: Path) -> None:
    from laura.storage import publish

    assert publish(None, "exports/e1.mp4", tmp_path / "missing.mp4") is None


def test_publish_returns_the_key_on_success(tmp_path: Path, monkeypatch: Any) -> None:
    from laura.storage import publish

    datei = tmp_path / "clip.mp4"
    datei.write_bytes(b"x")
    monkeypatch.setattr("laura.storage.urlopen", lambda request, timeout=0: _FakeResponse(200))
    store = SupabaseStorage("http://example.invalid/storage/v1", "k", "laura")

    assert publish(store, "exports/e1.mp4", datei, content_type="video/mp4") == (
        "laura/exports/e1.mp4"
    )


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status

    def read(self) -> bytes:
        return b"{}"

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_: object) -> None:
        return None


@pytest.mark.parametrize(
    ("pfad", "erwartet"),
    [
        ("exports/e1.mp4", "video/mp4"),
        ("assets/a1.mov", "video/quicktime"),
        # Deliberately a PREFIX, not an exact type: `mimetypes` consults the platform
        # (on Windows the registry), and .wav comes back as audio/wav here and
        # audio/x-wav elsewhere. What matters is that audio is not sent as video.
        ("assets/a1.wav", "audio/"),
        ("assets/unbekannt.xyzzy", "application/octet-stream"),
    ],
)
def test_content_type_is_guessed_from_the_name(pfad: str, erwartet: str) -> None:
    from laura.storage import guess_content_type

    assert guess_content_type(Path(pfad)).startswith(erwartet)
