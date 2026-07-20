"""Laura local service package.

Frame-/sample-accurate editorial & analysis backend. See ../../docs for the spec.
"""

__version__ = "0.1.0"

# The analysis pipeline version. Bumping this invalidates cached analysis results
# (idempotency anchor — see docs/05-workers-queue.md).
# 4: duplicate detection got a motion-dependent tolerance, so keep/drop decisions on
#    near-frozen footage (screen recordings) differ from a version-3 run of the same input.
PIPELINE_VERSION = "4"
