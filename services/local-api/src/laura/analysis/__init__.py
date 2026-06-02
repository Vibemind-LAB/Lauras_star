"""AI analysis stack: shot detection, ASR, alignment, diarization.

Heavy ML components (faster-whisper, WhisperX, pyannote) are OPTIONAL extras — each
module lazily imports its dependency and exposes an ``*_available()`` probe so the
orchestrator can degrade gracefully when an extra is not installed (docs/08-components.md).
"""
