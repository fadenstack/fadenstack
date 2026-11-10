"""The release feed: which Fadenstack releases exist, checked and compared with an install.

The feed is a static file on fadenstack.com (``index.json``) with a signature beside it
(``index.json.sig``). Reading it sends nothing about the install: it is fetched like any
file, and compared here, locally.

This file is in the CLI (``faden_cli/core/release_feed.py``) and in the backend
(``faden_backend/services/updates/release_feed.py``), byte for byte the same -- a test
keeps them so. It does no I/O: the caller fetches the two files and hands over their bytes.

Readers in the field read this feed for years, so it only ever grows: unknown fields are
ignored, and a change that old readers would misread goes to ``/v2/``.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

SCHEMA = 1
PRODUCT = "fadenstack"
FEED_URL = "https://fadenstack.com/updates/v1/index.json"

#: Keys a feed may be signed with: key id -> raw Ed25519 public key, base64. A new key
#: ships here in a release before anything is signed with it.
TRUSTED_KEYS: dict[str, str] = {
    "fadenstack-2026-10": "F87bn6AH1s5Lrw0bUKebHzxWO23vv8A6Hh9/+cxHjyo=",
    # The backup key: its private half is kept offline and signs nothing while the key above is usable. A key
    # an install trusts can only come with a release, so without it a lost or leaked signing key would leave
    # every install unable to trust a feed again until someone upgraded it by hand.
    "fadenstack-backup-2026-10": "4X1SvMFK7tO+04Ua+y0w7TC/GaW43tlqzr3vUoxFVWU=",
}

#: Least to most urgent. "none": nothing newer.
SEVERITIES = ("none", "normal", "security", "critical")
CHANNELS = ("stable", "preview")

#: Where the standalone CLI is downloaded from, by tag.
RELEASE_PAGE = "https://github.com/fadenstack/fadenstack/releases/tag/v{version}"


class FeedError(ValueError):
    """The feed cannot be used; the message says why."""


def signature_url(feed_url: str) -> str:
    """Where the signature of the feed at *feed_url* is."""
    return f"{feed_url}.sig"


def parse_version(text: str | None) -> Version | None:
    """*text* as a release version; None for anything else (``dev``, ``main``, a commit)."""
    if not text:
        return None
    try:
        return Version(text.strip().removeprefix("v"))
    except InvalidVersion:
        return None


def is_newer(a: str | None, b: str | None) -> bool:
    """Whether release *a* comes after release *b* (False when either is no release version)."""
    va, vb = parse_version(a), parse_version(b)
    return va is not None and vb is not None and va > vb


@dataclass(frozen=True)
class Fix:
    """A security problem a release fixes."""

    id: str
    severity: str
    summary: str


@dataclass(frozen=True)
class Release:
    """One release in the feed."""

    version: str
    channel: str
    date: str
    severity: str
    summary: str
    notes_url: str
    #: The oldest release that can upgrade straight to this one; None: any.
    upgrade_from: str | None
    fixes: tuple[Fix, ...]
    #: Versions of the parts released with it: ``cli``, ``agent``.
    components: dict[str, str]

    @property
    def parsed(self) -> Version:
        """The version, for comparing."""
        found = parse_version(self.version)
        if found is None:  # parse() keeps only releases with a version; one made by hand may not be
            raise FeedError(f"{self.version!r} is not a release version.")
        return found


@dataclass(frozen=True)
class Feed:
    """A verified feed."""

    generated_at: datetime
    #: Newest first.
    releases: tuple[Release, ...]
    #: Series ("0.4") -> the last day it gets fixes.
    supported_until: dict[str, date]

    def release(self, version: str) -> Release | None:
        """The entry for *version*, if the feed has one."""
        wanted = parse_version(version)
        return next((r for r in self.releases if r.parsed == wanted), None)


def verify(index: bytes, signature: bytes, keys: dict[str, str] | None = None) -> Feed:
    """The feed in *index*, once *signature* proves it was signed with a trusted key."""
    return parse(_signed_json(index, signature, TRUSTED_KEYS if keys is None else keys))


def _signed_json(index: bytes, signature: bytes, trusted: dict[str, str]) -> Any:
    """*index* as JSON, once *signature* proves it was signed with one of the *trusted* keys."""
    try:
        sig = json.loads(signature)
        key_id = str(sig["key_id"])
        raw_sig = base64.b64decode(sig["signature"], validate=True)
    except (ValueError, KeyError, TypeError, binascii.Error) as exc:
        raise FeedError("The feed's signature file is not readable.") from exc
    key = trusted.get(key_id)
    if key is None:
        raise FeedError(f"The feed is signed with a key this version does not know ({key_id}).")
    try:
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key)).verify(raw_sig, index)
    except InvalidSignature as exc:
        raise FeedError("The feed's signature does not match: it was changed or is not ours.") from exc
    try:
        return json.loads(index)
    except ValueError as exc:
        raise FeedError("The feed is not valid JSON.") from exc


def parse(data: Any) -> Feed:
    """The feed in *data* (already verified). Entries this version cannot read are left out."""
    if not isinstance(data, dict):
        raise FeedError("The feed is not a JSON object.")
    if data.get("schema") != SCHEMA or data.get("product") != PRODUCT:
        raise FeedError(f"Not a Fadenstack release feed of schema {SCHEMA}.")
    try:
        generated_at = datetime.fromisoformat(str(data["generated_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise FeedError("The feed has no valid generated_at.") from exc
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    releases = [r for r in (_release(raw) for raw in data.get("releases") or []) if r is not None]
    releases.sort(key=lambda r: r.parsed, reverse=True)
    supported: dict[str, date] = {}
    for raw in data.get("series") or []:
        try:
            supported[str(raw["series"])] = date.fromisoformat(str(raw["supported_until"]))
        except (KeyError, TypeError, ValueError):
            continue
    return Feed(generated_at=generated_at, releases=tuple(releases), supported_until=supported)


def _release(raw: Any) -> Release | None:
    if not isinstance(raw, dict) or parse_version(str(raw.get("version", ""))) is None:
        return None
    channel = str(raw.get("channel", "stable"))
    if channel not in CHANNELS:
        return None  # a channel added later: not this version's to offer
    severity = str(raw.get("severity", "normal"))
    if severity not in SEVERITIES[1:]:
        severity = "normal"
    upgrade_from = raw.get("upgrade_from")
    fixes = tuple(
        Fix(str(f.get("id", "")), str(f.get("severity", "")), str(f.get("summary", "")))
        for f in raw.get("fixes") or []
        if isinstance(f, dict)
    )
    components = raw.get("components") or {}
    return Release(
        version=str(raw["version"]),
        channel=channel,
        date=str(raw.get("date", "")),
        severity=severity,
        summary=str(raw.get("summary", "")),
        notes_url=str(raw.get("notes_url", "")),
        upgrade_from=str(upgrade_from) if parse_version(str(upgrade_from or "")) else None,
        fixes=fixes,
        components={str(k): str(v) for k, v in components.items()} if isinstance(components, dict) else {},
    )


def not_older(feed: Feed, seen: datetime | None) -> None:
    """Refuse a feed older than one already accepted: replaying an old feed must not hide a release."""
    if seen is not None and feed.generated_at < seen:
        raise FeedError(
            f"The feed is older ({feed.generated_at.isoformat()}) than one already seen ({seen.isoformat()})."
        )


@dataclass(frozen=True)
class Assessment:
    """An install's release, measured against the feed."""

    current: str
    channel: str
    #: The newest release on the channel; None when the feed offers none.
    latest: Release | None
    #: Releases on the channel newer than the install's, newest first.
    newer: tuple[Release, ...]
    #: The most urgent of them: none | normal | security | critical.
    severity: str
    #: Releases to install, in order, to reach ``latest`` (a release that cannot be skipped comes first).
    path: tuple[str, ...]
    #: The last day the install's series gets fixes, when the feed says.
    supported_until: date | None
    #: That day has passed.
    unsupported: bool

    @property
    def update_available(self) -> bool:
        """A newer release is out."""
        return bool(self.newer)

    @property
    def target(self) -> Release | None:
        """The first release to install: the next step on the path."""
        return next((r for r in self.newer if self.path and r.version == self.path[0]), None)


