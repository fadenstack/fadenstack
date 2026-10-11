"""The CLI says when a newer release is out: once a day from the network, never in the way."""

from __future__ import annotations

import base64
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
import pytest
from click.testing import CliRunner
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from faden_cli import __version__
from faden_cli.core import release_feed, update_check
from faden_cli.core.bundle import MARKER
from faden_cli.core.settings import FadenConfig

KEY = Ed25519PrivateKey.generate()
PUBLIC = base64.b64encode(
    KEY.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
)


def publish(
    *versions: str, severity: str = "normal", generated_at: str = "2026-10-20T10:00:00Z"
) -> tuple[bytes, bytes]:
    index = json.dumps({
        "schema": 1,
        "product": "fadenstack",
        "generated_at": generated_at,
        "releases": [
            {"version": v, "channel": "stable", "severity": severity, "summary": "s"} for v in versions
        ],
    }).encode()
    sig = json.dumps({"key_id": "test", "signature": base64.b64encode(KEY.sign(index)).decode()}).encode()
    return index, sig


def newer(by: int = 1) -> str:
    v = release_feed.parse_version(__version__)
    assert v is not None
    return f"{v.major}.{v.minor}.{v.micro + by}"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FADEN_CONFIG", str(tmp_path / "faden.yaml"))
    monkeypatch.delenv("FADEN_NO_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setattr(release_feed, "TRUSTED_KEYS", {"test": PUBLIC.decode()})


@pytest.fixture
def served(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """The feed fetch answers with a feed naming a newer release; records the URLs asked."""
    asked: list[str] = []

    def fetch(url: str, *, timeout: float = 0) -> tuple[bytes, bytes]:
        asked.append(url)
        return publish(__version__, newer(), severity="security")

    monkeypatch.setattr(update_check, "fetch", fetch)
    return asked


def test_a_newer_release_is_told_with_the_command_to_get_it(served: list[str]) -> None:
    feed = update_check.refresh(FadenConfig())
    text = update_check.message(feed, FadenConfig())
    assert text is not None
    assert f"Fadenstack {newer()} is available (you have {__version__})" in text
    assert "security" in text
    assert "faden upgrade" in text
    assert served == [release_feed.FEED_URL]


def test_up_to_date_says_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_check, "fetch", lambda url, **_: publish(__version__))
    assert update_check.message(update_check.refresh(FadenConfig()), FadenConfig()) is None


def test_a_cli_newer_than_its_install_says_upgrade(served: list[str], tmp_path: Path) -> None:
    install = tmp_path / "install"
    install.mkdir()
    (install / MARKER).write_text(json.dumps({"version": "0.0.1"}), encoding="utf-8")
    cfg = FadenConfig(install_dir=str(install))
    text = update_check.message(update_check.refresh(cfg), cfg)
    assert text == f"This CLI is {__version__}; the install still runs 0.0.1. Upgrade it: faden upgrade"


def test_a_mirror_is_read_instead(served: list[str]) -> None:
    update_check.refresh(FadenConfig(update_feed_url="https://mirror.example/updates/v1/index.json"))
    assert served == ["https://mirror.example/updates/v1/index.json"]


def test_a_failed_check_waits_a_day_too(monkeypatch: pytest.MonkeyPatch) -> None:
    def offline(url: str, **_: object) -> tuple[bytes, bytes]:
        raise OSError("network unreachable")

    monkeypatch.setattr(update_check, "fetch", offline)
    assert update_check.is_stale()
    with pytest.raises(OSError):
        update_check.refresh(FadenConfig())
    assert not update_check.is_stale()
    assert update_check.is_stale(datetime.now(UTC) + timedelta(hours=25))


def test_a_tampered_cache_is_not_believed(served: list[str]) -> None:
    update_check.refresh(FadenConfig())
    path = update_check.cache_path()
    data = json.loads(path.read_text(encoding="utf-8"))
    data["index"] = data["index"].replace(newer(), newer(5))
    path.write_text(json.dumps(data), encoding="utf-8")
    assert update_check.cached_feed() is None


def test_an_older_feed_does_not_replace_a_newer_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_check, "fetch", lambda url, **_: publish(__version__, newer()))
    update_check.refresh(FadenConfig())
    monkeypatch.setattr(
        update_check, "fetch", lambda url, **_: publish(__version__, generated_at="2026-01-01T00:00:00Z")
    )
    with pytest.raises(release_feed.FeedError, match="older"):
        update_check.refresh(FadenConfig())
    kept = update_check.cached_feed()
    assert kept is not None and kept.release(newer()) is not None


