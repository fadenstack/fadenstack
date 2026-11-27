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


def test_secrets_names_every_missing_input() -> None:
    result = _run(PACKAGE, "secrets")
    assert result.returncode == 1
    for name in SECRETS:
        assert name in result.stdout


def test_secrets_passes_when_all_are_set() -> None:
    result = _run(PACKAGE, "secrets", env=_env(**dict.fromkeys(SECRETS, "x")))
    assert result.returncode == 0, result.stdout


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
    result = _run(PACKAGE, "keychain", env=_env(**dict.fromkeys(SECRETS, "x")))
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
