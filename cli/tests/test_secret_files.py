"""The keys that open content reach the services as files, not as their environment (admin guardrails, 6.2).

``docker inspect`` showed the chat key, the settings master key, the pepper and the MCP key in the containers'
environment, and the console's container pages showed them to administrators.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest
import yaml

from faden_cli.core import compose, secret_files
from faden_cli.core.compose import ComposeContext
from faden_cli.core.env_gen import write_env_file

SHARED = Path(__file__).resolve().parents[2] / "shared"
KEYS = {
    "FADEN_GATEWAY_ENCRYPTION_KEY": "chat-key",
    "FADEN_BACKEND_SETTINGS_MASTER_KEY": "master-key",
    "FADEN_BACKEND_CREDENTIAL_PEPPER": "pepper",
    "FADEN_MCP_ENCRYPTION_KEY": "mcp-key",
}


def _compose() -> dict:
    return yaml.safe_load((SHARED / "docker-compose.yaml").read_text(encoding="utf-8"))


#: The names the services read the keys under (the gateway reads the backend's master key as its own).
_AS_READ = {*secret_files.SECRET_FILES, "FADEN_GATEWAY_SETTINGS_MASTER_KEY", "FADEN_GATEWAY_SETTINGS_MASTER_KEY_PREVIOUS"}


def test_no_service_gets_a_content_key_in_its_environment() -> None:
    for name, service in _compose()["services"].items():
        environment = service.get("environment") or {}
        names = environment if isinstance(environment, dict) else [item.split("=", 1)[0] for item in environment]
        assert not set(names) & _AS_READ, name


def test_each_service_gets_the_keys_it_had_as_files() -> None:
    doc = _compose()
    files = {name: spec["file"] for name, spec in doc["secrets"].items()}
    assert sorted(Path(path).name for path in files.values()) == sorted(secret_files.SECRET_FILES)
    targets = {
        name: {entry["target"]: files[entry["source"]] for entry in service.get("secrets", [])}
        for name, service in doc["services"].items()
    }
    assert targets["faden-gateway"] == {
        "FADEN_GATEWAY_SETTINGS_MASTER_KEY": "./secrets/FADEN_BACKEND_SETTINGS_MASTER_KEY",
        "FADEN_GATEWAY_SETTINGS_MASTER_KEY_PREVIOUS": "./secrets/FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS",
        "FADEN_GATEWAY_ENCRYPTION_KEY": "./secrets/FADEN_GATEWAY_ENCRYPTION_KEY",
        "FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS": "./secrets/FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS",
    }
    assert set(targets["faden-backend"]) == set(targets["faden-backend-worker"]) == {
        "FADEN_BACKEND_SETTINGS_MASTER_KEY", "FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS",
        "FADEN_BACKEND_CREDENTIAL_PEPPER",
    }
    assert set(targets["faden-gateway-migrator"]) == {
        "FADEN_GATEWAY_SETTINGS_MASTER_KEY", "FADEN_GATEWAY_ENCRYPTION_KEY", "FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS",
    }
    assert set(targets["faden-backend-migrator"]) == {"FADEN_BACKEND_SETTINGS_MASTER_KEY"}
    assert set(targets["faden-mcp"]) == {"FADEN_MCP_ENCRYPTION_KEY", "FADEN_MCP_ENCRYPTION_KEY_PREVIOUS"}


def _install(tmp_path: Path, values: dict[str, str]) -> ComposeContext:
    (tmp_path / "docker-compose.yaml").write_text(
        (SHARED / "docker-compose.yaml").read_text(encoding="utf-8"), encoding="utf-8",
    )
    write_env_file(tmp_path / ".env", values)
    return ComposeContext(
        compose_files=[tmp_path / "docker-compose.yaml"], env_file=tmp_path / ".env", project_dir=tmp_path,
    )


def test_every_compose_command_writes_the_files_from_env_first(tmp_path: Path) -> None:
    ctx = _install(tmp_path, {**KEYS, "FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS": "old-chat-key"})
    ctx.base_cmd()
    folder = tmp_path / "secrets"
    assert sorted(p.name for p in folder.iterdir()) == sorted(secret_files.SECRET_FILES)
    assert (folder / "FADEN_GATEWAY_ENCRYPTION_KEY").read_text(encoding="utf-8") == "chat-key"
    assert (folder / "FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS").read_text(encoding="utf-8") == "old-chat-key"
    assert (folder / "FADEN_MCP_ENCRYPTION_KEY_PREVIOUS").read_bytes() == b""
    if os.name != "nt":
        assert stat.S_IMODE(folder.stat().st_mode) == 0o700, "nobody else on the server reaches them"
        assert stat.S_IMODE((folder / "FADEN_GATEWAY_ENCRYPTION_KEY").stat().st_mode) == 0o644


def test_a_key_changed_in_env_is_rewritten_in_place(tmp_path: Path) -> None:
    """A running container's mount follows the file it was made from: same file, new content."""
    ctx = _install(tmp_path, KEYS)
    ctx.base_cmd()
    path = tmp_path / "secrets" / "FADEN_GATEWAY_ENCRYPTION_KEY"
    inode = path.stat().st_ino
    write_env_file(tmp_path / ".env", {**KEYS, "FADEN_GATEWAY_ENCRYPTION_KEY": "rotated"})
    ctx.base_cmd()
    assert path.read_text(encoding="utf-8") == "rotated"
    if os.name != "nt":
        assert path.stat().st_ino == inode


def test_the_folder_docker_made_for_a_missing_file_is_cleared(tmp_path: Path) -> None:
    ctx = _install(tmp_path, KEYS)
    (tmp_path / "secrets" / "FADEN_BACKEND_CREDENTIAL_PEPPER").mkdir(parents=True)
    ctx.base_cmd()
    assert (tmp_path / "secrets" / "FADEN_BACKEND_CREDENTIAL_PEPPER").read_text(encoding="utf-8") == "pepper"


def test_up_refuses_without_a_required_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    ran: list[list[str]] = []
    monkeypatch.setattr(compose, "_run", lambda cmd, **_k: ran.append(cmd))
    ctx = _install(tmp_path, {k: v for k, v in KEYS.items() if k != "FADEN_BACKEND_CREDENTIAL_PEPPER"})
    assert compose.up(ctx) == 1
    assert ran == []


def test_an_older_compose_file_gets_no_files(tmp_path: Path) -> None:
    """A compose file that still passes the keys as environment: nothing to write."""
    (tmp_path / "docker-compose.yaml").write_text("services: {}\n", encoding="utf-8")
    write_env_file(tmp_path / ".env", KEYS)
    ctx = ComposeContext(compose_files=[tmp_path / "docker-compose.yaml"], env_file=tmp_path / ".env",
                         project_dir=tmp_path)
    ctx.base_cmd()
    assert not (tmp_path / "secrets").exists()
