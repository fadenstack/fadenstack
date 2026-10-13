"""Build, sign and check the release feed installs read to learn about new releases.

    python release/update_feed.py build --version 0.4.2 --out dist/feed
    UPDATE_FEED_SIGNING_KEY=... python release/update_feed.py sign dist/feed
    python release/update_feed.py verify dist/feed
    python release/update_feed.py keygen <folder>        # a new key pair, for a key change
    python release/update_feed.py notes --version 0.4.2 --out notes.md   # the GitHub release's text

``build`` turns release/releases.yaml into ``index.json``; ``sign`` writes ``index.json.sig`` with
the private key (raw Ed25519, base64: the secret UPDATE_FEED_SIGNING_KEY, or --key-file) and
refuses a key the CLI does not ship; ``verify`` reads the result the way an install does.

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


def sign(index: bytes, private_key_b64: str) -> dict:
    """The signature file for *index*, naming the trusted key the private key belongs to."""
    key = Ed25519PrivateKey.from_private_bytes(base64.b64decode(private_key_b64.strip()))
    public = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    key_id = next((kid for kid, pub in release_feed.TRUSTED_KEYS.items() if pub == public), None)
    if key_id is None:
        raise BuildError(
            "This signing key is not one the CLI and backend trust (release_feed.TRUSTED_KEYS): "
            f"installs would refuse the feed. Its public key: {public}"
        )
    return {"key_id": key_id, "signature": base64.b64encode(key.sign(index)).decode()}


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
