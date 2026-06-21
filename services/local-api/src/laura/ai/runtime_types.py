from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RuntimeHealth:
    state: str
    ready: bool
    message: str | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)
