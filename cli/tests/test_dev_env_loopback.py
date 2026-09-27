"""The dev services reach the shared infra on 127.0.0.1, not "localhost".

Postgres, Redis and RabbitMQ are published on 127.0.0.1 only. On Windows
"localhost" tries ::1 first, and with WSL mirrored networking that SYN is
dropped rather than refused, so each new connection waited 21 s before
falling back to IPv4. The gateway opens connections as bursts need them:
chat replies took 22 s that the engine had produced in 90 ms.
"""

from __future__ import annotations

from pathlib import Path

from faden_cli.commands.dev import dev_up
from faden_cli.core.registry import (
    BACKEND_DEV_ENV,
    GATEWAY_DEV_ENV,
    INFRA_HOST_KEYS,
    INFRA_LOOPBACK,
)


def test_new_env_files_use_the_ipv4_loopback() -> None:
    defaults = {**BACKEND_DEV_ENV, **GATEWAY_DEV_ENV}
    for key in INFRA_HOST_KEYS:
        assert defaults[key] == INFRA_LOOPBACK == "127.0.0.1", key


def _env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip() and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            out[k.strip()] = v.strip()
    return out


def _workspace(tmp_path: Path) -> Path:
    shared = tmp_path / "shared"
    shared.mkdir()
    (shared / ".env").write_text("RABBITMQ_BACKEND_PASS=pw\nRABBITMQ_API_PASS=pw\n", encoding="utf-8")
    return tmp_path


def test_an_existing_backend_env_is_moved_off_localhost(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    backend = ws / "backend"
    backend.mkdir()
    (backend / ".env").write_text(
        "FADEN_BACKEND_DB_HOST=localhost\n"
        "FADEN_BACKEND_RABBIT_HOST=localhost\n"
        "FADEN_BACKEND_DB_PASS=kept\n",
        encoding="utf-8",
    )

    dev_up._ensure_backend_env(backend, ws)

    env = _env(backend / ".env")
    assert env["FADEN_BACKEND_DB_HOST"] == "127.0.0.1"
    assert env["FADEN_BACKEND_RABBIT_HOST"] == "127.0.0.1"
    assert env["FADEN_BACKEND_DB_PASS"] == "kept"
    text = (backend / ".env").read_text(encoding="utf-8")
    assert text.count("FADEN_BACKEND_DB_HOST=") == 1, "rewritten in place, not appended"


def test_an_existing_gateway_env_is_moved_off_localhost(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    api = ws / "gateway"
    api.mkdir()
    (api / ".env").write_text(
        "FADEN_GATEWAY_DB_HOST=localhost\n"
        "FADEN_GATEWAY_REDIS_HOST=LOCALHOST\n"
        "FADEN_GATEWAY_RABBIT_HOST=localhost\n",
        encoding="utf-8",
    )

    dev_up._ensure_gateway_env(api, ws)

    env = _env(api / ".env")
    for key in ("FADEN_GATEWAY_DB_HOST", "FADEN_GATEWAY_REDIS_HOST", "FADEN_GATEWAY_RABBIT_HOST"):
        assert env[key] == "127.0.0.1", key
    assert (api / ".env").read_text(encoding="utf-8").count("FADEN_GATEWAY_DB_HOST=") == 1


def test_a_host_pointed_elsewhere_is_left_alone(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    api = ws / "gateway"
    api.mkdir()
    (api / ".env").write_text("FADEN_GATEWAY_DB_HOST=db.internal\n", encoding="utf-8")

    dev_up._ensure_gateway_env(api, ws)

    assert _env(api / ".env")["FADEN_GATEWAY_DB_HOST"] == "db.internal"
