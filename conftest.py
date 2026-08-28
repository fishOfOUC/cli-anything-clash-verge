"""Make the harness importable when pytest is invoked from anywhere.

``cli_anything`` is a namespace package, so it has no ``__init__.py``. Inserting
this directory onto ``sys.path`` lets ``import cli_anything.clash_verge``
resolve whether pytest is started as ``pytest`` or ``python -m pytest``.
"""

import sys
from pathlib import Path

_HARNESS_ROOT = str(Path(__file__).resolve().parent)
if _HARNESS_ROOT not in sys.path:
    sys.path.insert(0, _HARNESS_ROOT)
