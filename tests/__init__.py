"""Test suite for Rased.

Written against stdlib unittest so the suite runs on a machine with nothing
installed beyond the runtime floor - the same air-gapped condition the service
itself is designed for. pytest collects these unchanged when it is available.
"""

import logging
import os
import tempfile
from pathlib import Path

# Configured before anything imports app.config, whose settings are cached for
# the life of the process.

# Point the inference endpoint at a closed local port. Tests must exercise the
# no-model path, and a closed port refuses instantly, whereas an unresolvable
# hostname costs seconds in the platform resolver before failing the same way.
os.environ.setdefault("RASED_LLM_BASE_URL", "http://127.0.0.1:9/v1")

# Keep audit writes out of the working tree: a test run must never append to
# the repository's own trail, which would leave it permanently unverifiable.
os.environ.setdefault(
    "RASED_AUDIT_LOG_PATH",
    str(Path(tempfile.mkdtemp(prefix="rased-test-audit-")) / "audit.jsonl"),
)

# Several tests deliberately exercise paths that log a warning (a denied tool
# call, a rejected citation, an unreachable endpoint). Those are expected
# outcomes here, so the handler is silenced to keep failures legible - without
# it, a passing run prints warnings that read like errors.
logging.getLogger().addHandler(logging.NullHandler())
logging.getLogger().setLevel(logging.CRITICAL)
