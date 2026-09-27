"""Test for python -m faden_cli entrypoint."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_module_execution() -> None:
    """Verify `python -m faden_cli --version` succeeds."""
    src_dir = str(Path(__file__).resolve().parent.parent / "src")
    env = dict(os.environ)
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{env.get('PYTHONPATH', '')}"

    result = subprocess.run(  # noqa: S603
        [sys.executable, "-m", "faden_cli", "--version"],
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    assert result.returncode == 0
    assert "faden" in result.stdout

