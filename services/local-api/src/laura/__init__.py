"""Laura local service package.

Frame-/sample-accurate editorial & analysis backend. See ../../docs for the spec.
"""

__version__ = "0.1.0"

# The analysis pipeline version. Bumping this invalidates cached analysis results
# (idempotency anchor — see docs/05-workers-queue.md).
PIPELINE_VERSION = "2"
