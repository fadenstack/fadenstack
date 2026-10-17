"""Test-wide safety net: nothing here may stop a real process.

`dev up` reclaims a workspace by finding and killing processes. Its unit
tests drive that code, and three of them mocked only some of the helpers
`_reclaim_workspace` calls — so the rest ran for real, scanned the machine
and killed the development stack the developer was running at the time.
A test run should never be able to do that. The same went for a deploy step
that ran `docker exec faden-postgres` for real and switched off logins of the
developer's database, so a real `docker exec` fails the test too.

The guard patches the primitives rather than the functions, so the tests that
exist to exercise the matching logic still call the real thing; they just
cannot reach a real process through it. A test that needs particular
behaviour overrides these with its own `monkeypatch`, which is applied after
this fixture and therefore wins.
"""

from __future__ import annotations

import pytest

from faden_cli.commands.dev import dev_up
from faden_cli.core import backend_tool, backup


@pytest.fixture(autouse=True)
def never_touch_real_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    # The CLI's events in the audit trail go through `docker exec faden-backend`: answered here, never sent.
    monkeypatch.setattr(backend_tool, "run_backend_tool", lambda *_a, **_k: {"ok": True})
    # The upgrade, deploy and rebuild steps that carry chat files out of the gateway container: none here.
    monkeypatch.setattr(backup, "keep_chat_files", lambda *_a, **_k: None)
    monkeypatch.setattr(backup, "keep_company_files", lambda *_a, **_k: None)

    # Nothing to find...
    monkeypatch.setattr(dev_up.psutil, "process_iter", lambda *_a, **_k: [])
    monkeypatch.setattr(dev_up.psutil, "net_connections", lambda *_a, **_k: [])
    # ...and no way to signal anything if something is found anyway.
    monkeypatch.setattr(dev_up.os, "kill", lambda *_a, **_k: None)

    # `dev_up.subprocess` is the shared module, so blanket-patching `run`
    # would break every legitimate caller in the suite. Intercept only the
    # commands that end processes and let the rest through.
    real_run = dev_up.subprocess.run
    lethal = ("taskkill", "pkill", "pgrep")

    def guarded_run(args, *rest, **kwargs):  # type: ignore[no-untyped-def]
        first = args[0] if isinstance(args, (list, tuple)) and args else args
        if isinstance(first, str) and any(word in first.lower() for word in lethal):
            pytest.fail(f"a test reached a real process-killing command: {args!r}")
        # Nor a command inside a running container: the developer's own stack
        # (faden-postgres, ...) has the same container names as an install.
        if isinstance(args, (list, tuple)) and isinstance(first, str) and "docker" in first.lower() and "exec" in args:
            pytest.fail(f"a test reached a real `docker exec`: {args!r}")
        # Nor one that copies out of, or creates a volume next to, the developer's own containers (the chat
        # files carry-over of an upgrade: patched below, and refused here should a test get past that).
        if isinstance(args, (list, tuple)) and isinstance(first, str) and "docker" in first.lower() and (
            "cp" in args[1:2] or list(args[1:3]) == ["volume", "create"]
        ):
            pytest.fail(f"a test reached a real `docker cp` or `docker volume create`: {args!r}")
        return real_run(args, *rest, **kwargs)

    monkeypatch.setattr(dev_up.subprocess, "run", guarded_run)
