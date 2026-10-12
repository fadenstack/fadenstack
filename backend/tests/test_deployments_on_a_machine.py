"""A machine's page lists the deployments it serves: those on a cluster it is a member of.

It showed the machine's latest ``deploy_workload`` command instead -- a
container test that had failed three days before, beside a model serving on
the same machine through Ray, which sends no such command.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.db.dao.inference_dao import DeploymentDAO
from faden_backend.db.models.inference import (
    InferenceControlPlane,
    InferenceDeployment,
    InferenceEnvironment,
    InferenceEnvironmentNode,
)
from faden_backend.db.models.llm import LLMModel, ModelSource, ModelStatus
from faden_backend.db.models.node_control import InfraNode

pytestmark = pytest.mark.anyio()


async def test_only_deployments_on_a_cluster_the_machine_is_in(dbsession: AsyncSession) -> None:
    plane = InferenceControlPlane(name=f"cp-{uuid.uuid4().hex[:8]}", driver="ray")
    model = LLMModel(display_name="Qwen3.8", source=ModelSource.HUGGINGFACE, status=ModelStatus.AVAILABLE,
                     hf_repo_id=f"nvidia/q-{uuid.uuid4().hex[:6]}")
    spark, workstation = (InfraNode(agent_id=f"a-{uuid.uuid4().hex[:8]}", host=h, status="healthy")
                          for h in ("192.0.2.49", "192.0.2.20"))
    dbsession.add_all([plane, model, spark, workstation])
    await dbsession.flush()

    deployments = {}
    for member, name in ((spark, "on-the-pair"), (workstation, "elsewhere")):
        env = InferenceEnvironment(control_plane_id=plane.id, name=f"env-{uuid.uuid4().hex[:6]}", head_node_id=member.id)
        dbsession.add(env)
        await dbsession.flush()
        dbsession.add(InferenceEnvironmentNode(environment_id=env.id, node_id=member.id, role="head"))
        deployment = InferenceDeployment(environment_id=env.id, model_id=model.id, name=f"{name}-{uuid.uuid4().hex[:6]}",
                                         spec_json={}, phase="running")
        dbsession.add(deployment)
        deployments[name] = deployment
    await dbsession.flush()

    found = await DeploymentDAO(dbsession).list_all(node_id=spark.id)
    assert [d.id for d in found] == [deployments["on-the-pair"].id]