def offered(feed: Feed, channel: str) -> list[Release]:
    """Releases an install on *channel* is offered: stable ones, and on preview the previews too."""
    return [
        r
        for r in feed.releases
        if channel == "preview" or (r.channel == "stable" and not r.parsed.is_prerelease)
    ]


def assess(feed: Feed, current: str, channel: str = "stable", today: date | None = None) -> Assessment:
    """Where *current* stands: newer releases, how urgent, the path to the newest, support."""
    channel = channel if channel in CHANNELS else "stable"
    releases = offered(feed, channel)
    latest = releases[0] if releases else None
    cur = parse_version(current)
    if cur is None:
        return Assessment(current, channel, latest, (), "none", (), None, False)
    newer = tuple(r for r in releases if r.parsed > cur)
    severity = max((r.severity for r in newer), key=SEVERITIES.index, default="none")
    path = upgrade_path(releases, cur, latest.parsed) if newer and latest else ()
    until = feed.supported_until.get(f"{cur.major}.{cur.minor}")
    unsupported = until is not None and (today or datetime.now(UTC).date()) > until
    return Assessment(current, channel, latest, newer, severity, path, until, unsupported)


def upgrade_path(releases: list[Release], current: Version, target: Version) -> tuple[str, ...]:
    """The releases to install one after the other to go from *current* to *target*.

    Each step is the newest release reachable straight from where the install is: one whose
    ``upgrade_from`` is no newer than that.
    """
    path: list[str] = []
    at = current
    while at < target:
        reachable = [
            r
            for r in releases
            if at < r.parsed <= target and (r.upgrade_from is None or Version(r.upgrade_from) <= at)
        ]
        if not reachable:
            path.append(str(target))  # the feed names no way there: say where, not how
            break
        step = max(reachable, key=lambda r: r.parsed)
        path.append(step.version)
        at = step.parsed
    return tuple(path)


