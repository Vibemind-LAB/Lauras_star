"""Pluggable reenact-backend interface for Laura.

Defines the ``ReenactBackend`` Protocol, a dependency-free ``StubReenactBackend``
(renders a visually-synthetic placeholder via ffmpeg), an ``UnavailableBackend``
(used for heavy-dep backends that are not yet installed), and a resolver
``resolve_reenact_backend`` that picks the correct implementation at runtime.

No GPU, no torch, no insightface, no liveportrait — this module is always safe
to import regardless of the environment.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol, runtime_checkable

from laura.ingest.ffmpeg import run_ffmpeg
from laura.render.reel import resolve_font


@runtime_checkable
class ReenactBackend(Protocol):
    """Minimal contract that every reenact backend must satisfy."""

    name: str

    def available(self) -> bool:
        """Return True iff this backend can currently produce output."""
        ...

    def reenact(
        self,
        *,
        driving_path: Path,
        portrait_path: Path,
        out_path: Path,
        fps_num: int,
        fps_den: int,
    ) -> None:
        """Reenact a portrait with motion from *driving_path*.

        Args:
            driving_path:  Source clip whose motion/audio drives the output.
            portrait_path: Still or video portrait to animate.  The stub ignores
                           this — real backends use it.
            out_path:      Destination MP4 (will be overwritten if it exists).
            fps_num:       Output frame-rate numerator.
            fps_den:       Output frame-rate denominator.
        """
        ...


class StubReenactBackend:
    """Length-preserving synthetic placeholder — always available, never real.

    Produces an obviously-fake, desaturated MP4 with a centred yellow
    "REENACT (stub)" label so the output can never be mistaken for a genuine
    reenactment.  Useful for UI wiring and pipeline smoke-tests without any
    heavy optional dependency.

    The overlay text is written to a temporary ``*.reenact_stub.txt`` file next
    to *out_path* and passed to ffmpeg via ``textfile=<basename>`` with
    ``cwd=out_path.parent`` — the R0.8 mechanism that avoids Windows
    drive-colon escaping issues inside drawtext paths.
    """

    name: str = "stub"

    def available(self) -> bool:
        return True

    def reenact(
        self,
        *,
        driving_path: Path,
        portrait_path: Path,  # noqa: ARG002  (unused by stub by design)
        out_path: Path,
        fps_num: int,
        fps_den: int,
    ) -> None:
        label = out_path.parent / f"{out_path.stem}.reenact_stub.txt"
        label.write_text("REENACT (stub)", encoding="utf-8")
        try:
            font = resolve_font()
            vf = (
                f"eq=saturation=0.4,"
                f"drawtext=fontfile={font}:textfile={label.name}"
                f":x=(w-text_w)/2:y=h-text_h-40"
                f":fontsize=36:fontcolor=yellow:box=1:boxcolor=black@0.6"
            )
            run_ffmpeg(
                [
                    "-i", str(driving_path),
                    "-vf", vf,
                    "-r", f"{fps_num}/{fps_den}",
                    "-c:v", "libx264",
                    "-pix_fmt", "yuv420p",
                    str(out_path),
                ],
                cwd=out_path.parent,
            )
        finally:
            if label.exists():
                label.unlink()


class UnavailableBackend:
    """Sentinel for heavy-dep backends (liveportrait, etc.) that are not installed.

    Never imports GPU or model libraries.  ``available()`` always returns False;
    ``reenact()`` always raises ``RuntimeError`` with a clear message.

    Args:
        name: Canonical backend identifier (e.g. ``"liveportrait"``).
    """

    def __init__(self, name: str) -> None:
        self.name = name

    def available(self) -> bool:
        return False

    def reenact(
        self,
        *,
        driving_path: Path,  # noqa: ARG002
        portrait_path: Path,  # noqa: ARG002
        out_path: Path,  # noqa: ARG002
        fps_num: int,  # noqa: ARG002
        fps_den: int,  # noqa: ARG002
    ) -> None:
        raise RuntimeError(f"reenact backend '{self.name}' is not installed")


def resolve_reenact_backend(name: str | None = None) -> ReenactBackend:
    """Return the best available ``ReenactBackend`` for *name*.

    Resolution order:
    1. ``LAURA_REENACT_BACKEND`` environment variable.
    2. The *name* argument.
    3. ``"stub"`` (safe fallback).

    Returns:
        ``StubReenactBackend()``  for ``"stub"``.
        ``UnavailableBackend(chosen)`` for any other identifier (real backends
        are registered here once their optional extra exists).
    """
    chosen: str = os.environ.get("LAURA_REENACT_BACKEND") or name or "stub"
    if chosen == "stub":
        return StubReenactBackend()
    return UnavailableBackend(chosen)
