"""Test suite for Rased.

Written against stdlib unittest so the suite runs on a machine with nothing
installed beyond the runtime floor - the same air-gapped condition the service
itself is designed for. pytest collects these unchanged when it is available.
"""

import logging

# Several tests deliberately exercise paths that log a warning (a denied tool
# call, a rejected citation, an unreachable endpoint). Those are expected
# outcomes here, so the handler is silenced to keep failures legible - without
# it, a passing run prints warnings that read like errors.
logging.getLogger().addHandler(logging.NullHandler())
logging.getLogger().setLevel(logging.CRITICAL)