def required_first(feed: Feed, installed: str, target: str) -> str | None:
    """The release to install before going from *installed* straight to *target*; None: nothing is skipped."""
    release = feed.release(target)
    cur = parse_version(installed)
    if release is None or cur is None or release.upgrade_from is None or Version(release.upgrade_from) <= cur:
        return None
    path = upgrade_path(list(feed.releases), cur, release.parsed)
    # With no way there in the feed, upgrade_path names the target itself -- "0.4.3 has to come first"
    # for 0.0.1 to 0.4.3 -- so the release it upgrades from is the one to install first.
    return path[0] if path and path[0] != str(release.parsed) else release.upgrade_from


#: How the CLI was installed, as `faden deploy` records it (FADEN_CLI_INSTALLER).
INSTALLERS = ("pipx", "uv", "pip", "binary", "source")


def cli_install_command(installer: str | None, version: str, *, latest: bool) -> str:
    """The command that puts CLI *version* on this server, for how the CLI was installed.

    *latest*: the version is the newest stable release, so the plain upgrade command gets it.
    """
    parsed = parse_version(version)
    # A step on the way, or a preview (which plain upgrades skip), is named exactly.
    pin = not latest or (parsed is not None and parsed.is_prerelease)
    match installer:
        case "uv":
            return f"uv tool install --force fadenstack=={version}" if pin else "uv tool upgrade fadenstack"
        case "pip":
            return f"pip install fadenstack=={version}" if pin else "pip install -U fadenstack"
        case "binary":
            return f"download faden for this system from {RELEASE_PAGE.format(version=version)}"
        case "source":
            return "git pull"
        case _:
            return f"pipx install --force fadenstack=={version}" if pin else "pipx upgrade fadenstack"


