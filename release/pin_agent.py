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
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from pathlib import Path

INSTALL = Path(__file__).resolve().parents[1] / "backend" / "faden_backend" / "web" / "api" / "install"
PIN = INSTALL / "agent_release.json"
BUILDS = ("linux-x86_64", "linux-aarch64")
URL = "https://github.com/fadenstack/fadenstack/releases/download/node-agent-v{version}/faden-agent-{build}.sha256"
HEX64 = re.compile(r"^[0-9a-f]{64}$")


def backend_version() -> str:
    match = re.search(r'^AGENT_VERSION = "([^"]+)"', (INSTALL / "__init__.py").read_text("utf-8"), re.M)
    if not match:
        sys.exit("AGENT_VERSION not found in the backend")
    return match.group(1)


def published(version: str) -> dict[str, str]:
    digests: dict[str, str] = {}
    for build in BUILDS:
        with urllib.request.urlopen(URL.format(version=version, build=build), timeout=30) as resp:  # noqa: S310
            words = resp.read().decode().split()
        digest = words[0].lower() if words else ""
        if not HEX64.match(digest):
            sys.exit(f"{build}: the release's .sha256 is not 64 hex characters")
        digests[build] = digest
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
