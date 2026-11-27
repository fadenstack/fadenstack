"""The vLLM worker's macOS sandbox (``sandbox-exec``).

The worker runs as the agent's own account, so without a sandbox a hostile
model or a vLLM bug could read the agent's credential (``state.json``), its
env file and the model-TLS key. The profile allows everything Metal and MLX
need (``allow default``) and then takes away, by path:

- reading or writing anything in the agent's state directory and the
  Fadenstack library (``/Library/Fadenstack``: builds, env file), except
  the runtime and the model store (read) and the worker's own home and tmp
  (read/write); metadata stays visible so path lookups work;
- writing anywhere but its home, its tmp, the per-user cache under
  ``/private/var/folders`` (Metal's shader cache) and ``/dev``.

The log is a file descriptor the agent opened, so it needs no path rule.
"""

from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path

SANDBOX_EXEC = "/usr/bin/sandbox-exec"


def _q(path: str | os.PathLike[str]) -> str:
    text = os.path.realpath(str(path))
    if any(ch in text for ch in '"\\\n\r\x00'):
        raise ValueError(f"path not allowed in a sandbox profile: {text!r}")
    return f'"{text}"'


def profile(
    *,
    denied: Iterable[Path],
    readable: Iterable[Path],
    writable: Iterable[Path],
) -> str:
    denied, readable, writable = list(denied), list(readable), list(writable)
    # The last matching rule wins: general rules first, the agent's state
    # denied after them, the worker's own paths inside it allowed last.
    lines = ["(version 1)", "(allow default)"]
    lines.append("(deny file-write*)")
    lines.append('(allow file-write* (subpath "/private/var/folders") (subpath "/dev"))')
    for path in denied:
        lines.append(f"(deny file-read-data file-write* (subpath {_q(path)}))")
    for path in readable:
        lines.append(f"(allow file-read-data (subpath {_q(path)}))")
    for path in writable:
        lines.append(f"(allow file-read-data file-write* (subpath {_q(path)}))")
    return "\n".join(lines) + "\n"


def available() -> bool:
    return os.access(SANDBOX_EXEC, os.X_OK)


def wrap(argv: list[str], profile_text: str) -> list[str]:
    """``sandbox-exec`` execs the command: the pid and the arguments from ``serve`` on stay."""
    return [SANDBOX_EXEC, "-p", profile_text, *argv]
