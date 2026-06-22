import sys
import types
from typing import Any

import pytest

import laura.analysis.transnet as tn


def _install_fake_pkg(monkeypatch: pytest.MonkeyPatch, model_cls: Any) -> None:
    mod = types.ModuleType("transnetv2_pytorch")
    mod.TransNetV2 = model_cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "transnetv2_pytorch", mod)


def test_load_model_moves_to_cuda_when_available(monkeypatch: pytest.MonkeyPatch) -> None:
    moved: dict[str, Any] = {}

    class FakeModel:
        def eval(self) -> "FakeModel":
            return self

        def to(self, dev: str) -> "FakeModel":
            moved["dev"] = dev
            return self

    _install_fake_pkg(monkeypatch, FakeModel)
    monkeypatch.setattr(tn, "torch_cuda_available", lambda: True)

    tn._load_model()
    assert moved.get("dev") == "cuda"


def test_load_model_stays_cpu_without_cuda(monkeypatch: pytest.MonkeyPatch) -> None:
    moved: dict[str, Any] = {}

    class FakeModel:
        def eval(self) -> "FakeModel":
            return self

        def to(self, dev: str) -> "FakeModel":
            moved["dev"] = dev
            return self

    _install_fake_pkg(monkeypatch, FakeModel)
    monkeypatch.setattr(tn, "torch_cuda_available", lambda: False)

    tn._load_model()
    assert "dev" not in moved
