"""The keys an install trusts for the release feed: the same in the CLI and the backend, a backup among them.

A key an install trusts only ever arrives with a release. The CLI and the backend each check the feed with their
own copy of ``TRUSTED_KEYS``; a copy that drifted would leave one of them refusing a feed the other accepts. The
backup key is what lets a lost or leaked signing key be replaced without every install upgraded by hand first.
"""

from __future__ import annotations

import ast
from pathlib import Path

from faden_cli.core import release_feed

BACKEND_FEED = Path(__file__).resolve().parents[2] / "backend" / "faden_backend" / "services" / "updates" / "release_feed.py"


def _trusted_keys(path: Path) -> dict[str, str]:
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TRUSTED_KEYS":
            return ast.literal_eval(node.value)
    raise AssertionError(f"no TRUSTED_KEYS in {path}")


def test_the_cli_and_the_backend_trust_the_same_keys() -> None:
    assert _trusted_keys(BACKEND_FEED) == release_feed.TRUSTED_KEYS


def test_a_backup_key_is_trusted_beside_the_signing_key() -> None:
    assert "fadenstack-2026-10" in release_feed.TRUSTED_KEYS
    assert any(key_id.startswith("fadenstack-backup-") for key_id in release_feed.TRUSTED_KEYS)
    assert len(set(release_feed.TRUSTED_KEYS.values())) == len(release_feed.TRUSTED_KEYS)  # distinct keys
