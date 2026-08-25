from __future__ import annotations

import tomllib
from pathlib import Path


def test_numpy_is_declared_as_a_runtime_dependency() -> None:
    pyproject_path = Path(__file__).parents[1] / "pyproject.toml"
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    names = {dependency.split("[", 1)[0].split(">", 1)[0].split("=", 1)[0].lower()
             for dependency in dependencies}

    assert "numpy" in names
