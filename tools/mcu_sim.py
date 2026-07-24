#!/usr/bin/env python3
"""Compatibility shim: the simulator lives in the mcuscope package as `mcuscope.sim`.

It moved into the package so the installed daemon can run it (`mcuscoped --sim`, the
`mcu-sim` console script). This shim keeps the documented source-checkout invocation
(`python tools/mcu_sim.py ...`) and the historical `import mcu_sim` name working.
"""

from __future__ import annotations

import os
import sys

# Make `mcuscope` importable when running from a source checkout without install.
_HERE = os.path.dirname(os.path.abspath(__file__))
_HOST = os.path.join(os.path.dirname(_HERE), "host")
if _HOST not in sys.path:
    sys.path.insert(0, _HOST)

from mcuscope.sim import *  # noqa: E402,F403 - re-export the public API
from mcuscope.sim import _format_typed_sample, main  # noqa: E402,F401 - used by tests

if __name__ == "__main__":
    raise SystemExit(main())
