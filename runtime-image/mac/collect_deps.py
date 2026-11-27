"""Collect the Mac runtime's off-PyPI wheels into one directory, for the mirror release.

The runtime lock installs everything from PyPI by exact version, except the
wheels listed in ``deps.json``: PyTorch nightlies (the index drops them after a
few months), vLLM's own GitHub release wheel, two packages pinned to a git
commit, and the vllm-metal wheel certified in Gate 0d. A cold build must not
depend on any of those origins, so they are mirrored once as release assets
(``publish_deps.sh``) and the build fetches only the mirror, by sha256.

Each entry in ``deps.json`` says how its wheel is obtained:

  ``url``    downloaded from its upstream URL (byte-identical upstream file)
  ``git``    built with ``uv build --wheel`` at the pinned commit, with
             SOURCE_DATE_EPOCH set from the commit, so a rebuild is the same file
  ``local``  copied from a local path (the certified vllm-metal wheel)

Usage:
  python collect_deps.py <out-dir>            collect, verify against the pins
  python collect_deps.py <out-dir> --pin      collect and write the sha256 pins into deps.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINS = HERE / "deps.json"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _download(url: str, dest: Path) -> None:
    with urllib.request.urlopen(url, timeout=600) as response, dest.open("wb") as out:  # noqa: S310 - pinned https origins
        shutil.copyfileobj(response, out)


def _build_from_git(repo: str, commit: str, dest_dir: Path) -> Path:
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "src"
        # A full clone with tags: setuptools-scm derives the version
        # (``0.32.3.dev12+g4cfc647``) from the nearest tag and the commits since.
        subprocess.run(["git", "clone", "-q", repo, str(src)], check=True)
        subprocess.run(["git", "-C", str(src), "checkout", "-q", commit], check=True)
        epoch = subprocess.run(
            ["git", "-C", str(src), "log", "-1", "--format=%ct"], check=True, capture_output=True, text=True,
        ).stdout.strip()
        out = Path(tmp) / "dist"
        env = {**os.environ, "SOURCE_DATE_EPOCH": epoch}
        subprocess.run(["uv", "build", "--wheel", "--out-dir", str(out), str(src)], check=True, env=env)
        wheels = list(out.glob("*.whl"))
        if len(wheels) != 1:
            raise SystemExit(f"{repo}@{commit}: expected one wheel, got {wheels}")
        target = dest_dir / wheels[0].name
        shutil.move(str(wheels[0]), target)
        return target


def collect(out: Path, *, pin: bool) -> list[dict]:
    pins = json.loads(PINS.read_text())
    out.mkdir(parents=True, exist_ok=True)
    problems = []
    for dep in pins["wheels"]:
        target = out / dep["file"]
        source = dep["source"]
        if not target.exists():
            if source["kind"] == "url":
                _download(source["url"], target)
            elif source["kind"] == "git":
                built = _build_from_git(source["repo"], source["commit"], out)
                if built.name != dep["file"]:
                    raise SystemExit(f"{dep['name']}: built {built.name}, expected {dep['file']}")
            elif source["kind"] == "local":
                shutil.copyfile(source["path"], target)
            else:
                raise SystemExit(f"{dep['name']}: unknown source kind {source['kind']!r}")
        digest = sha256(target)
        if pin:
            dep["sha256"] = digest
        elif dep.get("sha256") != digest:
            problems.append(f"{dep['file']}: sha256 {digest}, pinned {dep.get('sha256')}")
        print(f"{digest}  {dep['file']}")
    if pin:
        PINS.write_text(json.dumps(pins, indent=2) + "\n")
    if problems:
        raise SystemExit("not the pinned files:\n  " + "\n  ".join(problems))
    return pins["wheels"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("out", type=Path)
    parser.add_argument("--pin", action="store_true", help="write the collected files' sha256 into deps.json")
    args = parser.parse_args()
    collect(args.out, pin=args.pin)


if __name__ == "__main__":
    sys.exit(main())
