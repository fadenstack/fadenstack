"""Build, sign and check the release feed installs read to learn about new releases.

    python release/update_feed.py build --version 0.4.2 --out dist/feed
    UPDATE_FEED_SIGNING_KEY=... python release/update_feed.py sign dist/feed
    python release/update_feed.py verify dist/feed
    python release/update_feed.py keygen <folder>        # a new key pair, for a key change
    python release/update_feed.py notes --version 0.4.2 --out notes.md   # the GitHub release's text

    python release/update_feed.py apps-build --source apps.yaml --out dist/apps   # the app list
    APPS_FEED_SIGNING_KEY=... python release/update_feed.py apps-sign dist/apps
    python release/update_feed.py apps-verify dist/apps

``build`` turns release/releases.yaml into ``index.json``; ``sign`` writes ``index.json.sig`` with
the private key (raw Ed25519, base64: the secret UPDATE_FEED_SIGNING_KEY, or --key-file) and
refuses a key the CLI does not ship; ``verify`` reads the result the way an install does.

The ``apps-*`` commands do the same for the app list (``apps.json``: the desktop agent, the Office
add-ins, the Nextcloud app, the Chrome extension, with their releases). Its source, ``apps.yaml``,
lives in the apps' release repository, which runs these commands from a checkout of this one; it is
signed with keys of its own (APPS_TRUSTED_KEYS; the secret APPS_FEED_SIGNING_KEY).

The feed's format and its checks are the CLI's (cli/src/faden_cli/core/release_feed.py), so what
is published here is what installs can read.

Keys: installs trust the keys their release ships (TRUSTED_KEYS, the same in the CLI and the backend):
the signing key, whose private half is the secret UPDATE_FEED_SIGNING_KEY, and a backup key whose
private half is kept offline and signs nothing while the signing key is usable. When the signing key
is lost or leaked:
  1. keygen a new pair; add its public key to TRUSTED_KEYS in both copies, and drop the old one if it
     leaked;
  2. sign the feed that announces that release with the BACKUP key (--key-file): every install trusts
     it, also those whose CLI never saw the new key;
  3. once installs run the release, sign with the new key, and keygen a new backup pair the same way.
A key must ship in a release before anything is signed with it.

Needs: pyyaml, cryptography, packaging.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import sys
from datetime import UTC, date, datetime
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli" / "src" / "faden_cli" / "core"))

import release_feed  # noqa: E402  (the CLI's module, without importing the CLI)

RELEASES = ROOT / "release" / "releases.yaml"
#: Each release's notes, unless its entry names others: the releases page on fadenstack.com, which renders
#: this same feed (fadenstack.com repository, src/releases.njk).
NOTES_PAGE = "https://fadenstack.com/releases/#v{version}"
AGENT_VERSION_FILE = ROOT / "backend" / "faden_backend" / "web" / "api" / "install" / "__init__.py"
INDEX = "index.json"
SIG = "index.json.sig"


class BuildError(Exception):
    """The feed cannot be built; the message says what to fix."""


def agent_version() -> str | None:
    """The agent version the backend of this checkout expects."""
    found = re.search(r'^AGENT_VERSION = "([^"]+)"', AGENT_VERSION_FILE.read_text(encoding="utf-8"), re.M)
    return found.group(1) if found else None


def build(source: dict, *, version: str | None, agent: str | None, today: date) -> dict:
    """The feed for *source* (releases.yaml); with *version*, checks that release's entry first."""
    entries = list(source.get("releases") or [])
    seen: set[str] = set()
    releases = []
    for entry in entries:
        v = str(entry.get("version", ""))
        parsed = release_feed.parse_version(v)
        if parsed is None:
            raise BuildError(f"{v!r} is not a release version")
        if str(parsed) in seen:
            raise BuildError(f"{v} is listed twice")
        seen.add(str(parsed))
        channel = entry.get("channel", "stable")
        if channel not in release_feed.CHANNELS:
            raise BuildError(f"{v}: channel must be one of {', '.join(release_feed.CHANNELS)}")
        if parsed.is_prerelease and channel != "preview":
            raise BuildError(f"{v} is a pre-release: its channel is preview")
        severity = entry.get("severity", "normal")
        if severity not in release_feed.SEVERITIES[1:]:
            raise BuildError(f"{v}: severity must be normal, security or critical")
        upgrade_from = entry.get("upgrade_from")
        if upgrade_from is not None and release_feed.parse_version(str(upgrade_from)) is None:
            raise BuildError(f"{v}: upgrade_from {upgrade_from!r} is not a release version")
        when = str(entry.get("date") or "")
        if not when and version and str(parsed) == str(release_feed.parse_version(version)):
            when = today.isoformat()
        if when:
            date.fromisoformat(when)
        if not str(entry.get("summary") or "").strip():
            raise BuildError(f"{v}: a summary is needed")
        releases.append({
            "version": str(parsed),
            "channel": channel,
            "date": when,
            "severity": severity,
            "summary": " ".join(str(entry["summary"]).split()),
            # What is new, for people (the releases page); installs ignore it.
            "highlights": [" ".join(str(h).split()) for h in entry.get("highlights") or [] if str(h).strip()],
            "notes_url": entry.get("notes_url") or NOTES_PAGE.format(version=parsed),
            "upgrade_from": str(upgrade_from) if upgrade_from is not None else None,
            "fixes": [
                {"id": str(f["id"]), "severity": str(f.get("severity", "")), "summary": str(f.get("summary", ""))}
                for f in entry.get("fixes") or []
            ],
            "components": {"cli": str(parsed), **{k: str(c) for k, c in (entry.get("components") or {}).items()}},
        })

    if version is not None:
        wanted = release_feed.parse_version(version)
        entry = next((r for r in releases if release_feed.parse_version(r["version"]) == wanted), None)
        if entry is None:
            raise BuildError(
                f"release/releases.yaml has no entry for {version}: add it (severity, summary) and tag again"
            )
        if agent and entry["components"].get("agent") != agent:
            raise BuildError(
                f"{version}: components.agent is {entry['components'].get('agent')!r}, "
                f"but the backend expects agent {agent} (AGENT_VERSION)"
            )

    releases.sort(key=lambda r: release_feed.parse_version(r["version"]), reverse=True)
    stable = next((r["version"] for r in releases if r["channel"] == "stable"), None)
    preview = next((r["version"] for r in releases), None)
    series = [
        {"series": str(s["series"]), "supported_until": date.fromisoformat(str(s["supported_until"])).isoformat()}
        for s in source.get("series") or []
    ]
    feed = {
        "schema": release_feed.SCHEMA,
        "product": release_feed.PRODUCT,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "channels": {"stable": stable, "preview": preview},
        "series": series,
        "releases": releases,
    }
    release_feed.parse(feed)  # what installs will read
    return feed


