"""Letting a cluster's machines download models from Hugging Face themselves.

By default a model reaches a cluster through the Fadenstack server: the
server downloads it, and each machine copies it from there. That works for a
cluster with no internet, and it is the default. But a server behind a slow
link makes every model pay twice: the release VM downloaded a 132 GB model at
12 MB/s, and would then have copied it to both DGX Sparks over the same link
-- nine hours -- while the Sparks had lines of their own.

With the cluster's switch on, each machine downloads the model from the Hub
into its model store (agent ``hf_download.py``, agent 0.2.2), with the
server's Hugging Face token for a gated one. The engine stays offline either
way: it loads what is in the store. The switch can be turned on only when
every machine of the cluster can reach the Hub -- each is asked
(``CHECK_INTERNET``) -- and runs an agent that knows how.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.models.inference import InferenceEnvironment, InferenceEnvironmentNode
from faden_backend.db.models.node_control import InfraNode, NodeCommandType

log = logging.getLogger(__name__)

#: ``config_json["artifacts"]`` key of the switch.
CONFIG_KEY = "download_on_machines"
#: ``model_sync.source`` telling a machine to download the model itself.
SOURCE = "hugging_face"
#: What an agent says it can do: download from the Hub, and answer CHECK_INTERNET (agent 0.2.2).
CAPABILITY = "model_download"
CAPABILITY_LEVEL = 1
#: How long a machine's answer is trusted before it is asked again.
CHECK_TTL_SEC = 300.0
#: How long the machines have to answer, together.
CHECK_BUDGET_SEC = 20.0


class MachineDownloadsError(Exception):
    """The switch cannot be turned on now; the message says why."""


@dataclass(frozen=True)
class MachineCheck:
    """Whether one machine can download a model from the Hub itself."""

    node_id: str
    host: str
    #: True / False; None when it could not be asked (offline, old agent, no answer).
    internet: bool | None
    detail: str | None
    checked_at: datetime
    #: Why not, for the console to say in its language: ``no_internet``,
    #: ``store_not_writable``, ``old_agent``, ``offline``, ``no_answer``; None when it can.
    problem: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "node_id": self.node_id,
            "host": self.host,
            "internet": self.internet,
            "detail": self.detail,
            "checked_at": self.checked_at.isoformat(),
            "problem": self.problem,
        }


#: node id -> (monotonic time, answer). Per process: a web worker asks at most every CHECK_TTL_SEC.
_CHECKS: dict[str, tuple[float, MachineCheck]] = {}


def enabled(environment: InferenceEnvironment | None) -> bool:
    """Whether *environment*'s machines download models themselves."""
    if environment is None:
        return False
    artifacts = (environment.config_json or {}).get("artifacts") or {}
    return bool(artifacts.get(CONFIG_KEY, False))


def sync_payload(model: Any) -> dict[str, Any] | None:
    """The ``model_sync`` telling a machine to download *model* from the Hub; None without a repository."""
    if not getattr(model, "hf_repo_id", None):
        return None
    return {
        "model_id": str(model.id),
        "hf_repo_id": model.hf_repo_id,
        "source": SOURCE,
        "revision": model.hf_revision or "main",
        "model_dir_name": f"models--{model.hf_repo_id.replace('/', '--')}",
    }


def supported(capabilities: dict | None) -> bool:
    """Whether a machine's agent downloads models from the Hub and answers CHECK_INTERNET."""
    try:
        return int((capabilities or {}).get(CAPABILITY) or 0) >= CAPABILITY_LEVEL
    except (TypeError, ValueError):
        return False


async def _members(session: AsyncSession, environment_id: Any) -> list[InfraNode]:
    return list((await session.execute(
        select(InfraNode)
        .join(InferenceEnvironmentNode, InferenceEnvironmentNode.node_id == InfraNode.id)
        .where(InferenceEnvironmentNode.environment_id == environment_id)
        .order_by(InfraNode.host)
    )).scalars().all())


def _answer(node: InfraNode, internet: bool | None, detail: str | None, problem: str | None = None) -> MachineCheck:
    if problem is None and internet is not True:
        problem = "no_internet" if internet is False else "no_answer"
    return MachineCheck(
        node_id=str(node.id), host=str(node.host or node.id), internet=internet, detail=detail,
        checked_at=datetime.now(tz=UTC), problem=problem,
    )


async def _ask(gateway: Any, node: InfraNode) -> MachineCheck:
    """Have *node* try to reach the Hub, and read its answer."""
    command = await gateway.issue(
        node_id=node.id,
        command_type=NodeCommandType.CHECK_INTERNET.value,
        payload={"timeout_sec": 8},
        idempotency_key=f"check-internet:{node.id}:{uuid.uuid4().hex}",
        timeout_sec=int(CHECK_BUDGET_SEC),
    )
    done = await gateway.wait(command.id, budget_sec=CHECK_BUDGET_SEC)
    if done is None:
        return _answer(node, None, "The machine did not answer in time.")
    result = done.result_json or {}
    if done.status != "succeeded":
        return _answer(node, None, done.error_message or "The machine could not check.")
    if result.get("store_problem"):
        # It reaches the Hub, but could not write the model where it keeps models.
        return _answer(node, False, str(result["store_problem"]), "store_not_writable")
    if result.get("reachable"):
        latency = result.get("latency_ms")
        return _answer(node, True, f"Reached {result.get('url')} in {latency} ms" if latency is not None else None)
    return _answer(node, False, str(result.get("error") or "Hugging Face could not be reached."))


