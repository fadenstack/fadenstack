"""Allow Bypass says once that turning it on needs Root Mode.

Its description ended with "Turning it on needs Root Mode; turning it off does not." and the console adds the same
note below every protected setting that can be narrowed without it (``settings.protected_narrowing_help``): the
sentence showed twice (0.7.0rc1 on a test server, 10 November 2026). The description leaves it to the console.
"""

from __future__ import annotations

from faden_backend.services.system_settings.registry import APPROVAL_SETTINGS


def test_allow_bypass_leaves_the_root_mode_note_to_the_console() -> None:
    allow = next(s for s in APPROVAL_SETTINGS if s.key == "approvals.bypass_allowed")

    assert allow.protected and allow.narrowing_values == (False,), "the console's note applies to it"
    assert "Root Mode" not in allow.description
    assert allow.description.endswith("running Bypass sessions end within half a minute.")
