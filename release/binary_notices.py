"""The licences of the native libraries a standalone PyInstaller binary bundles.

cli-release.yml builds the ``faden`` binaries on Linux, macOS and Windows. The
Python packages, Python and PyInstaller's bootloader get their texts in the
workflow itself; this script covers what is not Python: the shared libraries
PyInstaller collects from the build machine's Python (OpenSSL, libffi, xz ...)
and the libraries that Python's extension modules carry statically.

    python release/binary_notices.py list BINARY
    python release/binary_notices.py append BINARY NOTICES
    python release/binary_notices.py check BINARY NOTICES

``list`` prints what the binary bundles and the licence each part needs.
``append`` adds that as a section to NOTICES. ``check`` fails unless NOTICES
holds every one of those texts. All three fail when a bundled library has no
licence text, so a Python or PyInstaller upgrade that pulls in a new library
stops the release instead of shipping it without notice.

The workflow builds twice: once to learn what gets bundled, then again with
NOTICES inside the binary. BINARY is read, not run, so it can be any platform's.

Where the text comes from:

- Linux: the Debian package that owns the library (``dpkg -S``), whose
  ``/usr/share/doc/<package>/copyright`` the build machine has installed.
- macOS and Windows: the build machine has no such database, so the texts are
  tracked files in ``release/licenses/``, each starting with its source URL.
  A bundled library that no rule here maps to one of them fails the build.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

LICENSES = Path(__file__).resolve().parent / "licenses"
HEADING = "4. Native libraries"

# Libraries whose licence a binary under Apache-2.0 should not carry unasked.
# libreadline (GPL-3.0-or-later) once got into the agent through the standard
# library's own imports; the CLI build excludes readline for the same reason.
# Fails even when the library has a copyright file: this needs a human.
FORBIDDEN = re.compile(r"^(libreadline|libgdbm)", re.IGNORECASE)

# Files that are Python itself (the CPython licence is part 2 of the notices).
_PYTHON = re.compile(r"^(libpython[\d.]*\.(so[.\d]*|dylib)|python\d*\.dll|Python)$")

# Libraries that need a text from release/licenses/ (macOS, Windows): the
# file's name without a platform, pattern first. Checked against the lowercased
# top-level file name of the binary's archive.
_LIBRARY_LICENCES: tuple[tuple[str, str], ...] = (
    (r"lib(ssl|crypto)[-.\d]*(-x64)?\.(dll|dylib)", "openssl"),
    (r"libffi[-.\d]*\.(dll|dylib)", "libffi"),
    (r"(lib)?sqlite3[-.\d]*\.(dll|dylib)", "sqlite"),
    (r"(lib)?(tcl|tk)[\d.]*t?\.(dll|dylib)", "tcl-tk"),
    (r"(zlib1\.dll|libz[.\d]*\.dylib)", "zlib"),
    (r"liblzma[.\d]*\.dylib", "xz"),
    (r"libbz2[.\d]*\.dylib", "bzip2"),
    (r"lib(ncurses|ncursesw|panel|panelw|form|formw|menu|menuw)[.\d]*\.dylib", "ncurses"),
    (r"libmpdec[.\d]*\.dylib", "libmpdec"),
    (r"libexpat[.\d]*\.dylib", "expat"),
    # Microsoft's C and C++ runtime and the Universal C Runtime.
    (r"(vcruntime140(_1)?|msvcp140(_\w+)?|concrt140|vcomp140|ucrtbase)\.dll", "microsoft-runtime"),
    (r"api-ms-win-[\w-]+\.dll", "microsoft-runtime"),
)

# Libraries that Python's extension modules carry inside them. The module being
# in the binary is the sign. {module: [(platforms, licence)]}.
#
# - pyexpat, _decimal: CPython's own tree has expat and libmpdec.
# - python.org's macOS build links xz and SQLite statically (Mac/BuildScript/
#   build-installer.py: --enable-static --disable-shared); its OpenSSL, ncurses
#   and Tcl/Tk are dylibs, which _LIBRARY_LICENCES catches.
# - python.org's Windows build links bzip2 and xz into the .pyd files and zlib
#   into python3NN.dll; its OpenSSL, libffi, SQLite and Tcl/Tk are DLLs.
# - On Linux every one of these comes from a Debian package, found by dpkg.
_STATIC: dict[str, tuple[tuple[frozenset[str], str], ...]] = {
    "pyexpat": ((frozenset({"linux", "darwin", "win32"}), "expat"),),
    "_decimal": ((frozenset({"linux", "darwin", "win32"}), "libmpdec"),),
    "_lzma": ((frozenset({"darwin", "win32"}), "xz"),),
    "_bz2": ((frozenset({"win32"}), "bzip2"),),
    "_sqlite3": ((frozenset({"darwin"}), "sqlite"),),
    "zlib": ((frozenset({"win32"}), "zlib"),),
}


@dataclass
class Bundled:
    """What a binary bundles, as the sources of the texts it needs."""

    platform: str
    # Licence file stem -> what in the binary needs it.
    licences: dict[str, list[str]] = field(default_factory=dict)
    # Debian package -> libraries from it.
    packages: dict[str, list[str]] = field(default_factory=dict)
    # Libraries nothing maps to a text, and libraries that must not be there.
    unmapped: list[str] = field(default_factory=list)
    forbidden: list[str] = field(default_factory=list)


def platform_of(binary_names: list[str]) -> str:
    """The platform a binary was built for, from the files in its archive."""
    for name in binary_names:
        if name.lower().endswith((".dll", ".pyd")):
            return "win32"
    for name in binary_names:
        if name.endswith(".dylib") or re.search(r"\.cpython-\d+-darwin\.so$", name):
            return "darwin"
    return "linux"


def archive_names(binary: Path) -> list[str]:
    """The names in a PyInstaller one-file binary's archive, ``/`` separated."""
    from PyInstaller.archive.readers import CArchiveReader

    return sorted(name.replace("\\", "/") for name in CArchiveReader(str(binary)).toc)


