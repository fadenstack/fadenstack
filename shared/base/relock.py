"""Regenerate requirements.txt, the base image's locked requirements, from requirements.in.

    python shared/base/relock.py            # needs uv on PATH; uses the network
    python shared/base/relock.py --check    # exit 1 when requirements.txt is not what this would write

The base layer is installed before each service's `uv sync --locked`, so the closer it is to what
the services' uv.lock files pin, the less that sync has to change. So the resolution is held to the
versions the services already agree on: for every package that one of them pins, the version most
of them pin (the highest, on a tie). Packages no service pins resolve to the newest release that
fits requirements.in. The result carries sha256 hashes for every platform the image is built for
(--universal), and the Dockerfile installs it with --require-hashes.

Dependabot does not update this file (pip-compile files with hashes are not its target): rerun this
after the services' locks move, and when requirements.in changes.
"""

from __future__ import annotations

import re
import subprocess
import sys
import tempfile
import tomllib
from collections import Counter
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SERVICES = ("backend", "gateway", "mcp", "pii", "skills")
PYTHON = "3.13"  # the base image's Python (PYTHON_BASE in the Dockerfile)


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _version_key(version: str) -> list[tuple[int, int | str]]:
    return [(0, int(part)) if part.isdigit() else (1, part) for part in re.split(r"[.\-+]", version)]


def _pinned_by_services() -> dict[str, list[str]]:
    """Every version of each package across the services' lock files, once per service."""
    versions: dict[str, list[str]] = {}
    for service in SERVICES:
        lock = tomllib.loads((ROOT / service / "uv.lock").read_text(encoding="utf-8"))
        for package in lock["package"]:
            if "version" in package:
                versions.setdefault(_norm(package["name"]), []).extend(sorted({package["version"]}))
    return versions


def _constraints() -> list[str]:
    lines = []
    for name, found in sorted(_pinned_by_services().items()):
        count = Counter(found)
        most = max(count.values())
        lines.append(f"{name}=={max((v for v, c in count.items() if c == most), key=_version_key)}")
    return lines


def _compile(output: Path) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        constraints = Path(tmp) / "constraints.txt"
        constraints.write_text("\n".join(_constraints()) + "\n", encoding="utf-8", newline="\n")
        subprocess.run(
            [
                "uv", "pip", "compile", "requirements.in",
                "--universal", "--python-version", PYTHON, "--generate-hashes",
                "--constraint", str(constraints),
                "--custom-compile-command", "python shared/base/relock.py",
                "--quiet", "--no-annotate", "--output-file", str(output),
            ],
            cwd=HERE,
            check=True,
        )


def main() -> int:
    target = HERE / "requirements.txt"
    if "--check" in sys.argv:
        with tempfile.TemporaryDirectory() as tmp:
            fresh = Path(tmp) / "requirements.txt"
            _compile(fresh)
            same = fresh.read_bytes() == target.read_bytes()
        print("requirements.txt is current" if same else "requirements.txt is stale: run shared/base/relock.py")
        return 0 if same else 1
    _compile(target)
    print(f"Wrote {target.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
