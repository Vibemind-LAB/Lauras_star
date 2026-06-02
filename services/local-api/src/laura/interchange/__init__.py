"""Interchange/export layer (docs/07-interchange.md, ADR-0001).

OTIO is the canonical model; EDL/SRT/VTT are deterministic, dependency-light writers.
Internal state is never EDL/XML — exports are generated from the canonical timeline.
"""
