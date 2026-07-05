"""Shared test fixtures / path setup.

Puts the repo `tools/` directory on sys.path so tests can import `mcu_sim` without
installing it (it is a development tool, not part of the hwbridge package).
"""

from __future__ import annotations

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(_TESTS_DIR))
_TOOLS_DIR = os.path.join(_REPO_ROOT, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
