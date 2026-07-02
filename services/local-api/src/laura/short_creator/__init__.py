"""NL-agent short-creator (AutoGen 0.4, Magentic-One-first).

Orchestrates Laura's existing capabilities (CLIP search, VLM, shorts scoring,
transcript, render) into a "make me a short about X" flow. AutoGen is an
OPTIONAL extra (``laura[autoshort]``); this package imports nothing from
autogen at module load, so the backend starts without it. See
``docs/superpowers/specs/2026-07-01-nl-agent-short-creator-design.md``.
"""

from __future__ import annotations
