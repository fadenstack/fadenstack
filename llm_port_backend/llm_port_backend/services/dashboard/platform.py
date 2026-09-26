"""The platform's own state: its dependencies and the host it runs on.

Shared by ``/dashboard/health`` and the attention rules. The dependency
probes are local and short (a TCP connect, one HTTP GET with a 2 s timeout)
and run concurrently; callers put the result through the fact cache.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_port_backend.services.tls import default_httpx_verify
from llm_port_backend.settings import settings


@dataclass
class Dependency:
    """One dependency's state: up, down, degraded or unknown."""

    name: str
    status: str
    detail: str | None = None


async def tcp_up(host: str, port: int, timeout: float = 0.8) -> bool:
    """Whether something accepts a TCP connection at host:port."""
    try:
        _reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout=timeout)
        writer.close()
        await writer.wait_closed()
        return True
    except Exception:
        return False


async def _postgres(db_factory: Callable[[], AsyncSession] | None) -> Dependency:
    ok = False
    if db_factory is not None:
        with contextlib.suppress(Exception):
            async with db_factory() as session:
                ok = int((await session.execute(text("SELECT 1"))).scalar_one()) == 1
    return Dependency("postgres", "up" if ok else "down")


async def _rabbitmq() -> Dependency:
    return Dependency(
        "rabbitmq", "up" if await tcp_up(settings.rabbit_host, settings.rabbit_port) else "down"
    )


async def _grafana() -> Dependency:
    if not settings.grafana_url:
        return Dependency("grafana", "unknown")
    try:
        async with httpx.AsyncClient(verify=default_httpx_verify(), timeout=2.0) as client:
            resp = await client.get(str(settings.grafana_url))
        return Dependency(
            "grafana", "up" if resp.status_code < 500 else "degraded", f"http {resp.status_code}"
        )
    except Exception as exc:  # any failure is the answer
        return Dependency("grafana", "down", str(exc))


async def dependencies(db_factory: Callable[[], AsyncSession] | None) -> list[Dependency]:
    """Postgres, RabbitMQ and Grafana, probed together."""
    return list(await asyncio.gather(_postgres(db_factory), _rabbitmq(), _grafana()))


def disk_free_percent() -> float | None:
    """Free space on the root filesystem of the machine the backend runs on."""
    try:
        usage = shutil.disk_usage(Path(os.sep).resolve())
    except OSError:
        return None
    return usage.free / usage.total * 100.0 if usage.total else None


async def containers_restarting(docker: Any) -> int | None:
    """Containers Docker reports as restarting; None when Docker cannot be asked."""
    if docker is None:
        return None
    try:
        containers = await asyncio.wait_for(docker.list_containers(all_=True), timeout=5.0)
    except Exception:
        return None
    return sum(1 for c in containers if str(c.get("State", "")).lower() == "restarting")