def upgrade_commands(installer: str | None, assessment: Assessment) -> list[str]:
    """The commands an operator runs on the server to take the next step on the path."""
    target = assessment.target
    if target is None:
        return []
    latest = assessment.latest is not None and target.version == assessment.latest.version
    return [
        cli_install_command(installer, target.version, latest=latest),
        "faden upgrade --dry-run",
        "faden upgrade -y",
    ]


def as_dict(release: Release) -> dict[str, Any]:
    """*release* as JSON-ready data."""
    return {
        "version": release.version,
        "channel": release.channel,
        "date": release.date,
        "severity": release.severity,
        "summary": release.summary,
        "notes_url": release.notes_url,
        "upgrade_from": release.upgrade_from,
        "fixes": [{"id": f.id, "severity": f.severity, "summary": f.summary} for f in release.fixes],
        "components": dict(release.components),
    }


# --------------------------------------------------------------------------------------------------------------
# The app list: the apps people run against a Fadenstack server (the desktop agent, the Office add-ins, the
# Nextcloud app, the Chrome extension), each with its releases. A second file beside the feed, ``apps.json`` with
# ``apps.json.sig``, signed with keys of its own: the apps are released from their own repository, and a key that
# leaked there cannot speak for Fadenstack's releases, nor the other way round. Read, kept and compared like the
# feed: nothing about the install or the apps it runs is sent.
# --------------------------------------------------------------------------------------------------------------

APPS_PRODUCT = "fadenstack-apps"
APPS_FILE = "apps.json"

#: Keys the app list may be signed with: key id -> raw Ed25519 public key, base64. A new key ships here in a
#: release before anything is signed with it; the backup key's private half is kept offline (as for the feed).
APPS_TRUSTED_KEYS: dict[str, str] = {
    "fadenstack-apps-2026-11": "r2UpGbwvhwNgNST+TqAjhLp5qRXMZaqk3VTL2JEEEYg=",
    "fadenstack-apps-backup-2026-11": "tGkx5JC2lclKlhd5/Jukap6RpOy7Qetj9FLjIXnJILQ=",
}

#: Where the app list's links may lead: https on one of these hosts, nowhere else (OWASP ASVS 5.0 13.2.4: links
#: an install shows are not taken from just anywhere). On GitHub only under the Fadenstack organisation.
LINK_HOSTS = ("fadenstack.com", "help.fadenstack.com", "github.com", "chromewebstore.google.com", "apps.nextcloud.com")
LINK_PATHS = {"github.com": "/fadenstack/"}

#: The apps the console knows how to connect to the server (what it can fill in for them); an app of another kind
#: is listed with its links only.
CONNECT_KINDS = ("desktop", "office", "nextcloud", "chrome")

#: Lengths past which a text is cut: the console shows these as they come, and a list is not a place for essays.
_TEXT_LIMITS = {"name": 80, "summary": 400, "description": 600}
_ID = re.compile(r"[a-z][a-z0-9-]{0,39}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_LANG = re.compile(r"[a-z]{2}(-[A-Za-z]{2,4})?")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")
#: A download larger than this is not one of ours.
_MAX_FILE_SIZE = 4 * 1024**3


def apps_url(feed_url: str) -> str:
    """Where the app list is, beside the feed at *feed_url* (also a mirror's)."""
    head, _, _ = feed_url.split("?", 1)[0].rpartition("/")
    return f"{head}/{APPS_FILE}"


