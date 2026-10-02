"""Unit tests for generic InferencePublicationCoordinator (Phase 5)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock
import pytest

from faden_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EndpointStatus,
    InferenceDeployment,
    InferenceEndpoint,
)
from faden_backend.services.inference.publication import (
    InferencePublicationCoordinator,
    gateway_concurrency,
)


@pytest.fixture
def mock_session():
    return AsyncMock()


@pytest.fixture
def mock_gateway_sync():
    sync = MagicMock()
    sync.enabled = True
    sync.publish_inference_endpoint = AsyncMock()
    sync.deactivate_source = AsyncMock()
    sync.reactivate_source = AsyncMock()
    sync.retire_source = AsyncMock()
    sync.set_source_health = AsyncMock()
    return sync


@pytest.mark.anyio
async def test_reconcile_publication_published_endpoint(mock_session, mock_gateway_sync):
    """A RUNNING deployment with a PUBLISHED endpoint is published to the gateway as healthy."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="meta-llama-3-8b",
        desired_state=DeploymentDesiredState.ACTIVE,
        phase=DeploymentPhase.RUNNING,
        ready_replicas=2,
        total_replicas=2,
        spec_json={"service": {"alias": "llama3", "path": "/v1"}},
    )
    endpoint = InferenceEndpoint(
        id=uuid.uuid4(),
        deployment_id=dep_id,
        name="openai",
        address="http://10.250.10.49:8000/faden-dep1",
        path="/v1",
        status=EndpointStatus.PUBLISHED,
        published_json={
            "model": "meta-llama/Meta-Llama-3-8B-Instruct",
            "base_url": "http://10.250.10.49:8000/faden-dep1/v1",
        },
    )

    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    coordinator.endpoint_dao.list_for_deployment = AsyncMock(return_value=[endpoint])

    await coordinator.reconcile_deployment_publication(deployment)

    mock_gateway_sync.publish_inference_endpoint.assert_awaited_once_with(
        deployment_id=dep_id,
        endpoint_id=endpoint.id,
        base_url="http://10.250.10.49:8000/faden-dep1/v1",
        alias="llama3",
        served_model_name="meta-llama/Meta-Llama-3-8B-Instruct",
        backend_provider_type="vllm",
        health_status="healthy",
        is_routable=True,
        max_concurrency=2 * 256,  # two copies at vLLM's default max_num_seqs
        capacity_hints=None,  # no wait of its own: the gateway's default
        tools=False,  # no engine config: vLLM's tool calls are off
        machine_tls=False,  # published plain: no machine_tls in the endpoint
    )


@pytest.mark.anyio
async def test_reconcile_publication_degraded_deployment_with_ready_replica(mock_session, mock_gateway_sync):
    """A DEGRADED deployment with ready_replicas > 0 remains routable on the gateway."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="qwen-coder",
        desired_state=DeploymentDesiredState.ACTIVE,
        phase=DeploymentPhase.DEGRADED,
        ready_replicas=1,  # 1 of 2 replicas ready
        total_replicas=2,
        spec_json={},
    )
    endpoint = InferenceEndpoint(
        id=uuid.uuid4(),
        deployment_id=dep_id,
        name="openai",
        address="http://10.250.10.49:8000/faden-dep2",
        path="/v1",
        status=EndpointStatus.PUBLISHED,
        published_json={"model": "Qwen/Qwen2.5-Coder-1.5B-Instruct"},
    )

    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    coordinator.endpoint_dao.list_for_deployment = AsyncMock(return_value=[endpoint])

    await coordinator.reconcile_deployment_publication(deployment)

    # Must be published as healthy and routable without inventing an alias
    mock_gateway_sync.publish_inference_endpoint.assert_awaited_once_with(
        deployment_id=dep_id,
        endpoint_id=endpoint.id,
        base_url="http://10.250.10.49:8000/faden-dep2/v1",
        alias=None,
        served_model_name="Qwen/Qwen2.5-Coder-1.5B-Instruct",
        backend_provider_type="vllm",
        health_status="healthy",
        is_routable=True,
        max_concurrency=256,  # only the ready copy counts
        capacity_hints=None,
        tools=False,  # no engine config: vLLM's tool calls are off
        machine_tls=False,  # published plain: no machine_tls in the endpoint
    )


@pytest.mark.anyio
async def test_reconcile_publication_carries_the_models_wait_for_a_reply(mock_session, mock_gateway_sync):
    """The deployment's own first-token wait goes to the routing row the gateway reads per request."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="qwen-27b",
        desired_state=DeploymentDesiredState.ACTIVE,
        phase=DeploymentPhase.RUNNING,
        ready_replicas=2,
        total_replicas=2,
        spec_json={"service": {"alias": "qwen", "first_token_timeout_sec": 300}},
    )
    endpoint = InferenceEndpoint(
        id=uuid.uuid4(),
        deployment_id=dep_id,
        name="openai",
        address="http://10.250.10.49:8000/faden-dep3",
        path="/v1",
        status=EndpointStatus.PUBLISHED,
        published_json={"model": "Qwen/Qwen3.8-27B-FP8"},
    )
    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    coordinator.endpoint_dao.list_for_deployment = AsyncMock(return_value=[endpoint])

    await coordinator.reconcile_deployment_publication(deployment)

    sent = mock_gateway_sync.publish_inference_endpoint.await_args.kwargs
    assert sent["capacity_hints"] == {"first_token_timeout_sec": 300.0}


