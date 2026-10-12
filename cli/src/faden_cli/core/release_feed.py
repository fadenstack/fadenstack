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
    trusted = TRUSTED_KEYS if keys is None else keys
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
        data = json.loads(index)
    except ValueError as exc:
        raise FeedError("The feed is not valid JSON.") from exc
    return parse(data)


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
