"""The release feed: only a signed feed is read, and an install is told what it should be told."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from faden_cli.core import release_feed as rf

KEY = Ed25519PrivateKey.generate()
KEYS = {
    "test": base64.b64encode(
        KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
}


def release(version: str, **extra: object) -> dict:
    return {"version": version, "channel": "stable", "severity": "normal", "summary": "s", **extra}


def feed_bytes(*releases: dict, generated_at: str = "2026-10-20T10:00:00Z", **extra: object) -> bytes:
    data = {
        "schema": 1,
        "product": "fadenstack",
        "generated_at": generated_at,
        "releases": list(releases),
        **extra,
    }
    return json.dumps(data).encode()


def signed(index: bytes, key: Ed25519PrivateKey = KEY, key_id: str = "test") -> bytes:
    return json.dumps({"key_id": key_id, "signature": base64.b64encode(key.sign(index)).decode()}).encode()


def feed(*releases: dict, **extra: object) -> rf.Feed:
    index = feed_bytes(*releases, **extra)
    return rf.verify(index, signed(index), KEYS)


def test_a_feed_signed_with_a_trusted_key_is_read() -> None:
    found = feed(release("0.4.2"), release("0.4.3"))
    assert [r.version for r in found.releases] == ["0.4.3", "0.4.2"]  # newest first


def test_a_changed_feed_is_refused() -> None:
    index = feed_bytes(release("0.4.2"))
    signature = signed(index)
    with pytest.raises(rf.FeedError, match="does not match"):
        rf.verify(index.replace(b"0.4.2", b"0.4.9"), signature, KEYS)


def test_a_feed_signed_with_another_key_is_refused() -> None:
    index = feed_bytes(release("0.4.2"))
    with pytest.raises(rf.FeedError, match="does not match"):
        rf.verify(index, signed(index, Ed25519PrivateKey.generate()), KEYS)
    with pytest.raises(rf.FeedError, match="does not know"):
        rf.verify(index, signed(index, key_id="someone"), KEYS)
    with pytest.raises(rf.FeedError, match="not readable"):
        rf.verify(index, b"not json", KEYS)


def test_entries_this_version_cannot_read_are_left_out() -> None:
    found = feed(
        release("0.4.2"),
        release("not-a-version"),
        release("0.6.0", channel="nightly"),  # a channel added later
        release("0.4.3", severity="apocalyptic", new_field={"x": 1}),
    )
    assert [r.version for r in found.releases] == ["0.4.3", "0.4.2"]
    assert found.releases[0].severity == "normal"


def test_another_schema_is_refused() -> None:
    with pytest.raises(rf.FeedError, match="schema"):
        rf.parse({"schema": 2, "product": "fadenstack", "generated_at": "2026-10-20T10:00:00Z"})


def test_an_older_feed_than_one_seen_is_refused() -> None:
    found = feed(release("0.4.2"))
    rf.not_older(found, None)
    rf.not_older(found, found.generated_at)
    with pytest.raises(rf.FeedError, match="older"):
        rf.not_older(found, datetime(2026, 11, 1, tzinfo=UTC))


def test_up_to_date() -> None:
    found = rf.assess(feed(release("0.4.2")), "0.4.2")
    assert not found.update_available
    assert found.severity == "none"
    assert found.path == ()


def test_the_most_urgent_newer_release_decides_the_severity() -> None:
    """A security fix in 0.4.3 still matters to 0.4.2 when 0.5.0 itself is a normal release."""
    found = rf.assess(
        feed(release("0.4.2"), release("0.4.3", severity="security"), release("0.5.0")),
        "0.4.2",
    )
    assert found.latest is not None and found.latest.version == "0.5.0"
    assert found.severity == "security"
    assert [r.version for r in found.newer] == ["0.5.0", "0.4.3"]


def test_previews_only_on_the_preview_channel() -> None:
    releases = (release("0.4.2"), release("0.5.0rc1", channel="preview"))
    stable = rf.assess(feed(*releases), "0.4.2", "stable")
    assert not stable.update_available
    preview = rf.assess(feed(*releases), "0.4.2", "preview")
    assert preview.latest is not None and preview.latest.version == "0.5.0rc1"
    # A pre-release marked stable by mistake is still not offered on stable.
    assert not rf.assess(feed(release("0.4.2"), release("0.5.0rc1")), "0.4.2").update_available


def test_a_release_that_cannot_be_skipped_comes_first() -> None:
    found = rf.assess(
        feed(release("0.4.1"), release("0.5.0"), release("0.5.1"), release("0.6.0", upgrade_from="0.5.0")),
        "0.4.1",
    )
    assert found.path == ("0.5.1", "0.6.0")
    assert found.target is not None and found.target.version == "0.5.1"


def test_required_first_names_the_stepping_stone() -> None:
    found = feed(release("0.5.0"), release("0.5.1"), release("0.6.0", upgrade_from="0.5.0"))
    assert rf.required_first(found, "0.4.1", "0.6.0") == "0.5.1"
    assert rf.required_first(found, "0.5.0", "0.6.0") is None
    assert rf.required_first(found, "0.4.1", "0.7.0") is None  # not in the feed: nothing to say


def test_with_no_way_in_the_feed_the_release_it_upgrades_from_comes_first() -> None:
    # 0.4.2 and 0.4.3 both upgrade from 0.4.0, which the feed does not list. It said
    # "0.0.1 cannot upgrade straight to 0.4.3: 0.4.3 has to come first."
    found = feed(release("0.4.2", upgrade_from="0.4.0"), release("0.4.3", upgrade_from="0.4.0"))
    assert rf.required_first(found, "0.0.1", "0.4.3") == "0.4.0"


def test_a_series_past_its_support_is_unsupported() -> None:
    found = feed(release("0.4.2"), series=[{"series": "0.4", "supported_until": "2027-04-30"}])
    assert not rf.assess(found, "0.4.2", today=date(2027, 4, 30)).unsupported
    late = rf.assess(found, "0.4.2", today=date(2027, 5, 1))
    assert late.unsupported and late.supported_until == date(2027, 4, 30)


def test_no_release_version_is_told_nothing() -> None:
    found = rf.assess(feed(release("0.4.2")), "main")
    assert not found.update_available and found.severity == "none"


def test_versions_compare_as_releases() -> None:
    assert rf.is_newer("0.10.0", "0.9.0")
    assert rf.is_newer("0.5.0", "0.5.0rc1")
    assert not rf.is_newer("0.5.0rc1", "0.5.0")
    assert not rf.is_newer("main", "0.4.2")


@pytest.mark.parametrize(
    ("installer", "latest", "expected"),
    [
        ("pipx", True, "pipx upgrade fadenstack"),
        ("pipx", False, "pipx install --force fadenstack==0.5.1"),
        (None, True, "pipx upgrade fadenstack"),
        ("uv", True, "uv tool upgrade fadenstack"),
        ("pip", False, "pip install fadenstack==0.5.1"),
        ("source", True, "git pull"),
    ],
)
def test_the_install_command_matches_how_the_cli_was_installed(
    installer: str | None, latest: bool, expected: str
) -> None:
    assert rf.cli_install_command(installer, "0.5.1", latest=latest) == expected


def test_a_preview_is_always_named_exactly() -> None:
    command = rf.cli_install_command("pipx", "0.6.0rc1", latest=True)
    assert command == "pipx install --force fadenstack==0.6.0rc1"


def test_upgrade_commands_take_the_next_step() -> None:
    found = rf.assess(
        feed(release("0.5.0"), release("0.5.1"), release("0.6.0", upgrade_from="0.5.0")),
        "0.4.1",
    )
    assert rf.upgrade_commands("pipx", found) == [
        "pipx install --force fadenstack==0.5.1",
        "faden upgrade --dry-run",
        "faden upgrade -y",
    ]


def test_the_backend_carries_the_same_module() -> None:
    """The backend and the CLI read the feed with the same code."""
    backend = Path(__file__).resolve().parents[2] / "backend/faden_backend/services/updates/release_feed.py"
    if not backend.exists():
        pytest.skip("not a checkout of the whole repository")
    assert backend.read_bytes() == Path(rf.__file__).read_bytes()
