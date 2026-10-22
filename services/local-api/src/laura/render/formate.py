"""Ausgabeformate fuer Reels und Reel-Bausteine (Reels Stufe 2, 22.10.2026).

Die einzige Tabelle der Groessen. ``reel`` ist das bisherige Hochformat und die Vorgabe:
fehlt ``format``, aendert sich nichts.
"""
from __future__ import annotations

from typing import Literal

Format = Literal["reel", "quadrat", "quer"]

FORMATE: dict[str, tuple[int, int]] = {
    "reel": (1080, 1920),      # 9:16 - Instagram, TikTok, Shorts
    "quadrat": (1080, 1080),   # 1:1  - LinkedIn
    "quer": (1920, 1080),      # 16:9 - Website, Praesentation
}

# Untertitel-Vorgabe je Format: (Schriftgroesse, Abstand unten) in Pixeln der Leinwand.
UNTERTITEL_VORGABEN: dict[str, tuple[int, int]] = {
    "reel": (72, 250),
    "quadrat": (64, 100),
    "quer": (54, 48),
}


def groesse(format: str | None) -> tuple[int, int]:
    """(Breite, Hoehe) des Formats; ``None`` ist ``reel``. Unbekannt -> ValueError."""
    name = format or "reel"
    if name not in FORMATE:
        raise ValueError(f"format muss eines von {', '.join(FORMATE)} sein: {format!r}")
    return FORMATE[name]