def dpkg_owner(library: str) -> str | None:
    """The Debian package that owns a shared library, by file name."""
    found = subprocess.run(
        ["dpkg", "--search", f"*/{library}"], capture_output=True, encoding="utf-8"
    )
    for line in found.stdout.splitlines():
        owner, _, path = line.partition(": ")
        if path.endswith(f"/{library}"):
            return owner.split(":")[0]
    return None


def classify(names: list[str], owner=dpkg_owner, platform: str | None = None) -> Bundled:
    """Which texts the files in a binary's archive need.

    Only top-level files count as the system's libraries: what a wheel vendors
    (``cryptography/...``, ``psutil/...``) sits under its package, and the
    package's own licence covers it. ``owner`` maps a Linux library to its
    Debian package.
    """
    platform = platform or platform_of(names)
    found = Bundled(platform)
    top = [name for name in names if "/" not in name]

    def need(licence: str, what: str) -> None:
        found.licences.setdefault(licence, []).append(what)

    for name in top:
        if _PYTHON.match(name):
            continue
        low = name.lower()
        if platform == "linux":
            if not re.match(r"lib.+\.so(\.|$)", name):
                continue
            package = owner(name)
            if package is None:
                found.unmapped.append(f"{name} (no Debian package owns it)")
            elif FORBIDDEN.match(package):
                found.forbidden.append(f"{name} (from {package})")
            else:
                found.packages.setdefault(package, []).append(name)
            continue
        if not low.endswith((".dll", ".dylib")):
            continue
        for pattern, licence in _LIBRARY_LICENCES:
            if re.fullmatch(pattern, low):
                need(licence, "api-ms-win-*.dll" if low.startswith("api-ms-win-") else name)
                break
        else:
            found.unmapped.append(f"{name} (no licence text in release/licenses/ is mapped to it)")

    # Extension modules: the stdlib's sit in lib-dynload/ (Linux), at the top
    # (Windows, macOS). Those under a package belong to the package.
    modules = {}
    for name in names:
        folder, _, base = name.rpartition("/")
        if folder in ("", "lib-dynload") or folder.endswith("/lib-dynload"):
            modules[base.split(".")[0]] = base
    if any(re.fullmatch(r"python3\d+\.dll", name.lower()) for name in top):
        modules["zlib"] = "python3NN.dll"  # not a file of its own: built into the DLL
    for module, rules in _STATIC.items():
        for platforms, licence in rules:
            if platform in platforms and module in modules:
                need(licence, f"linked into {modules[module]}")
    for licence in found.licences:
        if not (LICENSES / f"{licence}.txt").is_file():
            found.unmapped.append(f"{licence} (release/licenses/{licence}.txt is missing)")
    return found


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n").rstrip() + "\n"