@pytest.mark.parametrize(
    ("ready", "config", "expected"),
    [
        (3, {"max_num_seqs": 128}, 384),  # the 27B on the DGX pair: 16 refused its 17th chat
        (1, {"max_num_seqs": "64"}, 64),  # a number typed as text still counts
        (2, {}, 512),  # unset: vLLM's default
        (2, {"max_num_seqs": 0}, 512),
        (2, {"max_num_seqs": "many"}, 512),
        (0, {"max_num_seqs": 32}, 32),  # never below one copy's worth
    ],
)
def test_gateway_concurrency_is_ready_copies_times_their_batch(ready, config, expected):
    """The gateway lets through what the ready copies batch, not a fixed 16."""
    deployment = InferenceDeployment(
        id=uuid.uuid4(),
        name="qwen",
        ready_replicas=ready,
        spec_json={"engine": {"name": "vllm", "config": config}},
    )
    assert gateway_concurrency(deployment) == expected


@pytest.mark.anyio
async def test_reconcile_publication_stopped_deployment(mock_session, mock_gateway_sync):
    """Stopping a deployment soft-deactivates the source without destroying alias/membership."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="meta-llama-3-8b",
        desired_state=DeploymentDesiredState.STOPPED,
        phase=DeploymentPhase.STOPPED,
        ready_replicas=0,
    )

    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    await coordinator.reconcile_deployment_publication(deployment)

    mock_gateway_sync.deactivate_source.assert_awaited_once_with(
        source_kind="inference_deployment",
        source_id=dep_id,
    )
    mock_gateway_sync.publish_inference_endpoint.assert_not_awaited()


@pytest.mark.anyio
async def test_reconcile_publication_deleted_deployment(mock_session, mock_gateway_sync):
    """Deleting a deployment marks the source retired."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="meta-llama-3-8b",
        desired_state=DeploymentDesiredState.DELETED,
        phase=DeploymentPhase.DELETED,
        ready_replicas=0,
    )

    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    await coordinator.reconcile_deployment_publication(deployment)

    mock_gateway_sync.retire_source.assert_awaited_once_with(
        source_kind="inference_deployment",
        source_id=dep_id,
    )


@pytest.mark.anyio
async def test_reconcile_publication_unhealthy_endpoint_zero_replicas(mock_session, mock_gateway_sync):
    """Endpoint with 0 ready replicas is marked unhealthy and disabled."""
    dep_id = uuid.uuid4()
    deployment = InferenceDeployment(
        id=dep_id,
        name="meta-llama-3-8b",
        desired_state=DeploymentDesiredState.ACTIVE,
        phase=DeploymentPhase.FAILED,
        ready_replicas=0,
        total_replicas=1,
    )
    endpoint = InferenceEndpoint(
        id=uuid.uuid4(),
        deployment_id=dep_id,
        name="openai",
        address="http://10.250.10.49:8000/app",
        status=EndpointStatus.FAILED,
    )

    coordinator = InferencePublicationCoordinator(mock_session, gateway_sync=mock_gateway_sync)
    coordinator.endpoint_dao.list_for_deployment = AsyncMock(return_value=[endpoint])

    await coordinator.reconcile_deployment_publication(deployment)

    mock_gateway_sync.set_source_health.assert_awaited_once_with(
        source_kind="inference_deployment",
        source_id=dep_id,
        health_status="unhealthy",
        enabled=False,
    )

