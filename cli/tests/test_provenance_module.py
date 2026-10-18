"""The provenance module: marks AI-generated content (EU AI Act Art. 50(2)).

Its code is not in this repository: installs run its published image (faden-provenance, and
faden-provenance-migrator for its database), and ``faden dev up`` runs it from a checkout of its own beside
the core one, ``../fadenstack-provenance``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from click.testing import CliRunner

from faden_cli.commands import rebuild
from faden_cli.commands.dev import dev_down, dev_up
from faden_cli.core import backup, compose, env_gen, tune
from faden_cli.core.dev_modules import caller_env, dev_command, module_env, select_modules
from faden_cli.core.registry import (
    DATABASES,
    DEV_ENDPOINTS,
    DEV_MODULE_ENDPOINTS,
    DEV_PROCESSES,
    KNOWN_PORTS,
    MODULES,
    MODULES_COMPAT,
)
from faden_cli.core.sysinfo import SystemInfo, calculate_tune_profile

PROVENANCE = MODULES["provenance"]
SHARED = {"POSTGRES_USER": "postgres", "POSTGRES_PASSWORD": "devpassword", "REDIS_AUTH": "redispass"}


# ── What the registry says ────────────────────────────────────────


def test_it_is_a_module_with_its_own_port_database_and_token() -> None:
    assert MODULES_COMPAT["provenance"] == {
        "profile": "provenance", "description": "Provenance (marks AI-generated content, Art. 50(2))",
    }
    assert (PROVENANCE.container, PROVENANCE.service_url) == ("faden-provenance", "http://faden-provenance:8000")
    assert (8009, "Provenance") in KNOWN_PORTS
    assert "faden_provenance" in DATABASES and PROVENANCE.database == "faden_provenance"
    assert PROVENANCE.redis_base is None, "it uses no Redis"
    assert PROVENANCE.service_token_keys == (
        "FADEN_GATEWAY_PROVENANCE_SERVICE_TOKEN", "FADEN_BACKEND_PROVENANCE_SERVICE_TOKEN",
        "FADEN_PROVENANCE_SERVICE_TOKEN",
    )
    assert PROVENANCE.on_by_default and "Art. 50(2)" in PROVENANCE.off_warning


def test_switching_it_on_points_the_gateway_and_the_backend_at_it() -> None:
    values = env_gen.default_env_vars(profiles=["provenance"])
    assert values["FADEN_GATEWAY_PROVENANCE_ENABLED"] == values["FADEN_BACKEND_PROVENANCE_ENABLED"] == "true"
    assert values["FADEN_GATEWAY_PROVENANCE_SERVICE_URL"] == "http://faden-provenance:8000"
    assert values["FADEN_BACKEND_PROVENANCE_SERVICE_URL"] == "http://faden-provenance:8000"
    token = values["FADEN_PROVENANCE_SERVICE_TOKEN"]
    assert token and token != PROVENANCE.dev_service_token
    assert {values[key] for key in PROVENANCE.service_token_keys} == {token}, "one token on all three sides"


def test_module_enable_gives_an_older_env_its_token(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("POSTGRES_PASSWORD=keep-me\n", encoding="utf-8")
    env_gen.set_module_env(env, enable=["provenance"])
    values = env_gen.read_env_file(env)
    assert values["FADEN_BACKEND_PROVENANCE_ENABLED"] == "true"
    assert len({values[key] for key in PROVENANCE.service_token_keys}) == 1
    assert values["FADEN_PROVENANCE_SERVICE_TOKEN"]


# ── Development: from its own checkout ────────────────────────────


def test_dev_up_runs_it_as_its_package_on_its_port() -> None:
    assert dev_command(PROVENANCE) == "uv run -m faden_provenance"
    assert dev_command(PROVENANCE) in dev_up._SERVICE_INVOCATIONS
    assert [m.name for m in select_modules("provenance", None)] == ["provenance"]
    env = module_env(PROVENANCE, shared=SHARED, running=[PROVENANCE])
    assert env["FADEN_PROVENANCE_HOST"] == "127.0.0.1"
    assert env["FADEN_PROVENANCE_PORT"] == "8009"
    assert env["FADEN_PROVENANCE_ENVIRONMENT"] == "dev"
    assert env["FADEN_PROVENANCE_DB_BASE"] == "faden_provenance"
    assert env["FADEN_PROVENANCE_DB_PASS"] == "devpassword"
    assert env["FADEN_PROVENANCE_SERVICE_TOKEN"] == "dev-provenance-service-token"
    assert not any("REDIS" in key for key in env)


def test_the_gateway_and_backend_reach_it_on_the_host() -> None:
    gateway = caller_env("GATEWAY", shared=SHARED, running=[PROVENANCE])
    backend = caller_env("BACKEND", shared=SHARED, running=[PROVENANCE])
    assert gateway["FADEN_GATEWAY_PROVENANCE_ENABLED"] == "true"
    assert gateway["FADEN_GATEWAY_PROVENANCE_SERVICE_URL"] == "http://127.0.0.1:8009"
    assert backend["FADEN_BACKEND_PROVENANCE_SERVICE_URL"] == "http://127.0.0.1:8009"
    assert backend["FADEN_BACKEND_PROVENANCE_SERVICE_TOKEN"] == "dev-provenance-service-token"


@pytest.mark.parametrize("layout", ["monorepo", "flat"])
def test_its_checkout_is_found_beside_the_core_one(tmp_path: Path, layout: str) -> None:
    """In a workspace holding the core checkout (fadenstack/), or with the core checkout as the workspace."""
    if layout == "monorepo":
        workspace = tmp_path
        (workspace / "fadenstack" / "backend").mkdir(parents=True)
    else:
        workspace = tmp_path / "fadenstack"
        (workspace / "backend").mkdir(parents=True)
    (tmp_path / "fadenstack-provenance").mkdir()

    assert dev_up._module_present(workspace, PROVENANCE)
    found = dev_up.find_service_dir(workspace, PROVENANCE.dev_dir).resolve()
    assert found == (tmp_path / "fadenstack-provenance").resolve()


def test_without_its_checkout_dev_up_says_so_and_goes_on(
    tmp_path: Path, capsys: pytest.CaptureFixture[str],
) -> None:
    (tmp_path / "fadenstack" / "backend").mkdir(parents=True)

    assert not dev_up._module_present(tmp_path / "fadenstack", PROVENANCE)

    said = "".join(capsys.readouterr().out.split())  # the console wraps a long path
    assert "Provenance:nocheckoutat" in said and "fadenstack-provenance—notstarted." in said


def test_dev_status_and_down_know_its_process() -> None:
    assert any(p.name == "Provenance" and p.pattern == "faden_provenance" for p in DEV_PROCESSES)
    assert ("Provenance", ["m faden_provenance"]) in dev_down._DEV_PROCESS_PATTERNS
    assert ("Provenance", "http://127.0.0.1:8009") in DEV_ENDPOINTS
    assert DEV_MODULE_ENDPOINTS["Provenance"] == "provenance"


# ── Installs ──────────────────────────────────────────────────────


def test_a_backup_reads_its_migrator_s_head() -> None:
    assert backup._MIGRATOR_SERVICES["faden-provenance-migrator"] == "faden_provenance"


def test_rebuild_recreates_it_and_builds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Its image is published; nothing here builds it. An empty build list used to mean: build everything."""
    built: list[object] = []
    ran: list[list[str]] = []

    def run(cmd: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setenv("FADEN_CONFIG", str(tmp_path / "faden.yaml"))
    monkeypatch.setattr(rebuild, "build_context_from_config", lambda cfg: compose.ComposeContext())
    monkeypatch.setattr(rebuild, "compose_build", lambda *a, **k: built.append(k) or 0)
    monkeypatch.setattr(compose.shutil, "which", lambda name: "docker")
    monkeypatch.setattr(subprocess, "run", run)

    result = CliRunner().invoke(rebuild.rebuild_cmd, ["provenance"])

    assert result.exit_code == 0, result.output
    assert built == []
    assert ran == [["docker", "compose", "up", "-d", "--force-recreate", "--no-deps", "faden-provenance"]]


def test_postgres_and_memory_count_it_when_it_is_on(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """One worker with a pool of 5 and 10 more on demand; its memory once it is switched on."""
    host = SystemInfo(physical_cores=8, logical_cores=16, total_ram_bytes=64 * 1024**3)
    assert calculate_tune_profile("prod", system=host).db_connections()["provenance"] == 1 * (5 + 10)
    monkeypatch.setattr(tune, "detect_system", lambda: host)
    path = tmp_path / ".env"
    env_gen.write_env_file(path, {"POSTGRES_PASSWORD": "secret", "FADEN_BACKEND_PROVENANCE_ENABLED": "true"})

    tp = tune.tune_env(path)

    assert tp.modules == frozenset({"provenance"})
    assert "provenance module" in tp.memory_mb()