def _debian_version(package: str) -> str:
    return subprocess.run(
        ["dpkg-query", "--show", "--showformat=${Version}", package],
        check=True,
        capture_output=True,
        encoding="utf-8",
    ).stdout


def blocks(found: Bundled) -> list[str]:
    """The text of each licence, headed by what needs it. Order is stable."""
    out = []
    for package in sorted(found.packages):
        copyright_file = Path("/usr/share/doc", package, "copyright")
        if not copyright_file.is_file():
            raise SystemExit(f"{package} has no copyright file at {copyright_file}")
        files = ", ".join(sorted(found.packages[package]))
        out.append(f"--- {package} {_debian_version(package)} ({files}) ---\n\n{_read(copyright_file)}")
    for licence in sorted(found.licences):
        needs = ", ".join(sorted(set(found.licences[licence])))
        out.append(f"--- {licence} ({needs}) ---\n\n{_read(LICENSES / f'{licence}.txt')}")
    return out


def _fail_on_problems(found: Bundled) -> None:
    problems = [f"must not be bundled: {item}" for item in found.forbidden]
    problems += [f"has no licence text: {item}" for item in found.unmapped]
    if problems:
        sys.exit(
            "The binary bundles native libraries the notices cannot cover:\n  "
            + "\n  ".join(problems)
            + "\nExclude the module that pulls one in, or map it to a text: a rule in"
            "\nrelease/binary_notices.py and the text in release/licenses/<name>.txt"
            "\n(Linux: the Debian package needs a copyright file)."
        )


def intro(found: Bundled) -> str:
    where = (
        "Shared libraries from the Debian-based build environment, which Python's\n"
        "extension modules link against. Their Debian copyright files follow."
        if found.platform == "linux"
        else "Libraries from the Python distribution the executable was built with, as\n"
        "shared libraries or linked into its extension modules. Their licences follow."
    )
    return f"\n\n{HEADING}\n{'=' * len(HEADING)}\n\n{where}\n"


def section(found: Bundled) -> str:
    return intro(found) + "".join("\n" + block for block in blocks(found))


def report(found: Bundled) -> str:
    lines = [f"platform: {found.platform}"]
    lines += [f"  {package}: {', '.join(libs)}" for package, libs in sorted(found.packages.items())]
    lines += [f"  {lic}: {', '.join(sorted(set(why)))}" for lic, why in sorted(found.licences.items())]
    lines += [f"  FORBIDDEN {item}" for item in found.forbidden]
    lines += [f"  UNMAPPED {item}" for item in found.unmapped]
    return "\n".join(lines)


def append(binary: Path, notices: Path) -> None:
    found = classify(archive_names(binary))
    print(report(found))
    _fail_on_problems(found)
    text = notices.read_text(encoding="utf-8")
    if HEADING in text:
        sys.exit(f"{notices} already has the section {HEADING!r}")
    notices.write_text(text.rstrip() + "\n" + section(found), encoding="utf-8")
    print(f"Added {len(found.packages) + len(found.licences)} licence texts to {notices}")


def check(binary: Path, notices: Path) -> None:
    found = classify(archive_names(binary))
    _fail_on_problems(found)
    text = notices.read_text(encoding="utf-8")
    missing = [block.splitlines()[0] for block in blocks(found) if block not in text]
    if missing:
        sys.exit("The notices lack the licence of:\n  " + "\n  ".join(missing))
    print(f"{binary}: the notices cover all {len(found.packages) + len(found.licences)} bundled native licences")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="Print what the binary bundles").add_argument("binary", type=Path)
    for name, helptext in (("append", "Add the section to the notices"), ("check", "Check the notices")):
        command = sub.add_parser(name, help=helptext)
        command.add_argument("binary", type=Path)
        command.add_argument("notices", type=Path)
    args = parser.parse_args()

    if args.command == "list":
        found = classify(archive_names(args.binary))
        print(report(found))
        _fail_on_problems(found)
    elif args.command == "append":
        append(args.binary, args.notices)
    else:
        check(args.binary, args.notices)


if __name__ == "__main__":
    main()