def test_off_by_setting_or_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert update_check.enabled(FadenConfig())
    assert not update_check.enabled(FadenConfig(update_check=False))
    monkeypatch.setenv("FADEN_NO_UPDATE_CHECK", "1")
    assert not update_check.enabled(FadenConfig())


def _group() -> click.Group:
    @click.group()
    @click.pass_context
    def cli(ctx: click.Context) -> None:
        update_check.start(ctx, quiet=False)

    @cli.command()
    def status() -> None:
        click.echo("status ran")

    @cli.command()
    def upgrade() -> None:
        click.echo("upgrade ran")

    return cli


def test_a_command_ends_with_the_notice(served: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_check, "interactive", lambda: True)
    result = CliRunner().invoke(_group(), ["status"])
    assert result.exit_code == 0
    assert "status ran" in result.output
    assert served == [release_feed.FEED_URL]
    assert newer() in result.output  # the notice, on stderr, after the command's own output

    served.clear()
    CliRunner().invoke(_group(), ["status"])
    assert served == []  # checked less than a day ago


def test_quiet_when_not_a_terminal_and_in_upgrade(served: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(update_check, "interactive", lambda: False)
    result = CliRunner().invoke(_group(), ["status"])
    assert newer() not in result.output and served == []

    monkeypatch.setattr(update_check, "interactive", lambda: True)
    result = CliRunner().invoke(_group(), ["upgrade"])
    assert newer() not in result.output and served == []


def test_ci_is_not_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CI", "true")
    assert not update_check.interactive()


@pytest.mark.parametrize(
    ("prefix", "frozen", "expected"),
    [
        ("/home/op/.local/share/pipx/venvs/fadenstack", False, "pipx"),
        ("C:/Users/op/pipx/venvs/fadenstack", False, "pipx"),
        ("/home/op/.local/share/uv/tools/fadenstack", False, "uv"),
        ("/opt/whatever", True, "binary"),
    ],
)
def test_how_the_cli_was_installed(
    prefix: str, frozen: bool, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sys, "prefix", prefix)
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    assert update_check.installer() == expected


def _deploy_step(env: Path, *, choice: bool | None, yes: bool, answer: str = "") -> tuple[FadenConfig, str]:
    from faden_cli.commands.deploy import update_check_step  # noqa: PLC0415

    cfg = FadenConfig()

    @click.command()
    def step() -> None:
        update_check_step(env, cfg, choice=choice, yes=yes)

    result = CliRunner().invoke(step, input=answer)
    assert result.exit_code == 0, result.output
    return cfg, result.output


def test_deploy_asks_once_and_both_follow_the_answer(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("VERSION=0.4.1\n", encoding="utf-8")
    cfg, output = _deploy_step(env, choice=None, yes=False, answer="n\n")
    assert "They send nothing about this server" in " ".join(output.split())
    assert cfg.update_check is False
    assert "FADEN_UPDATE_CHECK=false" in env.read_text(encoding="utf-8")
    # Asked before: the answer stands, without asking again.
    cfg, output = _deploy_step(env, choice=None, yes=False)
    assert cfg.update_check is False and "Check for new releases?" not in output


def test_deploy_with_yes_or_the_flag(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("", encoding="utf-8")
    cfg, _ = _deploy_step(env, choice=None, yes=True)
    assert cfg.update_check is True and "FADEN_UPDATE_CHECK=true" in env.read_text(encoding="utf-8")
    cfg, _ = _deploy_step(env, choice=False, yes=True)
    assert cfg.update_check is False and "FADEN_UPDATE_CHECK=false" in env.read_text(encoding="utf-8")


def test_a_source_checkout_is_upgraded_with_git_not_a_newer_cli(tmp_path: Path) -> None:
    from faden_cli.commands import deploy as deploy_module  # noqa: PLC0415

    env = tmp_path / ".env"
    env.write_text("POSTGRES_PASSWORD=pw\n", encoding="utf-8")
    deploy_module.record_installer(env, tmp_path)
    text = env.read_text(encoding="utf-8")
    assert "FADEN_CLI_INSTALLER=source" in text
    assert "VERSION=" not in text  # a checkout builds its own images: nothing to pin
