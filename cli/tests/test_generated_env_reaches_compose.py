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
        for key in default_env_vars(admin_password="x")
        if key not in ELSEWHERE and not re.search(r"\$\{" + re.escape(key) + r"[:}-]", compose)
    )
    assert unused == []


def test_the_chat_encryption_key_reaches_the_gateway() -> None:
    gateway = COMPOSE.read_text(encoding="utf-8").split("\n  faden-gateway:\n", 1)[1].split("\n  faden-", 1)[0]
    assert "FADEN_GATEWAY_ENCRYPTION_KEY: ${FADEN_GATEWAY_ENCRYPTION_KEY:-}" in gateway
