"""release/binary_notices.py: the native libraries a standalone binary bundles, and their licences."""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "release" / "binary_notices.py"
if not SCRIPT.exists():
    pytest.skip("not a checkout of the whole repository", allow_module_level=True)

spec = importlib.util.spec_from_file_location("binary_notices", SCRIPT)
assert spec is not None and spec.loader is not None
binary_notices = importlib.util.module_from_spec(spec)
sys.modules["binary_notices"] = binary_notices
spec.loader.exec_module(binary_notices)

MACOS = [
    "Python",
    "libssl.3.dylib",
    "libcrypto.3.dylib",
    "libncursesw.6.dylib",
    "_lzma.cpython-313-darwin.so",
    "pyexpat.cpython-313-darwin.so",
    "cryptography/hazmat/bindings/_rust.abi3.so",
    "psutil/.dylibs/libsomething.dylib",
]
WINDOWS = [
    "python313.dll",
    "python3.dll",
    "libssl-3.dll",
    "libcrypto-3.dll",
    "libffi-8.dll",
    "VCRUNTIME140.dll",
    "api-ms-win-crt-runtime-l1-1-0.dll",
    "_bz2.pyd",
    "_decimal.pyd",
    "cryptography\\hazmat\\bindings\\_rust.pyd",
]
LINUX = [
    "libpython3.13.so.1.0",
    "libssl.so.3",
    "libcrypto.so.3",
    "libz.so.1",
    "python3.13/lib-dynload/_decimal.cpython-313-x86_64-linux-gnu.so",
    "psutil/_psutil_linux.abi3.so",
]
DEBIAN = {"libssl.so.3": "libssl3", "libcrypto.so.3": "libssl3", "libz.so.1": "zlib1g"}


def classify(names: list[str], owner=DEBIAN.get):
    return binary_notices.classify([name.replace("\\", "/") for name in names], owner=owner)


def test_macos_needs_openssl_ncurses_and_what_python_links_statically() -> None:
    found = classify(MACOS)
    assert found.platform == "darwin"
    assert set(found.licences) == {"openssl", "ncurses", "xz", "expat"}
    assert found.licences["openssl"] == ["libssl.3.dylib", "libcrypto.3.dylib"]
    assert not found.unmapped and not found.forbidden  # a package's own .dylibs are its business


def test_windows_needs_the_dlls_and_what_the_pyd_files_link() -> None:
    found = classify(WINDOWS)
    assert found.platform == "win32"
    assert set(found.licences) == {"openssl", "libffi", "microsoft-runtime", "bzip2", "libmpdec", "zlib"}
    assert not found.unmapped


def test_linux_takes_the_text_of_the_debian_package_that_owns_the_library() -> None:
    found = classify(LINUX)
    assert found.platform == "linux"
    assert found.packages == {"libssl3": ["libssl.so.3", "libcrypto.so.3"], "zlib1g": ["libz.so.1"]}
    assert set(found.licences) == {"libmpdec"}  # libpython is Python's own: part 2 of the notices


@pytest.mark.parametrize(
    ("names", "unknown"),
    [
        (["libssl-3.dll", "libnew-1.dll"], "libnew-1.dll"),
        (["libssl.3.dylib", "libnew.2.dylib"], "libnew.2.dylib"),
        (["libssl.so.3", "libnew.so.2"], "libnew.so.2"),
    ],
)
def test_a_library_nothing_maps_to_a_text_fails_the_build(names: list[str], unknown: str) -> None:
    found = classify(names)
    assert [item for item in found.unmapped if item.startswith(unknown)]
    with pytest.raises(SystemExit, match=re.escape(unknown)):
        binary_notices._fail_on_problems(found)


def test_readline_fails_even_though_debian_has_a_copyright_file_for_it() -> None:
    found = classify(["libreadline.so.8"], owner=lambda name: "libreadline8")
    assert found.forbidden
    with pytest.raises(SystemExit, match="must not be bundled"):
        binary_notices._fail_on_problems(found)


def test_every_text_a_rule_names_exists_and_says_where_it_is_from() -> None:
    wanted = {licence for _, licence in binary_notices._LIBRARY_LICENCES}
    wanted |= {licence for rules in binary_notices._STATIC.values() for _, licence in rules}
    for licence in wanted:
        text = (binary_notices.LICENSES / f"{licence}.txt").read_text(encoding="utf-8")
        assert text.startswith("Source: http"), licence
        assert len(text.splitlines()) > 5, licence


def test_the_section_holds_each_text_and_check_notices_one_that_is_missing(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(binary_notices, "archive_names", lambda binary: [n for n in MACOS])
    notices = tmp_path / "THIRD-PARTY-NOTICES.txt"
    notices.write_text("Existing notices\n", encoding="utf-8")

    binary_notices.append(Path("faden"), notices)
    text = notices.read_text(encoding="utf-8")
    assert "4. Native libraries" in text
    assert "--- openssl (libcrypto.3.dylib, libssl.3.dylib) ---" in text
    assert "Apache License" in text
    binary_notices.check(Path("faden"), notices)

    notices.write_text(text.replace("Apache License", "Pache License"), encoding="utf-8")
    with pytest.raises(SystemExit, match="--- openssl"):
        binary_notices.check(Path("faden"), notices)
