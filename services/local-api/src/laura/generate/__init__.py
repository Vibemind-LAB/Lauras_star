"""laura.generate — generative media (text-to-video) into the editorial asset pool.

Kept OUT of the Codex-owned ``ai/`` runtime subtree on purpose: this only enqueues a job and
registers the output as a normal synthetic asset via the standard ingest path, so it needs no
change to the AI-runtime framework. The real model backend (ComfyUI/LTX) plugs into
``backend.VideoGenerateBackend`` later; v1 ships a model-free stub.
"""
