"""Redis has a memory cap, and its password is not on a command line.

It ran with ``noeviction`` and no ``--maxmemory``: a writer gone wrong filled the host before Redis refused
anything. Evicting instead would drop Langfuse's queued jobs and a chat reply's live events silently, so the
policy stays and the cap is added. ``redis-cli -a`` in the health check showed the password in ``ps``.
"""

from __future__ import annotations

import re
from pathlib import Path

from faden_cli.core.env_gen import DEPLOY_SETTINGS, default_env_vars, fill_required_env, read_env_file, write_env_file

COMPOSE = Path(__file__).resolve().parents[2] / "shared" / "docker-compose.yaml"


def _redis_service() -> str:
    text = COMPOSE.read_text(encoding="utf-8")
    start = text.index("\n  redis:\n")
    end = text.index("\n  minio:\n")
    return "\n".join(line for line in text[start:end].splitlines() if not line.lstrip().startswith("#"))


def test_redis_is_capped_and_still_does_not_evict() -> None:
    redis = _redis_service()

    assert "--maxmemory ${FADEN_REDIS_MAXMEMORY:-1gb}" in redis
    assert "--maxmemory-policy noeviction" in redis


def test_the_health_check_does_not_put_the_password_on_a_command_line() -> None:
    redis = _redis_service()

    assert not re.search(r"redis-cli[^\n]*\s-a\b", redis)
    assert "REDISCLI_AUTH: ${REDIS_AUTH" in redis
    assert "redis-cli ping" in redis


def test_deploy_writes_the_default_and_upgrade_adds_it(tmp_path: Path) -> None:
    assert default_env_vars()["FADEN_REDIS_MAXMEMORY"] == DEPLOY_SETTINGS["FADEN_REDIS_MAXMEMORY"] == "1gb"

    env = tmp_path / ".env"
    older = {k: v for k, v in default_env_vars().items() if k != "FADEN_REDIS_MAXMEMORY"}
    write_env_file(env, older)
    fill_required_env(env)
    assert read_env_file(env)["FADEN_REDIS_MAXMEMORY"] == "1gb"


def test_a_cap_the_operator_set_is_kept(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    write_env_file(env, {**default_env_vars(), "FADEN_REDIS_MAXMEMORY": "4gb"})

    fill_required_env(env)

    assert read_env_file(env)["FADEN_REDIS_MAXMEMORY"] == "4gb"
