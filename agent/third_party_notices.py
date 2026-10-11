"""Write and check THIRD-PARTY-NOTICES.txt for the standalone agent binary.

The binary is more than the agent: it carries a Python, the packages uv.lock
pins, the system libraries Python's extension modules link against, and
PyInstaller's bootloader. Their licences ask for their text to travel with
them; LICENSE and NOTICE cover only our own code.

build-binary.sh runs this inside the build container:

    python third_party_notices.py write REQUIREMENTS OUT --pip-licenses PATH

        after the locked packages are installed and before PyInstaller runs,
        so the file goes into the binary (``faden-agent license
        --third-party``) and next to it on the release;

    python third_party_notices.py check BINARY REQUIREMENTS

        after PyInstaller: fails the build when the binary carries a system
        library from a Debian package, or a Python package, the notices do
        not cover. That is how libreadline (GPL-3.0-or-later) once got in --
        through the standard library's own imports, unasked -- and setuptools,
        from the build environment.

REQUIREMENTS is the agent's runtime requirements as ``uv export`` writes
them, and only those packages are listed: PyInstaller, uv and pip-licenses
build the binary but are not in it. pip-licenses runs from a venv of its own
and reads this interpreter's packages, so its own dependencies stay out too.

Linux (Debian) only: the system libraries come from dpkg's database.
"""

from __future__ import annotations

import argparse
import os
import platform
import re
import subprocess
import sys
import sysconfig
from importlib import metadata
from pathlib import Path

# The Debian packages whose libraries the binary carries. `check` keeps this
# honest: a library from any other package fails the build.
DEBIAN_PACKAGES = (
    "libssl3",
    "zlib1g",
    "liblzma5",
    "libbz2-1.0",
    "libuuid1",
    "libsqlite3-0",
    "libgcc-s1",
    "libffi8",
)

_LICENCE_FILE = re.compile(r"LICEN[CS]E|COPYING|NOTICE|AUTHORS", re.IGNORECASE)

_INTRO = """\
Third-party software in the faden-agent binary
==============================================

faden-agent itself is licensed under the Apache License, Version 2.0; its
LICENSE and NOTICE are printed by `faden-agent license`. The binary also
contains the software below, each part under its own licence:

  1. Python packages
  2. Python
  3. System libraries
  4. PyInstaller's bootloader
"""


def _heading(title: str) -> str:
    return f"\n\n{title}\n{'=' * len(title)}\n\n"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").rstrip() + "\n"


def _requirement_names(requirements: Path) -> list[str]:
    """The package names in a ``uv export`` file: ``name==version ; marker``."""
    names = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        match = re.match(r"([A-Za-z0-9][A-Za-z0-9._-]*)==", line)
        if match:
            names.append(match.group(1))
    return names


def _licence_files(dist: metadata.Distribution) -> list[Path]:
    return sorted(
        (
            Path(str(dist.locate_file(file)))
            for file in dist.files or ()
            if file.parts[0].endswith(".dist-info") and _LICENCE_FILE.search(file.name)
        ),
        key=lambda path: path.name,
    )


