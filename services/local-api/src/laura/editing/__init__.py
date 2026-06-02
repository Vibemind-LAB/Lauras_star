"""Rough-cut editing: frame-accurate, end-exclusive timeline operations.

Pure functions over a list of EditClip; the canonical OTIO state is regenerated from
the result. Transcript-first edits resolve word frame ranges into source clip ranges
(docs/03-time-model.md, ADR-0005).
"""