def safe_link(url: Any) -> str:
    """*url* when it is an https link to an allowed place (:data:`LINK_HOSTS`); else ``""``."""
    from urllib.parse import urlsplit  # noqa: PLC0415

    text = str(url or "").strip()
    try:
        parts = urlsplit(text)
        port = parts.port
    except ValueError:
        return ""
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or port is not None or parts.username or parts.password or host not in LINK_HOSTS:
        return ""
    if any(c.isspace() or ord(c) < 32 for c in text):
        return ""
    prefix = LINK_PATHS.get(host)
    if prefix and not parts.path.startswith(prefix):
        return ""
    return text


def localized(texts: dict[str, str], lang: str | None) -> str:
    """The text of *texts* for *lang* (``de-AT`` falls back to ``de``), else English, else any."""
    if not texts:
        return ""
    want = (lang or "en").strip()
    for key in (want, want.split("-")[0].lower(), "en"):
        if texts.get(key):
            return texts[key]
    return next(iter(texts.values()))


def _texts(raw: Any, limit: int) -> dict[str, str]:
    """A text in some languages (``{"en": ..., "de": ...}``; a plain string counts as English), each one line."""
    if isinstance(raw, str):
        raw = {"en": raw}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for lang, value in raw.items():
        text = " ".join(str(value or "").split())
        if isinstance(lang, str) and _LANG.fullmatch(lang) and text:
            out[lang] = text[:limit]
    return out


@dataclass(frozen=True)
class AppFile:
    """One file of an app release, to download."""

    #: Where it runs: ``windows-x64``, ``linux-x64``, ``nextcloud``, ...
    platform: str
    #: What it is: ``setup``, ``zip``, ``deb``, ``rpm``, ``appimage``, ``tarball``, ...
    kind: str
    url: str
    sha256: str
    size: int | None


@dataclass(frozen=True)
class AppRelease:
    """One release of an app."""

    version: str
    channel: str
    date: str
    severity: str
    #: Language -> one line.
    summary: dict[str, str]
    notes_url: str
    #: The oldest Fadenstack release this one works with; None: any.
    requires_server: str | None
    fixes: tuple[Fix, ...]
    files: tuple[AppFile, ...]

    @property
    def parsed(self) -> Version:
        """The version, for comparing."""
        found = parse_version(self.version)
        if found is None:  # parse_apps() keeps only releases with a version
            raise FeedError(f"{self.version!r} is not a release version.")
        return found


@dataclass(frozen=True)
class App:
    """One app and its releases."""

    id: str
    name: dict[str, str]
    description: dict[str, str]
    #: Its page on fadenstack.com.
    page: str
    #: The ``app_name`` its manifests carry (what the SDK reports): how the server knows it is in use.
    reports_as: tuple[str, ...]
    #: How the console helps connect it (:data:`CONNECT_KINDS`); None: links only.
    connect: str | None
    #: Default agent slugs it works for, by role: ``{"default": "desktop-assistant"}``, ``{"word": ...}``.
    agents: dict[str, str]
    #: Its listing in a store (Chrome Web Store, Nextcloud app store), when it has one.
    store: str
    #: Its id there (the Chrome extension's id, for an IT policy).
    store_id: str
    #: Series ("0.2") -> the last day it gets fixes.
    supported_until: dict[str, date]
    #: Newest first.
    releases: tuple[AppRelease, ...]


@dataclass(frozen=True)
class AppCatalogue:
    """A verified app list."""

    generated_at: datetime
    apps: tuple[App, ...]

    def app(self, app_id: str) -> App | None:
        """The app with *app_id*, if the list has it."""
        return next((a for a in self.apps if a.id == app_id), None)


def verify_apps(index: bytes, signature: bytes, keys: dict[str, str] | None = None) -> AppCatalogue:
    """The app list in *index*, once *signature* proves it was signed with a key trusted for it."""
    return parse_apps(_signed_json(index, signature, APPS_TRUSTED_KEYS if keys is None else keys))


