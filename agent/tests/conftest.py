"""Shared test setup.

The suite describes the Linux and Windows agent wherever it runs. A test of
the Mac service asks for it (``agent_main._IS_DARWIN = True``) and drives
``darwin_service`` with a stand-in runner, so no test touches a real account
or launchd job.
"""

from __future__ import annotations

import pytest

from faden_agent import __main__ as agent_main


@pytest.fixture(autouse=True)
def _not_a_mac_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent_main, "_IS_DARWIN", False)
