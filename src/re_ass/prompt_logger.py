"""Numbered prompt debug logger for re-ass runs."""

from __future__ import annotations

import logging
from pathlib import Path


LOGGER = logging.getLogger(__name__)


class PromptLogger:
    """Writes numbered prompt debug files to a directory.

    Call clear() at run start to remove stale files from previous runs.
    Each write() call increments the counter and writes {N:02d}-{label}.txt.
    """

    def __init__(self, debug_dir: Path) -> None:
        self._dir = debug_dir
        self._counter = 0

    def clear(self) -> None:
        """Remove all .txt files from the debug dir and reset the counter."""
        self._counter = 0
        try:
            for f in self._dir.glob("*.txt"):
                f.unlink()
        except Exception as error:
            LOGGER.warning("Could not clear prompt debug dir %s: %s", self._dir, error)

    def write(self, label: str, system_prompt: str, user_prompt: str) -> None:
        """Write a numbered prompt file; silently skips on any I/O error."""
        self._counter += 1
        path = self._dir / f"{self._counter:02d}-{label}.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"SYSTEM PROMPT\n{system_prompt}\n\n---\n\nUSER PROMPT\n{user_prompt}",
                encoding="utf-8",
            )
            LOGGER.debug("Debug prompt written to %s", path)
        except Exception as error:
            LOGGER.warning("Could not write debug prompt %s: %s", path, error)
