"""What an add-on adds to an install: compose files, files written with the deployment, steps before pulling.

``registry.extend_compose_files``: compose files every command includes. ``extend_bundle_files``: files
written with the deployment. ``extend_install_hooks``: steps run before images are pulled. Without an add-on
nothing changes.
"""

from __future__ import annotations

from pathlib import Path

import pytest

# The release bundle and the stand-ins for Docker an upgrade runs with (tests/test_release_bundle.py).
from test_release_bundle import _configure, _no_docker, carried  # noqa: F401

from faden_cli.core import bundle, registry
from faden_cli.core.compose import ComposeContext


@pytest.fixture(autouse=True)
def no_addon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts without an add-on, and leaves none behind."""
    monkeypatch.setattr(registry, "COMPOSE_OVERLAYS", [])
    monkeypatch.setattr(registry, "BUNDLE_FILES", {})
    monkeypatch.setattr(registry, "INSTALL_HOOKS", [])


def _files(cmd: list[str]) -> list[str]:
    return [Path(cmd[i + 1]).name for i, part in enumerate(cmd) if part == "-f"]


def _ctx(folder: Path) -> ComposeContext:
    (folder / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    return ComposeContext(compose_files=[folder / "docker-compose.yaml"], project_dir=folder)


def test_without_an_addon_the_install_runs_core_alone(tmp_path: Path) -> None:
    (tmp_path / "docker-compose.extra.yaml").write_text("services: {}\n", encoding="utf-8")
    assert _files(_ctx(tmp_path).base_cmd()) == ["docker-compose.yaml"]


def test_every_command_includes_an_addons_compose_file_after_cores(tmp_path: Path) -> None:
    registry.extend_compose_files(["docker-compose.extra.yaml", "docker-compose.missing.yaml"])
    registry.extend_compose_files(["docker-compose.extra.yaml"])
    (tmp_path / "docker-compose.extra.yaml").write_text("services: {}\n", encoding="utf-8")
    assert _files(_ctx(tmp_path).base_cmd()) == ["docker-compose.yaml", "docker-compose.extra.yaml"]


@pytest.mark.parametrize("name", ["../docker-compose.x.yaml", "sub/extra.yaml", "extra.env", "/etc/x.yaml"])
def test_only_compose_files_beside_the_install_own(name: str) -> None:
    with pytest.raises(ValueError, match="Not a compose file"):
        registry.extend_compose_files([name])


def test_unpacking_writes_an_addons_files_and_replaces_them_on_upgrade(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    shipped = tmp_path / "bundle"
    shipped.mkdir()
    (shipped / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    monkeypatch.setattr(bundle, "bundled_files", lambda: shipped)
    source = tmp_path / "addon.yaml"
    source.write_text("services: {a: {}}\n", encoding="utf-8")
    registry.extend_bundle_files({"docker-compose.extra.yaml": source})

    install = tmp_path / "install"
    assert "docker-compose.extra.yaml" in bundle.unpack(install)
    assert (install / "docker-compose.extra.yaml").read_text(encoding="utf-8") == "services: {a: {}}\n"
    assert "docker-compose.extra.yaml" not in bundle.unpack(install), "unchanged: not written again"
    source.write_text("services: {b: {}}\n", encoding="utf-8")
    assert "docker-compose.extra.yaml" in bundle.unpack(install), "a newer add-on brings its copy"
    assert (install / "docker-compose.extra.yaml").read_text(encoding="utf-8") == "services: {b: {}}\n"


@pytest.mark.parametrize("name", ["../outside.yaml", "/etc/passwd"])
def test_an_addons_files_stay_inside_the_install(name: str, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Not a path inside the install"):
        registry.extend_bundle_files({name: tmp_path / "x"})


def test_install_steps_run_once_each_in_order(tmp_path: Path) -> None:
    seen: list[tuple[str, Path]] = []

    def first(folder: Path) -> None:
        seen.append(("first", folder))

    def second(folder: Path) -> None:
        seen.append(("second", folder))

    registry.extend_install_hooks([first, second])
    registry.extend_install_hooks([first])
    registry.run_install_hooks(tmp_path)
    assert seen == [("first", tmp_path), ("second", tmp_path)]


def test_a_failing_install_step_stops_what_runs_it(tmp_path: Path) -> None:
    def broken(folder: Path) -> None:
        msg = f"cannot log in for {folder}"
        raise RuntimeError(msg)

    registry.extend_install_hooks([broken])
    with pytest.raises(RuntimeError, match="cannot log in"):
        registry.run_install_hooks(tmp_path)


def test_an_upgrade_runs_the_steps_after_the_env_and_before_pulling(
    carried: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,  # noqa: F811 -- the fixture imported above
) -> None:
    import json  # noqa: PLC0415

    from click.testing import CliRunner  # noqa: PLC0415

    from faden_cli.commands import upgrade as upgrade_module  # noqa: PLC0415
    from faden_cli.core import update_check  # noqa: PLC0415

    install = tmp_path / "fadenstack"
    bundle.unpack(install)
    (install / bundle.MARKER).write_text(json.dumps({"version": "0.0.1"}), encoding="utf-8")
    (install / ".env").write_text("VERSION=0.0.1\nPOSTGRES_PASSWORD=pgpw\n", encoding="utf-8")
    _configure(tmp_path, monkeypatch, install)
    calls: list[str] = []
    _no_docker(monkeypatch, calls)
    monkeypatch.setenv(update_check.ENV_OFF, "1")
    monkeypatch.setattr(update_check, "cached_feed", lambda: None)

    def step(folder: Path) -> None:
        env = (folder / ".env").read_text(encoding="utf-8")
        calls.append(f"step:{folder.name}:{'REDIS_AUTH' in env}")

    registry.extend_install_hooks([step])
    result = CliRunner().invoke(upgrade_module.upgrade_cmd, ["-y", "--no-backup", "--skip-doctor"])
    assert result.exit_code == 0, result.output
    assert calls[:2] == ["step:fadenstack:True", "pull"], "after the .env is complete, before any pull"
