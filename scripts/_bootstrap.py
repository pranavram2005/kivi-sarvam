"""Let the scripts import `backend` when run directly from the repository root.

Every script starts with `import _bootstrap  # noqa` so that
`python scripts/whatever.py` works without installing the package or setting
PYTHONPATH - one less thing for a reviewer to get wrong.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# A model can put a non-breaking hyphen, curly quote or dash in an answer, and
# the default Windows console encoding (cp1252) cannot encode those - printing
# one raises UnicodeEncodeError and kills the run. Reviewers run this on
# Windows, so make stdout UTF-8 and never let a character break a suite.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):  # already wrapped, or not a TTY
        pass