def notes(feed: dict, version: str) -> str:
    """The top of the GitHub release's text for *version*: its summary and highlights, as the feed has them."""
    wanted = release_feed.parse_version(version)
    entry = next((r for r in feed["releases"] if release_feed.parse_version(r["version"]) == wanted), None)
    if entry is None:
        raise BuildError(f"release/releases.yaml has no entry for {version}")
    lines = [entry["summary"], ""]
    if entry["highlights"]:
        lines += ["**What's new**", ""] + [f"- {h}" for h in entry["highlights"]] + [""]
    lines.append(f"Release notes: {entry['notes_url']}")
    return "\n".join(lines) + "\n"


def sign(index: bytes, private_key_b64: str, keys: dict[str, str] | None = None) -> dict:
    """The signature file for *index*, naming the trusted key the private key belongs to.

    *keys*: the keys installs trust for this file (default: the feed's, ``TRUSTED_KEYS``).
    """
    trusted = release_feed.TRUSTED_KEYS if keys is None else keys
    name = "TRUSTED_KEYS" if keys is None else "APPS_TRUSTED_KEYS"
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64.strip()))
    public = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    key_id = next((kid for kid, pub in trusted.items() if pub == public), None)
    if key_id is None:
        raise BuildError(
            f"This signing key is not one the CLI and backend trust for this file (release_feed.{name}): "
            f"installs would refuse it. Its public key: {public}"
        )
    return {"key_id": key_id, "signature": base64.b64encode(key.sign(index)).decode()}


