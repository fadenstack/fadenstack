"""release/update_feed.py builds the feed installs read: what it takes, and what it refuses."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "release" / "update_feed.py"
if not SCRIPT.exists():
    pytest.skip("not a checkout of the whole repository", allow_module_level=True)

spec = importlib.util.spec_from_file_location("update_feed", SCRIPT)
assert spec is not None and spec.loader is not None
update_feed = importlib.util.module_from_spec(spec)
sys.modules["update_feed"] = update_feed
spec.loader.exec_module(update_feed)

TODAY = date(2026, 10, 20)


def entry(version: str, **extra: object) -> dict:
    return {"version": version, "severity": "normal", "summary": "Fixes.", **extra}


def test_a_release_gets_its_notes_page_highlights_and_the_build_date() -> None:
    feed = update_feed.build(
        {"releases": [entry("0.4.2", highlights=["  One  line. ", ""], components={"agent": "0.2.7"})]},
        version="0.4.2",
        agent="0.2.7",
        today=TODAY,
    )
    release = feed["releases"][0]
    assert release["notes_url"] == "https://fadenstack.com/releases/#v0.4.2"
    assert release["highlights"] == ["One line."]
    assert release["date"] == "2026-10-20"
    assert release["components"] == {"cli": "0.4.2", "agent": "0.2.7"}
    assert feed["channels"] == {"stable": "0.4.2", "preview": "0.4.2"}


def test_the_github_release_text_has_the_summary_highlights_and_notes_page() -> None:
    feed = update_feed.build(
        {"releases": [entry("0.4.2", highlights=["One line.", "Another."]), entry("0.4.1")]},
        version="0.4.2",
        agent=None,
        today=TODAY,
    )
    assert update_feed.notes(feed, "0.4.2") == (
        "Fixes.\n\n**What's new**\n\n- One line.\n- Another.\n\n"
        "Release notes: https://fadenstack.com/releases/#v0.4.2\n"
    )
    assert "What's new" not in update_feed.notes(feed, "0.4.1")
    with pytest.raises(update_feed.BuildError, match=r"no entry for 0.4.9"):
        update_feed.notes(feed, "0.4.9")


def test_a_tag_without_an_entry_is_refused() -> None:
    with pytest.raises(update_feed.BuildError, match=r"no entry for 0.4.3"):
        update_feed.build({"releases": [entry("0.4.2")]}, version="0.4.3", agent=None, today=TODAY)


def test_the_agent_the_backend_expects_must_be_the_released_one() -> None:
    with pytest.raises(update_feed.BuildError, match="AGENT_VERSION"):
        update_feed.build(
            {"releases": [entry("0.4.2", components={"agent": "0.2.6"})]},
            version="0.4.2",
            agent="0.2.7",
            today=TODAY,
        )


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (entry("0.5.0rc1"), "pre-release"),
        (entry("0.5.0", severity="urgent"), "severity"),
        (entry("0.5.0", summary=" "), "summary"),
        (entry("0.5.0", upgrade_from="soon"), "upgrade_from"),
    ],
)
def test_entries_installs_could_not_read_are_refused(bad: dict, message: str) -> None:
    with pytest.raises(update_feed.BuildError, match=message):
        update_feed.build({"releases": [bad]}, version=None, agent=None, today=TODAY)


# The app list (apps.yaml -> apps.json), signed with its own keys.

TARBALL = "https://github.com/fadenstack/fadenstack-apps/releases/download/nextcloud-v0.1.10/fadenstack_agent.tar.gz"


def app_entry(**extra: object) -> dict:
    return {
        "id": "nextcloud",
        "name": {"en": "Fadenstack for Nextcloud", "de": "Fadenstack für Nextcloud"},
        "page": "https://fadenstack.com/agents/nextcloud/",
        "connect": "nextcloud",
        "agents": {"default": "nextcloud-assistant"},
        "releases": [{
            "version": "0.1.10",
            "channel": "preview",
            "date": "2026-11-20",
            "summary": {"en": "First preview.", "de": "Erste Vorschau."},
            "requires_server": "0.7.0",
            "files": [{"platform": "nextcloud", "kind": "tarball", "url": TARBALL, "sha256": "b" * 64, "size": 5}],
        }],
        **extra,
    }


def test_the_app_list_is_built_and_read_back_whole() -> None:
    from datetime import UTC, datetime

    catalogue = update_feed.build_apps({"apps": [app_entry()]}, now=datetime(2026, 11, 20, 10, 0, 0, 123, tzinfo=UTC))
    assert catalogue["product"] == "fadenstack-apps"
    # It expires two weeks after it is made; the release repository signs it anew every day.
    assert (catalogue["generated_at"], catalogue["expires"]) == ("2026-11-20T10:00:00Z", "2026-12-04T10:00:00Z")
    release = catalogue["apps"][0]["releases"][0]
    assert release["notes_url"] == "https://fadenstack.com/agents/nextcloud/#v0.1.10"
    assert release["files"][0]["url"] == TARBALL


def bad_release(**extra: object) -> dict:
    base = app_entry()["releases"][0]  # type: ignore[index]
    return app_entry(releases=[{**base, **extra}])


@pytest.mark.parametrize(
    ("bad", "message"),
    [
        (app_entry(id="Next Cloud"), "app id"),
        (app_entry(page="https://evil.example/"), "not a link installs show"),
        (app_entry(page=""), "a link is needed"),
        (app_entry(name={"de": "Nur deutsch"}), "English"),
        (app_entry(connect="teleport"), "connect"),
        (app_entry(agents={"default": "../x"}), "agent slug"),
        (bad_release(version="0.2.0rc1", channel="stable"), "pre-release"),
        (bad_release(date=""), "date"),
        (bad_release(requires_server="soon"), "requires_server"),
        (bad_release(files=[{"platform": "nextcloud", "kind": "tarball", "url": TARBALL, "sha256": "x", "size": 5}]),
         "sha256"),
        (bad_release(files=[{"platform": "nextcloud", "kind": "tarball", "url": "https://github.com/other/x.tgz",
                             "sha256": "b" * 64, "size": 5}]), "not a link installs show"),
        (bad_release(files=[{"platform": "nextcloud", "kind": "tarball", "url": TARBALL, "sha256": "b" * 64}]),
         "size"),
        (bad_release(files=[{"platform": "nextcloud", "kind": "tarball", "sha256": "b" * 64, "size": 5,
                             "url": "https://github.com/fadenstack/../attacker/x.tgz"}]), "not a link installs show"),
        # What a reader would drop or cut without a word: each refused when the list is built.
        (app_entry(store_id="not an id; rm -rf"), r"store_id: installs would read ''"),
        (app_entry(name={"en": "N" * 81}), r"name: installs would read"),
        (app_entry(reports_as=[f"App {i}" for i in range(11)]), r"reports_as: installs would read"),
        (bad_release(files=[{"platform": "nextcloud", "kind": "tarball", "url": TARBALL, "sha256": "b" * 64,
                             "size": 5 * 1024**3}]), r"releases: installs would read"),
    ],
)
def test_app_entries_installs_could_not_show_are_refused(bad: dict, message: str) -> None:
    with pytest.raises(update_feed.BuildError, match=message):
        update_feed.build_apps({"apps": [bad]})


@pytest.mark.parametrize("days", [0, 91])
def test_an_app_list_expires_within_a_sensible_time(days: int) -> None:
    with pytest.raises(update_feed.BuildError, match="valid_days"):
        update_feed.build_apps({"apps": []}, valid_days=days)


def test_an_app_listed_twice_is_refused() -> None:
    with pytest.raises(update_feed.BuildError, match="twice"):
        update_feed.build_apps({"apps": [app_entry(), app_entry()]})


def test_the_app_list_is_signed_only_with_a_key_trusted_for_it(monkeypatch: pytest.MonkeyPatch) -> None:
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    key = Ed25519PrivateKey.generate()
    private = base64.b64encode(
        key.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption())
    ).decode()
    public = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    feed_module = update_feed.release_feed
    monkeypatch.setattr(feed_module, "TRUSTED_KEYS", {"feed": public})
    monkeypatch.setattr(feed_module, "APPS_TRUSTED_KEYS", {"apps": "AAAA"})
    with pytest.raises(update_feed.BuildError, match="APPS_TRUSTED_KEYS"):
        update_feed.sign(b"{}", private, feed_module.APPS_TRUSTED_KEYS)  # the feed's key does not sign apps
    monkeypatch.setattr(feed_module, "APPS_TRUSTED_KEYS", {"apps": public})
    assert update_feed.sign(b"{}", private, feed_module.APPS_TRUSTED_KEYS)["key_id"] == "apps"
