"""FR2: detect which perspective a file belongs to, from where it lives.

This is a hint, not a verdict — the CLI cross-checks it against which
sheets the workbook actually contains (see parsers/*.detect) and prints
both, so the actor sees the detected mapping rather than having it applied
silently.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

_FOLDER_HINTS = {
    "bewonersperspectief": "bewoners",
    "expert perspectief": "expert",
    "data perspectief": "data",
}


def detect_perspective_from_path(file_path: str) -> Optional[str]:
    parts = [p.lower() for p in Path(file_path).parts]
    for part in parts:
        for hint, perspective in _FOLDER_HINTS.items():
            if hint in part:
                return perspective
    return None