APPS_INDEX = "apps.json"
APPS_SIG = "apps.json.sig"
_APP_ID = re.compile(r"[a-z][a-z0-9-]{0,39}")
_SLUG = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")
_TOKEN = re.compile(r"[a-z0-9][a-z0-9._-]{0,39}")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def _texts(where: str, raw: object, *, required: bool) -> dict[str, str]:
    """A text in some languages, English first; refused when English is missing and *required*."""
    if isinstance(raw, str):
        raw = {"en": raw}
    texts = {str(k): " ".join(str(v).split()) for k, v in (raw or {}).items() if str(v or "").strip()}  # type: ignore[union-attr]
    if required and not texts.get("en"):
        raise BuildError(f"{where}: an English text (en) is needed")
    return texts


def _link(where: str, url: object, *, required: bool = False) -> str:
    text = str(url or "").strip()
    if not text:
        if required:
            raise BuildError(f"{where}: a link is needed")
        return ""
    if release_feed.safe_link(text) != text:
        raise BuildError(
            f"{where}: {text!r} is not a link installs show (https on {', '.join(release_feed.LINK_HOSTS)}; "
            "GitHub only under github.com/fadenstack/)"
        )
    return text


def build_apps(source: dict) -> dict:
    """The app list for *source* (apps.yaml). Refuses anything installs would leave out, so nothing goes missing."""
    apps = []
    seen: set[str] = set()
    for raw in source.get("apps") or []:
        app_id = str(raw.get("id", ""))
        if not _APP_ID.fullmatch(app_id):
            raise BuildError(f"{app_id!r}: an app id is lower case letters, digits and -, starting with a letter")
        if app_id in seen:
            raise BuildError(f"{app_id} is listed twice")
        seen.add(app_id)
        connect = raw.get("connect")
        if connect is not None and connect not in release_feed.CONNECT_KINDS:
            raise BuildError(f"{app_id}: connect must be one of {', '.join(release_feed.CONNECT_KINDS)}")
        agents = {str(k): str(v) for k, v in (raw.get("agents") or {}).items()}
        for role, slug in agents.items():
            if not _TOKEN.fullmatch(role) or not _SLUG.fullmatch(slug):
                raise BuildError(f"{app_id}: agents.{role}: {slug!r} is not an agent slug")
        page = _link(f"{app_id}.page", raw.get("page"), required=True)
        releases = []
        versions: set[str] = set()
        for rel in raw.get("releases") or []:
            v = str(rel.get("version", ""))
            parsed = release_feed.parse_version(v)
            where = f"{app_id} {v}"
            if parsed is None:
                raise BuildError(f"{where}: not a release version")
            if str(parsed) in versions:
                raise BuildError(f"{where}: listed twice")
            versions.add(str(parsed))
            channel = rel.get("channel", "stable")
            if channel not in release_feed.CHANNELS:
                raise BuildError(f"{where}: channel must be one of {', '.join(release_feed.CHANNELS)}")
            if parsed.is_prerelease and channel != "preview":
                raise BuildError(f"{where}: a pre-release's channel is preview")
            severity = rel.get("severity", "normal")
            if severity not in release_feed.SEVERITIES[1:]:
                raise BuildError(f"{where}: severity must be normal, security or critical")
            when = str(rel.get("date") or "")
            try:
                date.fromisoformat(when)
            except ValueError as exc:
                raise BuildError(f"{where}: date must be YYYY-MM-DD") from exc
            requires = rel.get("requires_server")
            if requires is not None and release_feed.parse_version(str(requires)) is None:
                raise BuildError(f"{where}: requires_server {requires!r} is not a release version")
            files = []
            for f in rel.get("files") or []:
                fw = f"{where} file {f.get('url', '')}"
                if not _TOKEN.fullmatch(str(f.get("platform", ""))) or not _TOKEN.fullmatch(str(f.get("kind", ""))):
                    raise BuildError(f"{fw}: platform and kind are lower-case words (windows-x64, setup)")
                if not _SHA256.fullmatch(str(f.get("sha256", ""))):
                    raise BuildError(f"{fw}: sha256 is 64 lower-case hex digits")
                size = f.get("size")
                if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
                    raise BuildError(f"{fw}: size is the file's size in bytes")
                files.append({
                    "platform": str(f["platform"]),
                    "kind": str(f["kind"]),
                    "url": _link(fw, f.get("url"), required=True),
                    "sha256": str(f["sha256"]),
                    "size": size,
                })
            releases.append({
                "version": str(parsed),
                "channel": channel,
                "date": when,
                "severity": severity,
                "summary": _texts(f"{where} summary", rel.get("summary"), required=True),
                "notes_url": _link(f"{where} notes_url", rel.get("notes_url")) or f"{page}#v{parsed}",
                "requires_server": str(requires) if requires is not None else None,
                "fixes": [
                    {"id": str(x["id"]), "severity": str(x.get("severity", "")), "summary": str(x.get("summary", ""))}
                    for x in rel.get("fixes") or []
                ],
                "files": files,
            })
        releases.sort(key=lambda r: release_feed.parse_version(r["version"]), reverse=True)
        series = [
            {"series": str(s["series"]), "supported_until": date.fromisoformat(str(s["supported_until"])).isoformat()}
            for s in raw.get("series") or []
        ]
        apps.append({
            "id": app_id,
            "name": _texts(f"{app_id}.name", raw.get("name"), required=True),
            "description": _texts(f"{app_id}.description", raw.get("description"), required=False),
            "page": page,
            "reports_as": [str(n) for n in raw.get("reports_as") or []],
            "connect": connect,
            "agents": agents,
            "store": _link(f"{app_id}.store", raw.get("store")),
            "store_id": str(raw.get("store_id") or ""),
            "series": series,
            "releases": releases,
        })
    catalogue = {
        "schema": release_feed.SCHEMA,
        "product": release_feed.APPS_PRODUCT,
        "generated_at": datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "apps": apps,
    }
    # What installs will read: every app, release and file must come through.
    read = release_feed.parse_apps(catalogue)
    built = [(a["id"], len(a["releases"]), sum(len(r["files"]) for r in a["releases"])) for a in apps]
    kept = [(a.id, len(a.releases), sum(len(r.files) for r in a.releases)) for a in read.apps]
    if built != kept:
        raise BuildError(f"installs would not read the whole list: built {built}, read {kept}")
    return catalogue


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_build = sub.add_parser("build", help="releases.yaml -> index.json")
    p_build.add_argument("--version", help="the release being published: its entry must exist")
    p_build.add_argument("--out", type=Path, required=True)
    p_build.add_argument("--source", type=Path, default=RELEASES)
    p_sign = sub.add_parser("sign", help="write index.json.sig")
    p_sign.add_argument("folder", type=Path)
    p_sign.add_argument("--key-file", type=Path, help="default: $UPDATE_FEED_SIGNING_KEY")
    p_verify = sub.add_parser("verify", help="read the feed as an install does")
    p_verify.add_argument("folder", type=Path)
    p_keygen = sub.add_parser("keygen", help="a new key pair (private.key, public.txt)")
    p_keygen.add_argument("folder", type=Path)
    p_notes = sub.add_parser("notes", help="the release's summary and highlights, as Markdown for its GitHub release")
    p_notes.add_argument("--version", required=True)
    p_notes.add_argument("--out", type=Path, required=True)
    p_notes.add_argument("--source", type=Path, default=RELEASES)
    p_abuild = sub.add_parser("apps-build", help="apps.yaml -> apps.json (the app list)")
    p_abuild.add_argument("--source", type=Path, required=True)
    p_abuild.add_argument("--out", type=Path, required=True)
    p_asign = sub.add_parser("apps-sign", help="write apps.json.sig")
    p_asign.add_argument("folder", type=Path)
    p_asign.add_argument("--key-file", type=Path, help="default: $APPS_FEED_SIGNING_KEY")
    p_averify = sub.add_parser("apps-verify", help="read the app list as an install does")
    p_averify.add_argument("folder", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.cmd == "build":
            source = yaml.safe_load(args.source.read_text(encoding="utf-8")) or {}
            feed = build(source, version=args.version, agent=agent_version() if args.version else None,
                         today=datetime.now(UTC).date())
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / INDEX).write_text(json.dumps(feed, indent=2) + "\n", encoding="utf-8", newline="\n")
            print(f"{args.out / INDEX}: {len(feed['releases'])} releases, stable {feed['channels']['stable']}")
        elif args.cmd == "sign":
            secret = args.key_file.read_text(encoding="utf-8") if args.key_file else os.environ.get(
                "UPDATE_FEED_SIGNING_KEY", ""
            )
            if not secret.strip():
                raise BuildError("No signing key: set UPDATE_FEED_SIGNING_KEY or pass --key-file")
            signature = sign((args.folder / INDEX).read_bytes(), secret)
            (args.folder / SIG).write_text(json.dumps(signature) + "\n", encoding="utf-8", newline="\n")
            print(f"{args.folder / SIG}: signed with {signature['key_id']}")
        elif args.cmd == "verify":
            feed = release_feed.verify((args.folder / INDEX).read_bytes(), (args.folder / SIG).read_bytes())
            latest = release_feed.offered(feed, "stable")
            print(f"valid: {len(feed.releases)} releases, newest stable {latest[0].version if latest else '-'}")
        elif args.cmd == "notes":
            source = yaml.safe_load(args.source.read_text(encoding="utf-8")) or {}
            feed = build(source, version=args.version, agent=None, today=datetime.now(UTC).date())
            args.out.write_text(notes(feed, args.version), encoding="utf-8", newline="\n")
            print(f"{args.out}: notes for {args.version}")
        elif args.cmd == "apps-build":
            source = yaml.safe_load(args.source.read_text(encoding="utf-8")) or {}
            catalogue = build_apps(source)
            args.out.mkdir(parents=True, exist_ok=True)
            (args.out / APPS_INDEX).write_text(json.dumps(catalogue, indent=2, ensure_ascii=False) + "\n",
                                               encoding="utf-8", newline="\n")
            print(f"{args.out / APPS_INDEX}: {len(catalogue['apps'])} apps")
        elif args.cmd == "apps-sign":
            secret = args.key_file.read_text(encoding="utf-8") if args.key_file else os.environ.get(
                "APPS_FEED_SIGNING_KEY", ""
            )
            if not secret.strip():
                raise BuildError("No signing key: set APPS_FEED_SIGNING_KEY or pass --key-file")
            signature = sign((args.folder / APPS_INDEX).read_bytes(), secret, release_feed.APPS_TRUSTED_KEYS)
            (args.folder / APPS_SIG).write_text(json.dumps(signature) + "\n", encoding="utf-8", newline="\n")
            print(f"{args.folder / APPS_SIG}: signed with {signature['key_id']}")
        elif args.cmd == "apps-verify":
            found = release_feed.verify_apps((args.folder / APPS_INDEX).read_bytes(),
                                             (args.folder / APPS_SIG).read_bytes())
            print(f"valid: {len(found.apps)} apps ({', '.join(a.id for a in found.apps) or 'none'})")
        elif args.cmd == "keygen":
            key = Ed25519PrivateKey.generate()
            raw = key.private_bytes(
                serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
            )
            pub = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
            args.folder.mkdir(parents=True, exist_ok=True)
            private = args.folder / "private.key"
            if private.exists():
                raise BuildError(f"{private} exists: not overwritten")
            private.write_text(base64.b64encode(raw).decode() + "\n", encoding="utf-8")
            (args.folder / "public.txt").write_text(base64.b64encode(pub).decode() + "\n", encoding="utf-8")
            print(f"public key (add to release_feed.TRUSTED_KEYS): {base64.b64encode(pub).decode()}")
    except (BuildError, release_feed.FeedError, OSError, ValueError, KeyError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
