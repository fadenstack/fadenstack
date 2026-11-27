"""Pin the node agent build a core release hands out, and check the pin.

The install plan (backend/faden_backend/web/api/install) tells every new machine
which SHA-256 the agent binary must have; that comes from agent_release.json, a
tracked file naming the agent version and the digest of each architecture's
binary. The digests are read from the ``.sha256`` files of the
``node-agent-v<version>`` GitHub release (.github/workflows/node-agent-release.yml).

    python release/pin_agent.py write    # when cutting a core release: rewrites the file
    python release/pin_agent.py check    # CI: the file matches the published release

``write`` takes the version from AGENT_VERSION in the backend. ``check`` fails if
the file's version differs from it, or any digest differs from the release's.

Every build in BUILDS must be in the release; the Mac build from
FIRST_MAC_AGENT_VERSION on (the releases before it have none, and a core
release still pinning one of them must pass). The Mac pkg (``macos-arm64-pkg``,
the notarized ``faden-agent-macos-arm64.pkg``) is pinned when the release has
one: a release built without the Apple signing secrets has none.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

INSTALL = Path(__file__).resolve().parents[1] / "backend" / "faden_backend" / "web" / "api" / "install"
PIN = INSTALL / "agent_release.json"
BUILDS = ("linux-x86_64", "linux-aarch64", "macos-arm64")
#: The first node-agent release with a Mac build. Older ones are pinned without
#: one: their releases have no faden-agent-macos-arm64 to pin.
FIRST_MAC_AGENT_VERSION = "0.3.0"
MAC_BUILDS = frozenset({"macos-arm64"})
#: Pinned when the release carries them, under the key, from the file named.
OPTIONAL = {"macos-arm64-pkg": "macos-arm64.pkg"}
URL = "https://github.com/fadenstack/fadenstack/releases/download/node-agent-v{version}/faden-agent-{build}.sha256"
HEX64 = re.compile(r"^[0-9a-f]{64}$")
NOT_FOUND = 404


def backend_version() -> str:
    match = re.search(r'^AGENT_VERSION = "([^"]+)"', (INSTALL / "__init__.py").read_text("utf-8"), re.M)
    if not match:
        sys.exit("AGENT_VERSION not found in the backend")
    return match.group(1)


def _release(version: str) -> tuple[int, ...]:
    """*version*'s release numbers, for ordering (``0.3.0`` -> ``(0, 3, 0)``); a suffix is ignored."""
    match = re.match(r"^(\d+(?:\.\d+)*)", version)
    if not match:
        sys.exit(f"{version!r} is not an agent version")
    return tuple(int(part) for part in match.group(1).split("."))


def builds_for(version: str) -> tuple[str, ...]:
    """The builds the ``node-agent-v<version>`` release must carry."""
    if _release(version) >= _release(FIRST_MAC_AGENT_VERSION):
        return BUILDS
    return tuple(build for build in BUILDS if build not in MAC_BUILDS)


def _fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as resp:  # noqa: S310
        return resp.read().decode()


def _digest(key: str, text: str) -> str:
    words = text.split()
    digest = words[0].lower() if words else ""
    if not HEX64.match(digest):
        sys.exit(f"{key}: the release's .sha256 is not 64 hex characters")
    return digest


def published(version: str) -> dict[str, str]:
    """The digest of each build in the ``node-agent-v<version>`` release, keyed as the pin keys them."""
    digests: dict[str, str] = {}
    for build in builds_for(version):
        try:
            text = _fetch(URL.format(version=version, build=build))
        except urllib.error.HTTPError as exc:
            if exc.code == NOT_FOUND:
                sys.exit(f"{build}: node-agent-v{version} has no faden-agent-{build}.sha256")
            raise
        digests[build] = _digest(build, text)
    for key, name in OPTIONAL.items():
        if _release(version) < _release(FIRST_MAC_AGENT_VERSION):
            break  # Mac files only
        try:
            text = _fetch(URL.format(version=version, build=name))
        except urllib.error.HTTPError as exc:
            if exc.code == NOT_FOUND:
                continue  # not in this release; anything else is an error
            raise
        digests[key] = _digest(key, text)
    return digests


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("write", "check"))
    command = parser.parse_args().command
    version = backend_version()
    fresh = {"version": version, "sha256": published(version)}
    if command == "write":
        PIN.write_text(json.dumps(fresh, indent=2) + "\n", encoding="utf-8")
        print(f"pinned agent {version}")
        return
    if json.loads(PIN.read_text("utf-8")) != fresh:
        sys.exit(f"{PIN.name} does not match the node-agent-v{version} release; run release/pin_agent.py write")
    print(f"agent {version} pin matches the release")


if __name__ == "__main__":
    main()
