"""Lightweight .env loading in Settings.load (stdlib, no python-dotenv).

Real environment variables always win; the file only fills gaps. Searched from
the working directory upward so the repo-root .env covers dev runs started in
services/local-api (the desktop spawner's cwd).
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from laura import config


def test_load_dotenv_sets_missing_and_keeps_existing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / ".env").write_text(
        "# comment\n"
        "LAURA_TEST_NEW=from-file\n"
        'LAURA_TEST_QUOTED="with spaces"\n'
        "LAURA_TEST_EXISTING=file-value\n"
        "\n"
        "not-a-valid-line\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("LAURA_TEST_NEW", raising=False)
    monkeypatch.delenv("LAURA_TEST_QUOTED", raising=False)
    monkeypatch.setenv("LAURA_TEST_EXISTING", "real-env-wins")

    config._load_dotenv(tmp_path)

    assert os.environ["LAURA_TEST_NEW"] == "from-file"
    assert os.environ["LAURA_TEST_QUOTED"] == "with spaces"
    assert os.environ["LAURA_TEST_EXISTING"] == "real-env-wins"
    for key in ("LAURA_TEST_NEW", "LAURA_TEST_QUOTED"):
        monkeypatch.delenv(key, raising=False)


def test_load_dotenv_searches_upward(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / ".env").write_text("LAURA_TEST_UP=found\n", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    monkeypatch.delenv("LAURA_TEST_UP", raising=False)

    config._load_dotenv(nested)

    assert os.environ["LAURA_TEST_UP"] == "found"
    monkeypatch.delenv("LAURA_TEST_UP", raising=False)


def test_load_dotenv_missing_file_is_noop(tmp_path: Path) -> None:
    config._load_dotenv(tmp_path)  # no .env anywhere under tmp — must not raise
