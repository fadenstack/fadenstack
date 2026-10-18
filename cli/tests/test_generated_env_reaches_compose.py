"""Every setting ``faden deploy`` writes into ``.env`` reaches a container.

The gateway's chat encryption key was generated into every install's ``.env``
and passed to no service: the gateway started without it, and every install
stored its chats in plain text (2026-10-02).
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from faden_cli.core import secret_files
from faden_cli.core.env_gen import default_env_vars

COMPOSE = Path(__file__).resolve().parents[2] / "shared" / "docker-compose.yaml"
#: Read by the RAG module's own compose file, in its own repository.
ELSEWHERE = {"FADEN_RAG_SERVICE_TOKEN", "FADEN_BACKEND_RAG_SERVICE_TOKEN"}


def _secret_files(compose: str) -> set[str]:
    """The .env keys compose mounts as files (core/secret_files.py writes them from .env)."""
    return set(re.findall(r"file: \./secrets/([A-Z0-9_]+)", compose))


def _mounted(service: str) -> dict[str, str]:
    """What *service* reads under /run/secrets: the name it reads, and the .env key the file is written from."""
    doc = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    files = {name: Path(spec["file"]).name for name, spec in doc["secrets"].items()}
    return {entry["target"]: files[entry["source"]] for entry in doc["services"][service].get("secrets", [])}


def test_every_generated_setting_reaches_a_container() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    as_files = _secret_files(compose)
    unused = sorted(
        key
        for key in default_env_vars()
        if key not in ELSEWHERE and key not in as_files
        and not re.search(r"\$\{" + re.escape(key) + r"[:}-]", compose)
    )
    assert unused == []


def test_the_chat_encryption_key_reaches_the_gateway() -> None:
    # Required: `faden` refuses to start the services without it, as the gateway does (faden upgrade writes one).
    assert _mounted("faden-gateway")["FADEN_GATEWAY_ENCRYPTION_KEY"] == "FADEN_GATEWAY_ENCRYPTION_KEY"
    assert "FADEN_GATEWAY_ENCRYPTION_KEY" in secret_files.REQUIRED


def test_the_previous_keys_and_the_pepper_reach_the_services_that_read_them() -> None:
    """`faden secrets rotate` hands a service the old key through .env; the pepper keeps the machines connected."""
    for service in ("faden-backend", "faden-backend-worker"):
        mounted = _mounted(service)
        assert mounted["FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS"] == "FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS"
        assert mounted["FADEN_BACKEND_CREDENTIAL_PEPPER"] == "FADEN_BACKEND_CREDENTIAL_PEPPER"
    gateway = _mounted("faden-gateway")
    assert gateway["FADEN_GATEWAY_SETTINGS_MASTER_KEY_PREVIOUS"] == "FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS"
    assert gateway["FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS"] == "FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS"
    assert _mounted("faden-mcp")["FADEN_MCP_ENCRYPTION_KEY_PREVIOUS"] == "FADEN_MCP_ENCRYPTION_KEY_PREVIOUS"


def test_the_migrators_seal_provider_keys_with_the_installs_master_key() -> None:
    """Without it the migration (pr0vk3ys / pr0vk3yg) sealed them with the published development default."""
    assert _mounted("faden-backend-migrator")["FADEN_BACKEND_SETTINGS_MASTER_KEY"] == "FADEN_BACKEND_SETTINGS_MASTER_KEY"
    assert _mounted("faden-gateway-migrator")["FADEN_GATEWAY_SETTINGS_MASTER_KEY"] == "FADEN_BACKEND_SETTINGS_MASTER_KEY"
