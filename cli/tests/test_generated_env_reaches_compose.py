"""Every setting ``faden deploy`` writes into ``.env`` reaches a container.

The gateway's chat encryption key was generated into every install's ``.env``
and passed to no service: the gateway started without it, and every install
stored its chats in plain text (2026-10-02).
"""

from __future__ import annotations

import re
from pathlib import Path

from faden_cli.core.env_gen import default_env_vars

COMPOSE = Path(__file__).resolve().parents[2] / "shared" / "docker-compose.yaml"
#: Read by the RAG module's own compose file, in its own repository.
ELSEWHERE = {"FADEN_RAG_SERVICE_TOKEN", "FADEN_BACKEND_RAG_SERVICE_TOKEN"}


def test_every_generated_setting_reaches_a_container() -> None:
    compose = COMPOSE.read_text(encoding="utf-8")
    unused = sorted(
        key
        for key in default_env_vars()
        if key not in ELSEWHERE and not re.search(r"\$\{" + re.escape(key) + r"[:}-]", compose)
    )
    assert unused == []


def test_the_chat_encryption_key_reaches_the_gateway() -> None:
    gateway = COMPOSE.read_text(encoding="utf-8").split("\n  faden-gateway:\n", 1)[1].split("\n  faden-", 1)[0]
    assert "FADEN_GATEWAY_ENCRYPTION_KEY: ${FADEN_GATEWAY_ENCRYPTION_KEY:-}" in gateway


def test_the_previous_keys_and_the_pepper_reach_the_services_that_read_them() -> None:
    """`faden secrets rotate` hands a service the old key through .env; the pepper keeps the machines connected."""
    compose = COMPOSE.read_text(encoding="utf-8")

    def block(name: str) -> str:
        return compose.split(f"\n  {name}:\n", 1)[1].split("\n  faden-", 1)[0]

    for service in ("faden-backend", "faden-backend-worker"):
        text = block(service)
        assert "FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS: ${FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS:-}" in text
        assert "FADEN_BACKEND_CREDENTIAL_PEPPER: ${FADEN_BACKEND_CREDENTIAL_PEPPER:?" in text
    gateway = block("faden-gateway")
    assert "FADEN_GATEWAY_SETTINGS_MASTER_KEY_PREVIOUS: ${FADEN_BACKEND_SETTINGS_MASTER_KEY_PREVIOUS:-}" in gateway
    assert "FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS: ${FADEN_GATEWAY_ENCRYPTION_KEY_PREVIOUS:-}" in gateway
    assert "FADEN_MCP_ENCRYPTION_KEY_PREVIOUS: ${FADEN_MCP_ENCRYPTION_KEY_PREVIOUS:-}" in block("faden-mcp")


def test_the_migrators_seal_provider_keys_with_the_installs_master_key() -> None:
    """Without it the migration (pr0vk3ys / pr0vk3yg) sealed them with the published development default."""
    compose = COMPOSE.read_text(encoding="utf-8")
    backend = compose.split("\n  faden-backend-migrator:\n", 1)[1].split("\n  faden-backend:\n", 1)[0]
    gateway = compose.split("\n  faden-gateway-migrator:\n", 1)[1].split("\n  faden-gateway:\n", 1)[0]
    assert "FADEN_BACKEND_SETTINGS_MASTER_KEY: ${FADEN_BACKEND_SETTINGS_MASTER_KEY:?" in backend
    assert "FADEN_GATEWAY_SETTINGS_MASTER_KEY: ${FADEN_BACKEND_SETTINGS_MASTER_KEY:?" in gateway
