"""Fetch the Mac runtime's mirrored wheels, verify them by sha256, and check the lock.

The build installs PyPI packages by exact version and every other wheel from
one place only: the ``mac-runtime-deps-<n>`` prerelease on fadenstack/fadenstack
(``deps.json``). Nothing comes from a nightly index, a git remote or another
project's release at build time, so a cold build in a year resolves the same.

A local directory (``--from DIR``, e.g. the output of ``collect_deps.py``) is
used first; a file there is accepted only with the pinned sha256, like a
download.

Usage: python fetch_deps.py <dest-dir> [--from DIR] [--lock requirements-lock.txt]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINS = HERE / "deps.json"
LOCK = HERE / "requirements-lock.txt"

#: What a lock line may be: ``name==version`` from PyPI, or a mirrored wheel.
_PYPI = re.compile(r"^[A-Za-z0-9_.-]+==[A-Za-z0-9_.!+-]+$")
_MIRRORED = re.compile(r"^([A-Za-z0-9_.-]+) @ file://@WHEELS@/(\S+\.whl)$")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_lock(lock_text: str) -> list[tuple[int, str, list[str]]]:
    """``(line number, requirement, [sha256, ...])`` per requirement; ``--hash`` continuations folded in."""
    entries: list[tuple[int, str, list[str]]] = []
    logical: list[str] = []
    start = 0
    for number, raw in enumerate(lock_text.splitlines(), 1):
        text = raw.split(" #", 1)[0] if not raw.lstrip().startswith("#") else ""
        text = text.strip()
        if not logical:
            start = number
        if text.endswith("\\"):
            logical.append(text[:-1].strip())
            continue
        logical.append(text)
        parts = " ".join(p for p in logical if p).split()
        logical = []
        if not parts:
            continue
        hashes = [p.split("sha256:", 1)[1] for p in parts if p.startswith("--hash=sha256:")]
        rest = [p for p in parts if not p.startswith("--hash=")]
        entries.append((start, " ".join(rest), hashes))
    return entries


def requirements(lock_text: str) -> list[str]:
    """The lock's requirements without their hashes (what ``uv pip freeze`` prints back)."""
    return [requirement for _, requirement, _ in parse_lock(lock_text)]


def check_lock(lock_text: str, pins: dict, *, require_hashes: bool = True) -> list[str]:
    """Why *lock_text* is not buildable from PyPI pins plus the mirror alone (empty: it is)."""
    wheels = {w["name"]: w for w in pins["wheels"]}
    problems: list[str] = []
    mirrored: set[str] = set()
    for number, line, hashes in parse_lock(lock_text):
        if line.startswith("-"):
            problems.append(f"line {number}: options are not allowed in the lock ({line})")
            continue
        if require_hashes and not hashes:
            problems.append(f"line {number}: {line} has no --hash (run hash_lock.py)")
        if _PYPI.match(line):
            name, version = line.split("==", 1)
            if re.search(r"\.dev\d{8}", version):
                problems.append(f"line {number}: {name} is a nightly ({version}); mirror it")
            continue
        match = _MIRRORED.match(line)
        if not match:
            problems.append(f"line {number}: neither a PyPI pin nor a mirrored wheel: {line}")
            continue
        name, file = match.groups()
        pin = wheels.get(name)
        if pin is None or pin["file"] != file:
            problems.append(f"line {number}: {file} is not pinned in deps.json")
        elif not pin.get("sha256"):
            problems.append(f"line {number}: {file} has no sha256 pin")
        elif require_hashes and hashes and hashes != [pin["sha256"]]:
            problems.append(f"line {number}: {file}'s --hash is not its deps.json pin")
        mirrored.add(name)
    for name in sorted(set(wheels) - mirrored):
        problems.append(f"deps.json pins {name} but the lock does not use it")
    return problems


def asset_url(pins: dict, wheel: dict) -> str:
    return f"https://github.com/{pins['repository']}/releases/download/{pins['release_tag']}/{wheel['asset']}"


def fetch(dest: Path, *, source: Path | None, pins: dict) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    for wheel in pins["wheels"]:
        target = dest / wheel["file"]
        want = wheel["sha256"]
        if target.exists() and sha256(target) == want:
            continue
        local = source / wheel["file"] if source else None
        if local is not None and local.exists():
            shutil.copyfile(local, target)
        else:
            url = asset_url(pins, wheel)
            partial = target.with_suffix(".part")
            with urllib.request.urlopen(url, timeout=600) as response, partial.open("wb") as out:  # noqa: S310 - pinned https origin
                shutil.copyfileobj(response, out)
            partial.replace(target)
        got = sha256(target)
        if got != want:
            target.unlink()
            raise SystemExit(f"{wheel['file']}: sha256 {got}, pinned {want}; refusing it")
        print(f"ok  {want}  {wheel['file']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dest", type=Path)
    parser.add_argument("--from", dest="source", type=Path, default=None)
    parser.add_argument("--lock", type=Path, default=LOCK)
    parser.add_argument("--print-requirements", action="store_true",
                        help="print the lock's requirements without hashes, one per line, and exit")
    args = parser.parse_args()
    if args.print_requirements:
        print("\n".join(requirements(args.lock.read_text())))
        return
    pins = json.loads(PINS.read_text())
    problems = check_lock(args.lock.read_text(), pins)
    if problems:
        raise SystemExit("the lock is not buildable from the mirror alone:\n  " + "\n  ".join(problems))
    fetch(args.dest, source=args.source, pins=pins)


if __name__ == "__main__":
    sys.exit(main())