async def check_machines(
    session: AsyncSession, gateway: Any, environment_id: Any, *, fresh: bool = False,
) -> list[MachineCheck]:
    """Whether each machine of the cluster can download from the Hub; asked at most every few minutes."""
    nodes = await _members(session, environment_id)
    now = time.monotonic()
    answers: dict[str, MachineCheck] = {}
    to_ask: list[InfraNode] = []
    for node in nodes:
        key = str(node.id)
        cached = _CHECKS.get(key)
        if str(node.status or "").lower() in ("offline", "disabled"):
            answers[key] = _answer(node, None, "The machine is offline.", "offline")
        elif not supported(node.capabilities_json):
            answers[key] = _answer(
                node, None, "Its agent is too old to download models itself: update it to 0.2.2 or later "
                "(re-run the install line on it).", "old_agent",
            )
        elif cached is not None and not fresh and now - cached[0] < CHECK_TTL_SEC:
            answers[key] = cached[1]
        else:
            to_ask.append(node)
    if to_ask:
        results = await asyncio.gather(*(_ask(gateway, node) for node in to_ask), return_exceptions=True)
        for node, result in zip(to_ask, results, strict=True):
            if isinstance(result, BaseException):
                log.warning("Could not ask %s whether it reaches Hugging Face: %s", node.host, result)
                result = _answer(node, None, f"Could not ask the machine: {result}")
            answers[str(node.id)] = result
            if result.internet is not None:
                _CHECKS[str(node.id)] = (now, result)
    return [answers[str(node.id)] for node in nodes]


def _blocker(checks: list[MachineCheck]) -> str | None:
    """Why the switch cannot be turned on, naming the machines; None when it can."""
    if not checks:
        return "The cluster has no machines."
    lacking = [check for check in checks if check.internet is not True]
    if not lacking:
        return None
    parts = [
        f"{check.host}: " + (
            (check.detail or "its model store is not writable") if check.problem == "store_not_writable"
            else f"cannot reach Hugging Face ({check.detail})" if check.internet is False
            else (check.detail or "could not be checked")
        )
        for check in lacking
    ]
    return "Every machine must reach Hugging Face for this. " + "; ".join(parts)


async def environment_status(
    session: AsyncSession, gateway: Any, environment_id: Any, *, fresh: bool = False,
) -> dict[str, Any]:
    """Whether the cluster's machines download models themselves, and whether they could."""
    env = await session.get(InferenceEnvironment, environment_id)
    if env is None:
        raise MachineDownloadsError("There is no such cluster.")
    checks = await check_machines(session, gateway, env.id, fresh=fresh)
    blocker = _blocker(checks)
    return {
        "on": enabled(env),
        "machines": [check.as_dict() for check in checks],
        "can_switch_on": blocker is None,
        "reason": blocker,
    }


async def switch(session: AsyncSession, gateway: Any, environment_id: Any, *, on: bool) -> dict[str, Any]:
    """Have the cluster's machines download models themselves, or copy them from the server again."""
    from faden_backend.services.inference.wakeup import wake_reconciler_after_commit  # noqa: PLC0415

    env = await session.get(InferenceEnvironment, environment_id)
    if env is None:
        raise MachineDownloadsError("There is no such cluster.")
    if on:
        blocker = _blocker(await check_machines(session, gateway, env.id, fresh=True))
        if blocker is not None:
            raise MachineDownloadsError(blocker)
    config = dict(env.config_json or {})
    artifacts = dict(config.get("artifacts") or {})
    if bool(artifacts.get(CONFIG_KEY, False)) != on:
        artifacts[CONFIG_KEY] = on
        config["artifacts"] = artifacts
        env.config_json = config
        await session.flush()
        wake_reconciler_after_commit(session)
        log.info(
            "Cluster %s: machines %s", env.name,
            "download models from Hugging Face themselves" if on else "copy models from the server again",
        )
    return await environment_status(session, gateway, env.id)


async def node_may_download(session: AsyncSession, node_id: Any) -> bool:
    """Whether *node* belongs to a cluster whose machines download models themselves."""
    envs = (await session.execute(
        select(InferenceEnvironment)
        .join(InferenceEnvironmentNode, InferenceEnvironmentNode.environment_id == InferenceEnvironment.id)
        .where(InferenceEnvironmentNode.node_id == node_id)
    )).scalars().all()
    return any(enabled(env) for env in envs)
