"""A cluster's machines download models from Hugging Face themselves, when they can.

Off by default: the server downloads a model and each machine copies it from
there, which works for a cluster with no internet. On the release VM that
meant a 132 GB model at the server's 12 MB/s, and then again to each DGX
Spark over the same link, while the Sparks had lines of their own. The switch
turns on only when every machine answers that it reaches the Hub.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from faden_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from faden_backend.db.models.node_control import InfraNode, NodeCommandType
from faden_backend.services.inference import machine_downloads
from faden_backend.services.inference.artifacts import ModelArtifactCoordinator

REPO = "nvidia/Qwen3.8-Flash-Next-NVFP4"


class _Machines:
    """Stands in for the command channel: each machine answers CHECK_INTERNET as told."""

    def __init__(self, answers: dict[str, dict[str, Any] | None]) -> None:
        self.answers = answers  # host -> result, or None for "no answer"
        self.asked: list[str] = []
        self._by_command: dict[uuid.UUID, str] = {}
        self._hosts: dict[str, str] = {}

    def know(self, node: InfraNode) -> None:
        self._hosts[str(node.id)] = str(node.host)

    async def issue(self, *, node_id: Any, command_type: str, **_: Any) -> Any:
        assert command_type == NodeCommandType.CHECK_INTERNET.value
        host = self._hosts[str(node_id)]
        self.asked.append(host)
        command_id = uuid.uuid4()
        self._by_command[command_id] = host
        return SimpleNamespace(id=command_id)

    async def wait(self, command_id: uuid.UUID, *, budget_sec: float) -> Any:
        answer = self.answers[self._by_command[command_id]]
        if answer is None:
            return None
        return SimpleNamespace(status="succeeded", result_json=answer, error_message=None)


@pytest.fixture(autouse=True)
def _no_cached_answers() -> None:
    machine_downloads._CHECKS.clear()


async def _cluster(
    session: AsyncSession, hosts: dict[str, int], *, on: bool = False,
) -> tuple[InferenceEnvironment, list[InfraNode]]:
    """A cluster of machines whose agents have the given ``model_download`` capability levels."""
    cp = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray")
    session.add(cp)
    await session.flush()
    nodes = []
    for host, level in hosts.items():
        node = InfraNode(
            agent_id=f"agent-{uuid.uuid4().hex[:8]}", host=host, status="healthy",
            capabilities_json={"model_download": level} if level else {},
        )
        session.add(node)
        nodes.append(node)
    await session.flush()
    env = InferenceEnvironment(
        control_plane_id=cp.id, name=f"dgx-{uuid.uuid4().hex[:6]}", head_node_id=nodes[0].id,
        config_json={"ray_tls": True, **({"artifacts": {machine_downloads.CONFIG_KEY: True}} if on else {})},
    )
    session.add(env)
    await session.flush()
    for i, node in enumerate(nodes):
        session.add(InferenceEnvironmentNode(environment_id=env.id, node_id=node.id, role="head" if i == 0 else "worker"))
    await session.flush()
    return env, nodes


REACHES = {"reachable": True, "url": "https://huggingface.co/api/models?limit=1", "latency_ms": 180}
BLOCKED = {"reachable": False, "error": "ConnectTimeout: timed out"}


async def test_it_is_off_until_switched_on(dbsession: AsyncSession) -> None:
    env, nodes = await _cluster(dbsession, {"10.250.10.71": 1, "10.250.10.49": 1})
    machines = _Machines({"10.250.10.71": REACHES, "10.250.10.49": REACHES})
    for node in nodes:
        machines.know(node)

    status = await machine_downloads.environment_status(dbsession, machines, env.id)

    assert status["on"] is False
    assert status["can_switch_on"] is True and status["reason"] is None
    assert [m["internet"] for m in status["machines"]] == [True, True]


async def test_a_machine_without_internet_keeps_the_switch_off_and_is_named(dbsession: AsyncSession) -> None:
    env, nodes = await _cluster(dbsession, {"10.250.10.71": 1, "10.250.10.49": 1})
    machines = _Machines({"10.250.10.71": REACHES, "10.250.10.49": BLOCKED})
    for node in nodes:
        machines.know(node)

    status = await machine_downloads.environment_status(dbsession, machines, env.id)
    assert status["can_switch_on"] is False
    assert "10.250.10.49" in status["reason"] and "ConnectTimeout" in status["reason"]
    assert {m["host"]: m["problem"] for m in status["machines"]} == {"10.250.10.49": "no_internet", "10.250.10.71": None}
    assert "10.250.10.71" not in status["reason"]

    with pytest.raises(machine_downloads.MachineDownloadsError, match="10.250.10.49"):
        await machine_downloads.switch(dbsession, machines, env.id, on=True)
    assert machine_downloads.enabled(env) is False


async def test_an_old_agent_is_not_asked_and_says_what_to_do(dbsession: AsyncSession) -> None:
    env, nodes = await _cluster(dbsession, {"10.250.10.71": 1, "10.250.10.49": 0})
    machines = _Machines({"10.250.10.71": REACHES})
    for node in nodes:
        machines.know(node)

    status = await machine_downloads.environment_status(dbsession, machines, env.id)

    assert machines.asked == ["10.250.10.71"]
    assert status["can_switch_on"] is False
    assert "0.2.2" in status["reason"] and "10.250.10.49" in status["reason"]
    assert {m["host"]: m["problem"] for m in status["machines"]} == {"10.250.10.71": None, "10.250.10.49": "old_agent"}


async def test_a_machine_that_does_not_answer_is_not_taken_for_online(dbsession: AsyncSession) -> None:
    env, nodes = await _cluster(dbsession, {"10.250.10.71": 1})
    machines = _Machines({"10.250.10.71": None})
    machines.know(nodes[0])
    status = await machine_downloads.environment_status(dbsession, machines, env.id)
    assert status["machines"][0]["internet"] is None
    assert status["can_switch_on"] is False


async def test_switching_on_asks_again_and_keeps_the_rest_of_the_config(dbsession: AsyncSession) -> None:
    env, nodes = await _cluster(dbsession, {"10.250.10.71": 1, "10.250.10.49": 1})
    machines = _Machines({"10.250.10.71": REACHES, "10.250.10.49": REACHES})
    for node in nodes:
        machines.know(node)
    await machine_downloads.environment_status(dbsession, machines, env.id)  # answers cached
    machines.asked.clear()

    status = await machine_downloads.switch(dbsession, machines, env.id, on=True)

    assert sorted(machines.asked) == ["10.250.10.49", "10.250.10.71"], "asked again, not from the cache"
    assert status["on"] is True
    assert env.config_json["ray_tls"] is True
    assert env.config_json["artifacts"][machine_downloads.CONFIG_KEY] is True

    status = await machine_downloads.switch(dbsession, machines, env.id, on=False)
    assert status["on"] is False and machine_downloads.enabled(env) is False


async def test_only_a_machine_of_such_a_cluster_gets_the_token(dbsession: AsyncSession) -> None:
    _env_on, on_nodes = await _cluster(dbsession, {"10.250.10.71": 1}, on=True)
    _env_off, off_nodes = await _cluster(dbsession, {"10.250.10.220": 1})
    assert await machine_downloads.node_may_download(dbsession, on_nodes[0].id) is True
    assert await machine_downloads.node_may_download(dbsession, off_nodes[0].id) is False


# -- what the machines are told --------------------------------------------------


async def _model(session: AsyncSession) -> LLMModel:
    model = LLMModel(
        display_name="Qwen3.8-Flash-Next-NVFP4", source=ModelSource.HUGGINGFACE, status=ModelStatus.FAILED,
        hf_repo_id=f"{REPO}-{uuid.uuid4().hex[:6]}", hf_revision=None,
    )
    session.add(model)
    await session.flush()
    return model


async def test_the_machines_are_told_to_download_it_themselves(
    dbsession: AsyncSession, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Not the server's half-downloaded copy, and no server download started."""
    from faden_backend.settings import settings
    from faden_backend.services.llm import tasks as llm_tasks

    monkeypatch.setattr(settings, "model_store_root", str(tmp_path))
    started = AsyncMock()
    monkeypatch.setattr(llm_tasks.download_model_task, "kiq", started)
    env, nodes = await _cluster(dbsession, {"10.250.10.71": 1, "10.250.10.49": 1}, on=True)
    model = await _model(dbsession)
    blobs = tmp_path / f"models--{model.hf_repo_id.replace('/', '--')}" / "blobs"
    blobs.mkdir(parents=True)
    (blobs / "63fde954").write_bytes(b"x")  # the half the server had
    (blobs / "4dafaef6.incomplete").write_bytes(b"x")

    gateway = AsyncMock()
    readiness = await ModelArtifactCoordinator(dbsession).ensure(
        model=model, environment=env, gateway=gateway, fetch_to_server=True,
    )

    assert readiness.manifest_sha256 is None, "the server's copy says nothing about the machines'"
    sent = [call.kwargs for call in gateway.issue_command.await_args_list]
    assert {c["node_id"] for c in sent} == {n.id for n in nodes}
    for command in sent:
        assert command["command_type"] == NodeCommandType.SYNC_MODEL.value
        sync = command["payload"]["model_sync"]
        assert sync["source"] == machine_downloads.SOURCE and sync["hf_repo_id"] == model.hf_repo_id
        assert sync["revision"] == "main" and "blobs" not in sync
        assert command["idempotency_key"].startswith(f"artifact:{model.id}:hf:main:")
    started.assert_not_awaited()
    assert readiness.waiting_on is not None and "from Hugging Face themselves" in readiness.waiting_on