def parse_apps(data: Any) -> AppCatalogue:
    """The app list in *data* (already verified). What this version cannot read, or may not show, is left out."""
    if not isinstance(data, dict):
        raise FeedError("The app list is not a JSON object.")
    if data.get("schema") != SCHEMA or data.get("product") != APPS_PRODUCT:
        raise FeedError(f"Not a Fadenstack app list of schema {SCHEMA}.")
    try:
        generated_at = datetime.fromisoformat(str(data["generated_at"]).replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise FeedError("The app list has no valid generated_at.") from exc
    if generated_at.tzinfo is None:
        generated_at = generated_at.replace(tzinfo=UTC)
    apps: list[App] = []
    for raw in data.get("apps") or []:
        app = _app(raw)
        if app is not None and all(a.id != app.id for a in apps):
            apps.append(app)
    return AppCatalogue(generated_at=generated_at, apps=tuple(apps))


def _app(raw: Any) -> App | None:
    if not isinstance(raw, dict) or not _ID.fullmatch(str(raw.get("id", ""))):
        return None
    name = _texts(raw.get("name"), _TEXT_LIMITS["name"])
    if not name:
        return None
    releases = [r for r in (_app_release(x) for x in raw.get("releases") or []) if r is not None]
    releases.sort(key=lambda r: r.parsed, reverse=True)
    unique: list[AppRelease] = []
    for r in releases:
        if not unique or unique[-1].parsed != r.parsed:
            unique.append(r)
    supported: dict[str, date] = {}
    for s in raw.get("series") or []:
        try:
            supported[str(s["series"])] = date.fromisoformat(str(s["supported_until"]))
        except (KeyError, TypeError, ValueError):
            continue
    agents = raw.get("agents") if isinstance(raw.get("agents"), dict) else {}
    connect = str(raw.get("connect") or "")
    store_id = str(raw.get("store_id") or "")
    return App(
        id=str(raw["id"]),
        name=name,
        description=_texts(raw.get("description"), _TEXT_LIMITS["description"]),
        page=safe_link(raw.get("page")),
        reports_as=tuple(
            str(n)[:100] for n in (raw.get("reports_as") or [])[:10] if isinstance(n, str) and n.strip()
        ),
        connect=connect if connect in CONNECT_KINDS else None,
        agents={
            str(role): str(slug)
            for role, slug in list(agents.items())[:10]
            if _TOKEN.fullmatch(str(role)) and _SLUG.fullmatch(str(slug))
        },
        store=safe_link(raw.get("store")),
        store_id=store_id if re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", store_id) else "",
        supported_until=supported,
        releases=tuple(unique),
    )


def _app_release(raw: Any) -> AppRelease | None:
    if not isinstance(raw, dict) or parse_version(str(raw.get("version", ""))) is None:
        return None
    channel = str(raw.get("channel", "stable"))
    if channel not in CHANNELS:
        return None
    severity = str(raw.get("severity", "normal"))
    if severity not in SEVERITIES[1:]:
        severity = "normal"
    requires = raw.get("requires_server")
    return AppRelease(
        version=str(raw["version"]),
        channel=channel,
        date=str(raw.get("date", ""))[:10],
        severity=severity,
        summary=_texts(raw.get("summary"), _TEXT_LIMITS["summary"]),
        notes_url=safe_link(raw.get("notes_url")),
        requires_server=str(requires) if parse_version(str(requires or "")) else None,
        fixes=tuple(
            Fix(
                str(f.get("id", ""))[:64],
                str(f.get("severity", ""))[:16],
                " ".join(str(f.get("summary", "")).split())[:400],
            )
            for f in raw.get("fixes") or []
            if isinstance(f, dict)
        ),
        files=tuple(f for f in (_app_file(x) for x in raw.get("files") or []) if f is not None),
    )


def _app_file(raw: Any) -> AppFile | None:
    if not isinstance(raw, dict):
        return None
    url = safe_link(raw.get("url"))
    sha256 = str(raw.get("sha256", "")).lower()
    platform, kind = str(raw.get("platform", "")), str(raw.get("kind", ""))
    if not url or not _SHA256.fullmatch(sha256) or not _TOKEN.fullmatch(platform) or not _TOKEN.fullmatch(kind):
        return None
    size = raw.get("size")
    ok_size = isinstance(size, int) and not isinstance(size, bool) and 0 < size <= _MAX_FILE_SIZE
    return AppFile(platform=platform, kind=kind, url=url, sha256=sha256, size=size if ok_size else None)


def not_older_apps(catalogue: AppCatalogue, seen: datetime | None) -> None:
    """Refuse an app list older than one already accepted, as for the feed."""
    if seen is not None and catalogue.generated_at < seen:
        raise FeedError(
            f"The app list is older ({catalogue.generated_at.isoformat()}) than one already seen ({seen.isoformat()})."
        )


def offered_app_releases(app: App, channel: str) -> list[AppRelease]:
    """An app's releases offered on *channel*: stable ones, and on preview the previews too."""
    return [
        r for r in app.releases if channel == "preview" or (r.channel == "stable" and not r.parsed.is_prerelease)
    ]


@dataclass(frozen=True)
class AppAssessment:
    """An app, measured against what runs here: its releases, the versions in use, the server's release."""

    app: App
    #: The newest release on the channel; None when it has none there.
    latest: AppRelease | None
    #: Versions people use (as their apps report them), newest first.
    in_use: tuple[str, ...]
    #: Of those, the ones older than ``latest``.
    outdated: tuple[str, ...]
    #: The most urgent release newer than a version in use: none | normal | security | critical.
    severity: str
    #: Releases on the channel newer than the oldest version in use, newest first.
    newer: tuple[AppRelease, ...]
    #: Versions in use whose series is past its last day of fixes.
    unsupported: tuple[str, ...]
    #: The last day of fixes of ``latest``'s series, when the list says.
    supported_until: date | None
    #: ``latest`` needs a newer Fadenstack than this server runs.
    server_too_old: bool


def assess_app(
    app: App, channel: str, in_use: list[str] | tuple[str, ...], server: str | None, today: date | None = None,
) -> AppAssessment:
    """Where *app* stands here: *in_use* are the versions its people run, *server* this install's release."""
    channel = channel if channel in CHANNELS else "stable"
    releases = offered_app_releases(app, channel)
    latest = releases[0] if releases else None
    versions = sorted({v for v in (parse_version(x) for x in in_use) if v is not None}, reverse=True)
    oldest = versions[-1] if versions else None
    newer = tuple(r for r in releases if oldest is not None and r.parsed > oldest)
    severity = max((r.severity for r in newer), key=SEVERITIES.index, default="none")
    day = today or datetime.now(UTC).date()
    unsupported = tuple(
        str(v) for v in versions
        if (until := app.supported_until.get(f"{v.major}.{v.minor}")) is not None and day > until
    )
    until = app.supported_until.get(f"{latest.parsed.major}.{latest.parsed.minor}") if latest else None
    return AppAssessment(
        app=app,
        latest=latest,
        in_use=tuple(str(v) for v in versions),
        outdated=tuple(str(v) for v in versions if latest is not None and v < latest.parsed),
        severity=severity,
        newer=newer,
        unsupported=unsupported,
        supported_until=until,
        server_too_old=bool(latest and latest.requires_server and is_newer(latest.requires_server, server)),
    )


def app_release_dict(release: AppRelease, lang: str | None = None) -> dict[str, Any]:
    """*release* as JSON-ready data; with *lang*, its summary in that language."""
    return {
        "version": release.version,
        "channel": release.channel,
        "date": release.date,
        "severity": release.severity,
        "summary": localized(release.summary, lang),
        "notes_url": release.notes_url,
        "requires_server": release.requires_server,
        "fixes": [{"id": f.id, "severity": f.severity, "summary": f.summary} for f in release.fixes],
        "files": [
            {"platform": f.platform, "kind": f.kind, "url": f.url, "sha256": f.sha256, "size": f.size}
            for f in release.files
        ],
    }
