"""release/pin_agent.py: every build is pinned, the Mac pkg when the release has one."""

from __future__ import annotations

import importlib.util
import io
import urllib.error
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "release" / "pin_agent.py"
spec = importlib.util.spec_from_file_location("pin_agent", SCRIPT)
assert spec and spec.loader
pin_agent = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pin_agent)

FILES = ("linux-x86_64", "linux-aarch64", "macos-arm64", "macos-arm64.pkg")
DIGEST = {name: f"{i:x}" * 64 for i, name in enumerate(FILES, 1)}


def _release(monkeypatch: pytest.MonkeyPatch, files: dict[str, str]) -> list[str]:
    asked: list[str] = []

    def fetch(url: str) -> str:
        asked.append(url)
        name = url.rsplit("/faden-agent-", 1)[1].removesuffix(".sha256")
        if name not in files:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO())  # type: ignore[arg-type]
        return f"{files[name]}  faden-agent-{name}\n"

    monkeypatch.setattr(pin_agent, "_fetch", fetch)
    return asked


def test_the_mac_build_is_pinned_beside_linux(monkeypatch: pytest.MonkeyPatch) -> None:
    _release(monkeypatch, {k: v for k, v in DIGEST.items() if k != "macos-arm64.pkg"})
    assert pin_agent.published("0.3.0") == {
        "linux-x86_64": DIGEST["linux-x86_64"],
        "linux-aarch64": DIGEST["linux-aarch64"],
        "macos-arm64": DIGEST["macos-arm64"],
    }


def test_the_notarized_pkg_is_pinned_when_released(monkeypatch: pytest.MonkeyPatch) -> None:
    asked = _release(monkeypatch, DIGEST)
    pinned = pin_agent.published("0.3.0")
    assert pinned["macos-arm64-pkg"] == DIGEST["macos-arm64.pkg"]
    assert any(url.endswith("/node-agent-v0.3.0/faden-agent-macos-arm64.pkg.sha256") for url in asked)


def test_a_release_without_the_mac_build_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _release(monkeypatch, {k: v for k, v in DIGEST.items() if not k.startswith("macos")})
    gone = r"macos-arm64: node-agent-v0\.3\.0 has no faden-agent-macos-arm64\.sha256"
    with pytest.raises(SystemExit, match=gone):
        pin_agent.published("0.3.0")


def test_a_bad_digest_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    _release(monkeypatch, {**DIGEST, "macos-arm64.pkg": "not-hex"})
    with pytest.raises(SystemExit, match="macos-arm64-pkg"):
        pin_agent.published("0.3.0")


def test_other_http_errors_are_not_read_as_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    def fetch(url: str) -> str:
        if url.endswith(".pkg.sha256"):
            raise urllib.error.HTTPError(url, 503, "Unavailable", {}, io.BytesIO())  # type: ignore[arg-type]
        return DIGEST["linux-x86_64"]

    monkeypatch.setattr(pin_agent, "_fetch", fetch)
    with pytest.raises(urllib.error.HTTPError):
        pin_agent.published("0.3.0")

