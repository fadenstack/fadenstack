# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for faden-agent single-file executable.

Build:
    pyinstaller node_agent.spec

Output:
    dist/faden-agent       (Linux/macOS)
    dist/faden-agent.exe   (Windows)
"""

import sys
from pathlib import Path

block_cipher = None

HERE = Path(SPECPATH)

# Read the identity from pyproject rather than restating it.
#
# A version typed in two places is a version that disagrees with itself, and
# the one embedded in a shipped binary is the copy people report bugs
# against.
try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11 running the build
    import tomli as tomllib

_PROJECT = tomllib.loads((HERE / "pyproject.toml").read_text(encoding="utf-8"))["project"]
VERSION = _PROJECT["version"]
DESCRIPTION = _PROJECT["description"]
COMPANY = _PROJECT["authors"][0]["name"]
COPYRIGHT = f"Copyright 2026 {COMPANY} and contributors. Apache-2.0."

# The licence travels with the binary.
#
# The agent ships its Apache-2.0 LICENSE and NOTICE with every binary, and
# the licences of everything bundled with it: build-binary.sh writes
# build/THIRD-PARTY-NOTICES.txt (third_party_notices.py) before this runs.
# `faden-agent license` prints the first two, `--third-party` the third.
_LICENCE_FILES = [
    (str(path), ".")
    for path in (HERE / "LICENSE", HERE / "NOTICE", HERE / "build" / "THIRD-PARTY-NOTICES.txt")
    if path.is_file()
]

# Downloading a model on the machine (hf_download.py). huggingface_hub loads
# its submodules lazily, by name, so the analysis cannot follow them; and it
# asks the installed packages' metadata whether hf-xet is there -- without
# that metadata in the binary it would say no and download over plain HTTP,
# one connection per file, many times slower.
from PyInstaller.utils.hooks import collect_submodules, copy_metadata

_HF_DATAS = copy_metadata("huggingface_hub")
try:
    _HF_DATAS += copy_metadata("hf_xet")
    _HF_IMPORTS = ["hf_xet"]
except Exception:  # a platform hf-xet has no wheel for: plain HTTP it is
    _HF_IMPORTS = []
_HF_IMPORTS += collect_submodules("huggingface_hub")

# The Serve drain patch is copied into the runtime container as source
# (ray/drain.py reads it beside its module): a one-file binary holds only
# bytecode, so the file travels as data, where its module expects it.
_SOURCE_DATAS = [(str(HERE / "faden_agent" / "ray" / "serve_drain_patch.py"), "faden_agent/ray")]

a = Analysis(
    ["faden_agent/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=_LICENCE_FILES + _HF_DATAS + _SOURCE_DATAS,
    hiddenimports=_HF_IMPORTS + [
        # psutil platform backends — PyInstaller misses these on cross-collect
        "psutil._pslinux",
        "psutil._pswindows",
        "psutil._psosx",
        "psutil._psposix",
        # httpx transport
        "httpcore",
        "httpcore._async",
        "httpcore._sync",
        "h11",
        "certifi",
        "anyio",
        "anyio._backends",
        "anyio._backends._asyncio",
        "sniffio",
        # websockets
        "websockets.legacy",
        "websockets.legacy.client",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Trim unnecessary stdlib modules to reduce binary size
        "tkinter",
        "unittest",
        "xmlrpc",
        "pydoc",
        "doctest",
        "test",
        # Line editing for an interactive prompt. The standard library's code,
        # rlcompleter and site import it, and so does websockets.cli -- all
        # inside try/except, none on any path the agent runs -- and with it
        # PyInstaller bundles libreadline, which is GPL-3.0-or-later.
        "readline",
        "rlcompleter",
        # Installed only because PyInstaller needs it, yet bundled whole: the
        # huggingface_hub CLI's "import tomli" (Python < 3.11 only) resolves
        # to setuptools' vendored copy. Nothing the agent runs uses it.
        "setuptools",
        "_distutils_hack",
        "pkg_resources",
    ],
    noarchive=False,
    optimize=1,
    cipher=block_cipher,
)

pyz = PYZ(a.pure, cipher=block_cipher)

# Windows carries this in the PE resource; ELF and Mach-O have no equivalent
# slot, which is why the licence files above are bundled on every platform
# rather than only here.
version_info = None
if sys.platform == "win32":
    from PyInstaller.utils.win32.versioninfo import (
        FixedFileInfo, StringFileInfo, StringStruct, StringTable,
        VarFileInfo, VarStruct, VSVersionInfo,
    )

    _parts = tuple(int(part) for part in VERSION.split(".")[:3]) + (0,)
    version_info = VSVersionInfo(
        ffi=FixedFileInfo(filevers=_parts, prodvers=_parts),
        kids=[
            StringFileInfo([StringTable("040904B0", [
                StringStruct("CompanyName", COMPANY),
                StringStruct("FileDescription", DESCRIPTION),
                StringStruct("FileVersion", VERSION),
                StringStruct("InternalName", "faden-agent"),
                StringStruct("LegalCopyright", COPYRIGHT),
                StringStruct("OriginalFilename", "faden-agent.exe"),
                StringStruct("ProductName", "Fadenstack Node Agent"),
                StringStruct("ProductVersion", VERSION),
            ])]),
            VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
        ],
    )

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="faden-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=sys.platform != "win32",  # strip symbols on Linux/macOS
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    version=version_info,
)
