"""The app list: read only when signed with a key trusted for it, shown only where its links may lead."""

from __future__ import annotations

import base64
import json
from datetime import UTC, date, datetime

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from faden_cli.core import release_feed as rf

KEY = Ed25519PrivateKey.generate()
OTHER = Ed25519PrivateKey.generate()


def public(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.b64encode(raw).decode()


KEYS = {"apps": public(KEY)}
SHA = "a" * 64
TARBALL = "https://github.com/fadenstack/fadenstack-apps/releases/download/nextcloud-v0.1.10/fadenstack_agent.tar.gz"


def release(version: str, **extra: object) -> dict:
    return {
        "version": version,
        "channel": "stable",
        "severity": "normal",
        "summary": {"en": "One line.", "de": "Eine Zeile."},
        "files": [{"platform": "nextcloud", "kind": "tarball", "url": TARBALL, "sha256": SHA, "size": 1000}],
        **extra,
    }


def app(app_id: str = "nextcloud", *releases: dict, **extra: object) -> dict:
    return {
        "id": app_id,
        "name": {"en": "Fadenstack for Nextcloud", "de": "Fadenstack für Nextcloud"},
        "page": "https://fadenstack.com/agents/nextcloud/",
        "reports_as": ["fadenstack_agent"],
        "connect": "nextcloud",
        "agents": {"default": "nextcloud-assistant"},
        "releases": list(releases) or [release("0.1.10")],
        **extra,
    }


def catalogue_bytes(
    *apps: dict, generated_at: str = "2026-11-20T10:00:00Z", expires: str | None = "2026-12-04T10:00:00Z",
    product: str = "fadenstack-apps",
) -> bytes:
    data = {"schema": 1, "product": product, "generated_at": generated_at, "apps": list(apps)}
    if expires is not None:
        data["expires"] = expires
    return json.dumps(data).encode()


def signed(index: bytes, key: Ed25519PrivateKey = KEY, key_id: str = "apps") -> bytes:
    return json.dumps({"key_id": key_id, "signature": base64.b64encode(key.sign(index)).decode()}).encode()


def catalogue(*apps: dict) -> rf.AppCatalogue:
    index = catalogue_bytes(*apps)
    return rf.verify_apps(index, signed(index), KEYS)


def test_a_signed_app_list_is_read() -> None:
    found = catalogue(app())
    nextcloud = found.app("nextcloud")
    assert nextcloud is not None
    assert nextcloud.connect == "nextcloud" and nextcloud.agents == {"default": "nextcloud-assistant"}
    assert [r.version for r in nextcloud.releases] == ["0.1.10"]
    assert nextcloud.releases[0].files[0].url == TARBALL


def test_the_app_list_trusts_its_own_keys_not_the_feeds() -> None:
    """A key that signs Fadenstack's releases cannot speak for the apps, nor the other way round."""
    assert set(rf.APPS_TRUSTED_KEYS.values()).isdisjoint(rf.TRUSTED_KEYS.values())
    assert any(k.startswith("fadenstack-apps-backup-") for k in rf.APPS_TRUSTED_KEYS)
    index = catalogue_bytes(app())
    with pytest.raises(rf.FeedError, match="does not know"):
        rf.verify_apps(index, signed(index, key_id="fadenstack-2026-10"))  # the feed's key id: not trusted here


def test_a_changed_app_list_is_refused() -> None:
    index = catalogue_bytes(app())
    signature = signed(index)
    with pytest.raises(rf.FeedError, match="changed"):
        rf.verify_apps(index.replace(b"0.1.10", b"0.1.11"), signature, KEYS)


def test_a_list_signed_with_another_key_under_a_trusted_id_is_refused() -> None:
    index = catalogue_bytes(app())
    with pytest.raises(rf.FeedError, match="changed or is not ours"):
        rf.verify_apps(index, signed(index, key=OTHER), KEYS)


def test_the_release_feed_is_not_an_app_list() -> None:
    """The feed's bytes, signed alike, still do not pass for an app list: the product says what it is."""
    index = catalogue_bytes(app(), product="fadenstack")
    with pytest.raises(rf.FeedError, match="Not a Fadenstack app list"):
        rf.verify_apps(index, signed(index), KEYS)


@pytest.mark.parametrize(
    "url",
    [
        "http://fadenstack.com/agents/",  # not https
        "https://evil.example/fadenstack.com",  # another host
        "https://fadenstack.com.evil.example/",  # a host that only starts like ours
        "https://github.com/someone-else/app/releases/download/x/y.zip",  # GitHub, not our organisation
        "https://user:pw@fadenstack.com/",  # credentials in the link
        "https://fadenstack.com:8443/",  # another port
        "javascript:alert(1)",
        "https://fadenstack.com/a b",
        "",
    ],
)
def test_links_only_to_allowed_places(url: str) -> None:
    assert rf.safe_link(url) == ""


def test_allowed_links_are_kept() -> None:
    for url in (
        "https://fadenstack.com/agents/desktop/",
        "https://help.fadenstack.com/operate/upgrading/",
        TARBALL,
        "https://chromewebstore.google.com/detail/abc",
        "https://apps.nextcloud.com/apps/fadenstack_agent",
    ):
        assert rf.safe_link(url) == url


def test_a_file_without_an_allowed_link_or_a_checksum_is_left_out() -> None:
    files = [
        {"platform": "windows-x64", "kind": "zip", "url": "https://evil.example/x.zip", "sha256": SHA},
        {"platform": "windows-x64", "kind": "zip", "url": TARBALL, "sha256": "not-a-hash"},
        {"platform": "windows-x64", "kind": "zip", "url": TARBALL},
        {"platform": "../x", "kind": "zip", "url": TARBALL, "sha256": SHA},
        {"platform": "linux-x64", "kind": "deb", "url": TARBALL, "sha256": SHA.upper(), "size": True},
    ]
    found = catalogue(app("desktop", release("0.2.0", files=files)))
    kept = found.apps[0].releases[0].files
    assert [(f.platform, f.kind, f.sha256, f.size) for f in kept] == [("linux-x64", "deb", SHA, None)]


def test_a_bad_page_link_is_dropped_but_the_app_stays() -> None:
    found = catalogue(app(page="https://evil.example/"))
    assert found.apps[0].page == ""


def test_entries_this_version_cannot_read_are_left_out() -> None:
    found = catalogue(
        app("nextcloud"),
        app("nextcloud"),  # the same id twice: the first counts
        app("Not An Id"),
        {"id": "nameless"},
        app("future", release("1.0.0", channel="nightly"), release("0.9.0")),
        app("desktop", connect="teleport", agents={"default": "../../etc", "word": "word-assistant"}),
    )
    assert [a.id for a in found.apps] == ["nextcloud", "future", "desktop"]
    assert [r.version for r in found.apps[1].releases] == ["0.9.0"]
    assert found.apps[2].connect is None and found.apps[2].agents == {"word": "word-assistant"}


def test_texts_are_one_line_and_cut() -> None:
    found = catalogue(app(name={"en": "A\n  name", "xx-not-a-lang!": "dropped"},
                          releases=[release("0.1.0", summary="x" * 1000)]))
    assert found.apps[0].name == {"en": "A name"}
    assert found.apps[0].releases[0].summary == {"en": "x" * 400}  # a plain string counts as English


def test_localized_falls_back_to_the_language_then_english() -> None:
    texts = {"en": "Hello", "de": "Hallo"}
    assert rf.localized(texts, "de-AT") == "Hallo"
    assert rf.localized(texts, "fr") == "Hello"
    assert rf.localized({"de": "Hallo"}, "fr") == "Hallo"
    assert rf.localized({}, "de") == ""


@pytest.mark.parametrize(
    ("expires", "message"),
    [(None, "no valid expires"), ("soon", "no valid expires"), ("2026-11-19T10:00:00Z", "expires before")],
)
def test_a_list_without_a_sound_expiry_is_refused(expires: str | None, message: str) -> None:
    """Every app list expires: a list that never does could be served for ever (a freeze attack)."""
    index = catalogue_bytes(app(), expires=expires)
    with pytest.raises(rf.FeedError, match=message):
        rf.verify_apps(index, signed(index), KEYS)


def test_an_expired_list_is_told_and_refused_when_read() -> None:
    found = catalogue(app())
    assert not rf.apps_expired(found, datetime(2026, 12, 4, 9, 59, tzinfo=UTC))
    assert rf.apps_expired(found, datetime(2026, 12, 4, 10, 0, tzinfo=UTC))
    rf.not_expired_apps(found, datetime(2026, 12, 1, tzinfo=UTC))
    with pytest.raises(rf.FeedError, match="expired on 2026-12-04"):
        rf.not_expired_apps(found, datetime(2026, 12, 5, tzinfo=UTC))


def test_an_older_app_list_than_one_seen_is_refused() -> None:
    found = catalogue(app())
    with pytest.raises(rf.FeedError, match="older"):
        rf.not_older_apps(found, datetime(2026, 11, 21, tzinfo=UTC))
    rf.not_older_apps(found, datetime(2026, 11, 19, tzinfo=UTC))


def test_the_app_list_is_beside_the_feed() -> None:
    assert rf.apps_url(rf.FEED_URL) == "https://fadenstack.com/updates/v1/apps.json"
    assert rf.apps_url("https://mirror.plant.local/fs/index.json?x=1") == "https://mirror.plant.local/fs/apps.json"


def nextcloud(*releases: dict, **extra: object) -> rf.App:
    found = catalogue(app("nextcloud", *releases, **extra)).app("nextcloud")
    assert found is not None
    return found


def test_nothing_in_use_is_told_nothing_urgent() -> None:
    found = rf.assess_app(nextcloud(release("0.1.10", severity="security")), "stable", [], "0.7.0")
    assert found.latest is not None and found.latest.version == "0.1.10"
    assert (found.severity, found.outdated, found.newer) == ("none", (), ())


def test_the_oldest_version_in_use_decides_the_severity() -> None:
    app_ = nextcloud(release("0.1.8", severity="security"), release("0.1.9"), release("0.1.10"))
    found = rf.assess_app(app_, "stable", ["0.1.9", "0.1.7", "dev", "0.1.10"], "0.7.0")
    assert found.in_use == ("0.1.10", "0.1.9", "0.1.7")
    assert found.outdated == ("0.1.9", "0.1.7")
    assert found.severity == "security"  # 0.1.7 still lacks 0.1.8's fix
    assert [r.version for r in found.newer] == ["0.1.10", "0.1.9", "0.1.8"]


def test_previews_only_on_the_preview_channel() -> None:
    app_ = nextcloud(release("0.1.10"), release("0.2.0rc1", channel="preview"))
    assert rf.assess_app(app_, "stable", [], None).latest.version == "0.1.10"  # type: ignore[union-attr]
    assert rf.assess_app(app_, "preview", [], None).latest.version == "0.2.0rc1"  # type: ignore[union-attr]


def test_a_series_past_its_support_is_named() -> None:
    app_ = nextcloud(
        release("0.1.10"), release("0.2.0"),
        series=[{"series": "0.1", "supported_until": "2026-12-31"}, {"series": "0.2", "supported_until": "2027-06-30"}],
    )
    found = rf.assess_app(app_, "stable", ["0.1.10", "0.2.0"], "0.7.0", today=date(2027, 1, 5))
    assert found.unsupported == ("0.1.10",)
    assert found.supported_until == date(2027, 6, 30)


def test_a_release_that_needs_a_newer_server_says_so() -> None:
    app_ = nextcloud(release("0.2.0", requires_server="0.8.0"))
    assert rf.assess_app(app_, "stable", [], "0.7.0").server_too_old
    assert not rf.assess_app(app_, "stable", [], "0.8.1").server_too_old
    assert not rf.assess_app(app_, "stable", [], "dev").server_too_old  # a checkout: no release to compare


def test_a_release_as_data_in_a_language() -> None:
    data = rf.app_release_dict(nextcloud().releases[0], "de")
    assert data["summary"] == "Eine Zeile."
    assert data["files"] == [{"platform": "nextcloud", "kind": "tarball", "url": TARBALL, "sha256": SHA, "size": 1000}]
