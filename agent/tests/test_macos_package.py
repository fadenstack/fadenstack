"""macos-package.sh and the Mac signing modes of build-binary.sh: developer-id fails closed.

The Apple account is pending, so the developer-id path cannot run end to end
here. What can be checked is that every step refuses without its inputs
(naming them, signing nothing), and, on a Mac, the shape of the pkg itself.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

AGENT = Path(__file__).resolve().parents[1]
PACKAGE = AGENT / "macos-package.sh"
BUILD = AGENT / "build-binary.sh"
SECRETS = (
    "FADEN_MAC_CODESIGN_IDENTITY",
    "FADEN_MAC_INSTALLER_IDENTITY",
    "APPLE_CERTIFICATES_P12_BASE64",
    "APPLE_CERTIFICATES_P12_PASSWORD",
    "APPLE_NOTARY_KEY_ID",
    "APPLE_NOTARY_ISSUER_ID",
    "APPLE_NOTARY_KEY_P8_BASE64",
)
darwin = pytest.mark.skipif(sys.platform != "darwin", reason="needs macOS tools")


def _env(**extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k not in SECRETS and k != "CI"}
    env.update(extra)
    return env


def _run(script: Path, *args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(script), *args], capture_output=True, text=True, env=env or _env(), timeout=120, check=False
    )


APP = "Developer ID Application: Fadenstack Test (ABCDE12345)"
INSTALLER = "Developer ID Installer: Fadenstack Test (ABCDE12345)"
WELL_FORMED = {**dict.fromkeys(SECRETS, "x"), "FADEN_MAC_CODESIGN_IDENTITY": APP,
               "FADEN_MAC_INSTALLER_IDENTITY": INSTALLER}


def test_secrets_with_none_set_is_exit_1() -> None:
    result = _run(PACKAGE, "secrets")
    assert result.returncode == 1
    assert "no Apple signing inputs" in result.stdout


def test_secrets_passes_when_all_are_set_and_well_formed() -> None:
    result = _run(PACKAGE, "secrets", env=_env(**WELL_FORMED))
    assert result.returncode == 0, result.stdout
    assert "team ABCDE12345" in result.stdout


@pytest.mark.parametrize("dropped", SECRETS)
def test_a_partial_set_is_exit_3_naming_what_is_missing(dropped: str) -> None:
    # A typo or a forgotten secret must fail the release, never fall back to ad hoc.
    env = _env(**{k: v for k, v in WELL_FORMED.items() if k != dropped})
    result = _run(PACKAGE, "secrets", env=env)
    assert result.returncode == 3
    assert dropped in result.stdout


@pytest.mark.parametrize(
    "override",
    [
        {"FADEN_MAC_CODESIGN_IDENTITY": "Apple Development: Someone (ABCDE12345)"},
        {"FADEN_MAC_INSTALLER_IDENTITY": "Developer ID Application: Fadenstack Test (ABCDE12345)"},
        {"FADEN_MAC_INSTALLER_IDENTITY": "Developer ID Installer: Fadenstack Test (ZZZZZ99999)"},
        {"FADEN_MAC_CODESIGN_IDENTITY": "Developer ID Application: no team"},
    ],
)
def test_a_misnamed_identity_is_exit_3(override: dict[str, str]) -> None:
    result = _run(PACKAGE, "secrets", env=_env(**{**WELL_FORMED, **override}))
    assert result.returncode == 3
    assert "malformed" in result.stdout


def _fake_codesign(tmp_path: Path, display: str) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "display.txt").write_text(display)
    tool = bin_dir / "codesign"
    tool.write_text(f'#!/bin/sh\ncase " $* " in *" --display "*) cat "{tmp_path}/display.txt" >&2 ;; esac\nexit 0\n')
    tool.chmod(0o755)
    (tmp_path / "agent").write_bytes(b"x")
    return _env(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}", FADEN_MAC_CODESIGN_IDENTITY=APP)


DEVELOPER_ID = f"""Executable=/x
CodeDirectory v=20500 size=1 flags=0x10000(runtime) hashes=1+7 location=embedded
Authority={APP}
Authority=Developer ID Certification Authority
Authority=Apple Root CA
TeamIdentifier=ABCDE12345
"""


def test_verify_accepts_the_developer_id_and_team(tmp_path: Path) -> None:
    env = _fake_codesign(tmp_path, DEVELOPER_ID)
    result = _run(PACKAGE, "verify", str(tmp_path / "agent"), env=env)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    ("display", "why"),
    [
        ("CodeDirectory v=20500 flags=0x10002(adhoc,runtime)\nSignature=adhoc\nTeamIdentifier=not set\n",
         "is not signed by"),
        (DEVELOPER_ID.replace("TeamIdentifier=ABCDE12345", "TeamIdentifier=ZZZZZ99999"), "TeamIdentifier"),
        (DEVELOPER_ID.replace("flags=0x10000(runtime)", "flags=0x0(none)"), "hardened runtime"),
    ],
)
def test_verify_refuses_anything_else(tmp_path: Path, display: str, why: str) -> None:
    env = _fake_codesign(tmp_path, display)
    result = _run(PACKAGE, "verify", str(tmp_path / "agent"), env=env)
    assert result.returncode == 1
    assert why in result.stderr


def test_cleanup_removes_the_notary_key(tmp_path: Path) -> None:
    key = tmp_path / "faden-notary.p8"
    key.write_text("secret")
    result = _run(PACKAGE, "cleanup", env=_env(RUNNER_TEMP=str(tmp_path), FADEN_MAC_KEYCHAIN=str(tmp_path / "k")))
    assert result.returncode == 0, result.stderr
    assert not key.exists()


def test_signed_pkg_needs_the_installer_identity() -> None:
    result = _run(PACKAGE, "pkg")
    assert result.returncode == 1
    assert "FADEN_MAC_INSTALLER_IDENTITY" in result.stderr
    assert "nothing was signed" in result.stderr


def test_notarize_needs_the_api_key() -> None:
    result = _run(PACKAGE, "notarize")
    assert result.returncode == 1
    for name in ("APPLE_NOTARY_KEY_ID", "APPLE_NOTARY_ISSUER_ID", "APPLE_NOTARY_KEY_P8_BASE64"):
        assert name in result.stderr


def test_keychain_refuses_outside_ci() -> None:
    # It rewrites the keychain search list: only on a throwaway runner.
    result = _run(PACKAGE, "keychain", env=_env(**WELL_FORMED))
    assert result.returncode == 1
    assert "CI=true" in result.stderr


def test_unknown_command_fails() -> None:
    assert _run(PACKAGE, "sign-everything").returncode == 1


@darwin
def test_developer_id_build_needs_an_identity() -> None:
    result = _run(BUILD, "macos-arm64", env=_env(FADEN_MAC_SIGNING="developer-id"))
    assert result.returncode == 1
    assert "FADEN_MAC_CODESIGN_IDENTITY" in result.stderr


@darwin
def test_developer_id_build_refuses_an_identity_not_in_the_keychain() -> None:
    result = _run(
        BUILD,
        "macos-arm64",
        env=_env(FADEN_MAC_SIGNING="developer-id", FADEN_MAC_CODESIGN_IDENTITY="Developer ID Application: Nobody (0000000000)"),
    )
    assert result.returncode == 1
    assert "no valid signing identity" in result.stderr


@darwin
def test_unknown_signing_mode_is_refused() -> None:
    result = _run(BUILD, "macos-arm64", env=_env(FADEN_MAC_SIGNING="whatever"))
    assert result.returncode == 2


@darwin
def test_unsigned_pkg_stages_only_the_binary_as_root(tmp_path: Path) -> None:
    # A copy of the script with a stand-in for the built agent beside it.
    (tmp_path / "faden_agent").mkdir()
    (tmp_path / "faden_agent" / "__init__.py").write_text('__version__ = "9.9.9"\n')
    (tmp_path / "dist").mkdir()
    binary = tmp_path / "dist" / "faden-agent-macos-arm64"
    shutil.copyfile("/usr/bin/true", binary)  # signed by Apple: passes codesign --verify
    binary.chmod(0o755)
    shutil.copy2(PACKAGE, tmp_path / "macos-package.sh")

    result = _run(tmp_path / "macos-package.sh", "pkg", "--unsigned")
    assert result.returncode == 0, result.stderr
    pkg = tmp_path / "dist" / "faden-agent-macos-arm64.pkg"
    files = subprocess.run(
        ["pkgutil", "--payload-files", str(pkg)], capture_output=True, text=True, check=True
    ).stdout.split()
    real = [f for f in files if not Path(f).name.startswith("._")]
    assert sorted(real) == sorted(
        [".", "./Library", "./Library/Fadenstack", "./Library/Fadenstack/staging",
         "./Library/Fadenstack/staging/faden-agent-9.9.9"]
    )