def write(requirements: Path, out: Path, pip_licenses: str) -> None:
    names = _requirement_names(requirements)
    if not names:
        sys.exit(f"No packages in {requirements}")

    listing = subprocess.run(
        [
            pip_licenses,
            f"--python={sys.executable}",
            "--from=mixed",
            "--with-license-file",
            "--with-notice-file",
            "--no-license-path",
            "--with-urls",
            "--format=plain-vertical",
            # Else it leaves out its own dependencies (wcwidth, prettytable)
            # and pip's, whoever else needs them: the list is ours already.
            "--with-system",
            "--packages",
            *names,
        ],
        check=True,
        capture_output=True,
        encoding="utf-8",
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    ).stdout.rstrip()
    parts = [_INTRO, _heading("1. Python packages"), listing + "\n"]

    # pip-licenses prints one licence file per package. A few ship several:
    # cryptography and packaging are "Apache-2.0 OR BSD", and the LICENSE it
    # picks only says to read the other two.
    for name in names:
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:  # a marker left it out here
            continue
        if f"\n{dist.metadata['Name']}\n{dist.version}\n" not in f"\n{listing}\n":
            sys.exit(f"pip-licenses left out {dist.metadata['Name']} {dist.version}")
        files = _licence_files(dist)
        if len(files) < 2:
            continue
        for path in files:
            parts.append(f"\n--- {dist.metadata['Name']} {dist.version}: {path.name} ---\n\n")
            parts.append(_read(path))

    parts.append(_heading(f"2. Python {platform.python_version()}"))
    parts.append("The interpreter and its standard library.\n\n")
    parts.append(_read(Path(sysconfig.get_path("stdlib")) / "LICENSE.txt"))

    parts.append(_heading("3. System libraries"))
    parts.append(
        "Shared libraries from the Debian build environment, which Python's\n"
        "extension modules link against. Their Debian copyright files follow.\n"
    )
    for package in DEBIAN_PACKAGES:
        version = subprocess.run(
            ["dpkg-query", "--show", "--showformat=${Version}", package],
            check=True,
            capture_output=True,
            encoding="utf-8",
        ).stdout
        parts.append(f"\n--- {package} {version} ---\n\n")
        parts.append(_read(Path("/usr/share/doc", package, "copyright")))

    pyinstaller = metadata.distribution("pyinstaller")
    parts.append(_heading("4. PyInstaller's bootloader"))
    parts.append(
        f"The binary starts through the bootloader of PyInstaller {pyinstaller.version},\n"
        "stock and unmodified. It is licensed under the GNU General Public License,\n"
        "version 2 or later, with the bootloader exception, which permits distributing\n"
        "it embedded in another program under that program's terms. PyInstaller's\n"
        "licence follows.\n\n"
    )
    for path in _licence_files(pyinstaller):
        parts.append(_read(path))

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("".join(parts), encoding="utf-8")
    print(f"Wrote {out} ({len(names)} packages)")


def _debian_package(library: str) -> str | None:
    found = subprocess.run(
        ["dpkg", "--search", f"*/{library}"], capture_output=True, encoding="utf-8"
    )
    for line in found.stdout.splitlines():
        owner, _, path = line.partition(": ")
        if path.endswith(f"/{library}"):
            return owner.split(":")[0]
    return None


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def check(binary: Path, requirements: Path) -> None:
    from PyInstaller.archive.readers import CArchiveReader

    archive = CArchiveReader(str(binary))
    uncovered = []

    # System libraries sit at the top level; extension modules and what
    # wheels vendor sit under their package, covered by its licence.
    for name in sorted(archive.toc):
        if "/" in name or not re.match(r"lib.+\.so(\.|$)", name):
            continue
        if name.startswith("libpython"):
            continue  # Python's own: part 2
        package = _debian_package(name)
        if package not in DEBIAN_PACKAGES:
            uncovered.append(f"{name} (from {package or 'no Debian package'})")

    # Python code: the standard library, the agent, PyInstaller's run-time
    # modules (part 4), or a package the notices list. Anything else got in
    # through the build environment, as setuptools once did.
    listed = {_normalise(name) for name in _requirement_names(requirements)}
    owners = metadata.packages_distributions()
    modules = set(archive.open_embedded_archive("PYZ.pyz").toc)
    modules |= {name.split(".")[0] for name in archive.toc if "/" not in name and ".cpython-" in name}
    for top in sorted({module.split(".")[0] for module in modules}):
        if (
            top in sys.stdlib_module_names
            or top.startswith(("_sysconfigdata", "pyimod", "_pyi_", "pyi_"))
            or top == "faden_agent"
        ):
            continue
        dists = owners.get(top, [])
        if not any(_normalise(dist) in listed for dist in dists):
            uncovered.append(f"Python package {top} (from {', '.join(dists) or 'no distribution'})")

    if uncovered:
        sys.exit(
            "The binary carries software its third-party notices do not cover:\n  "
            + "\n  ".join(uncovered)
            + "\nExclude the module that pulls it in (node_agent.spec), or add it to"
            "\nthe notices: a runtime dependency in pyproject.toml, or a Debian"
            "\npackage in DEBIAN_PACKAGES (third_party_notices.py)."
        )
    print(f"{binary}: everything bundled is covered by the notices")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    p_write = sub.add_parser("write", help="Write the notices")
    p_write.add_argument("requirements", type=Path)
    p_write.add_argument("out", type=Path)
    p_write.add_argument("--pip-licenses", default="pip-licenses")
    p_check = sub.add_parser("check", help="Check a built binary against the notices")
    p_check.add_argument("binary", type=Path)
    p_check.add_argument("requirements", type=Path)
    args = parser.parse_args()

    if args.command == "write":
        write(args.requirements, args.out, args.pip_licenses)
    else:
        check(args.binary, args.requirements)


if __name__ == "__main__":
    main()
