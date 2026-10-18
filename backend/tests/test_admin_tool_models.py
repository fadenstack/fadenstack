"""The CLI's backend tool writes audit events in a process of its own: every model it needs is loaded there.

On the release server the tool imported the audit model alone; an event's actor_id names the user table, so
every write stopped at NoReferencedTableError and none of the CLI's events (backup, upgrade, seal) was
recorded, nor the chat retention stored. The test suite imports every model, so only a fresh process shows it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = """
from faden_backend.tools import admin
admin._factory()
from faden_backend.db.meta import meta
for fk in meta.tables["audit_events"].foreign_keys:
    fk.column  # raises NoReferencedTableError when the user table's model is not loaded
print("ok")
"""


def test_the_tool_resolves_the_audit_trails_foreign_keys_in_a_fresh_process() -> None:
    backend = Path(__file__).resolve().parents[1]  # where `python -m faden_backend...` runs from
    proc = subprocess.run(
        [sys.executable, "-c", SCRIPT], capture_output=True, text=True, check=False, timeout=120, cwd=backend,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    assert proc.stdout.strip().endswith("ok")
