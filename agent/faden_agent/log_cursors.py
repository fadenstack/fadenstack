"""Where each log shipper has got to, kept across agent restarts.

Every log the agent ships already lives on disk on this machine: the journal,
Docker's log files, Ray's log files. A shipper reads one at a paced rate and
moves its cursor only once Loki has taken the lines, so the log itself is the
queue: a failed push is sent again on the next pass, a burst drains at the
pace the budget allows, and nothing waits in memory. Keeping the cursors here
means a restarted agent resumes where it stopped instead of skipping to the
end of every log.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


class LogCursors:
    """A small JSON file of cursors, one key per log a shipper follows."""

    def __init__(self, path: Path | None) -> None:
        """*path* None keeps them in memory only (tests, a node with no state directory)."""
        self._path = path
        self._values: dict[str, Any] = {}
        self._dirty = False
        if path is not None and path.is_file():
            try:
                loaded = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("Log cursors in %s could not be read; logs are followed from their end.", path)
            else:
                if isinstance(loaded, dict):
                    self._values = loaded

    @property
    def resumed(self) -> bool:
        """Whether cursors were loaded: the agent ran before and is picking up where it stopped."""
        return bool(self._values)

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def with_prefix(self, prefix: str) -> dict[str, Any]:
        return {key: value for key, value in self._values.items() if key.startswith(prefix)}

    def set(self, key: str, value: Any) -> None:
        if self._values.get(key) != value:
            self._values[key] = value
            self._dirty = True

    def replace_prefix(self, prefix: str, values: dict[str, Any]) -> None:
        """Make the keys under *prefix* exactly *values*: a shipper's whole set, stale ones gone."""
        current = self.with_prefix(prefix)
        if current == values:
            return
        for key in current:
            self._values.pop(key, None)
        self._values.update(values)
        self._dirty = True

    def save(self) -> None:
        """Write them if anything changed, atomically, readable by this user only."""
        if not self._dirty or self._path is None:
            return
        tmp = self._path.with_suffix(".tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(self._values, handle)
            tmp.replace(self._path)
            self._dirty = False
        except OSError:
            log.debug("Could not save log cursors to %s", self._path, exc_info=True)
