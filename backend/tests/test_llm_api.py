"""Tier 2 API tests: hermetic DAO-direct slice of the /api/llm/* endpoints.

Requires live Postgres (settings.db_url) + RabbitMQ, same as
test_system_settings_api.py — written to compile and collect cleanly now;
executed once a stack is running.

Scope (docs/backend-test-gap-analysis.md, Tier 2):
    providers  GET / / {id} / PATCH {id}  (incl. cascade remote_model rename)
    models     GET / (with instances) / {id} / {id}/artifacts
    jobs       GET / (status + model_id filters) / {id} / POST {id}/cancel

Deliberately EXCLUDED:
    * LLMService-backed endpoints (create/delete provider, download/register
      model, delete model, runtimes) — ``app.state.llm_service`` is only set
      in ``lifespan_setup`` and ASGITransport does not run lifespans.
    * POST /jobs/{id}/retry — dispatches a taskiq task (needs real broker +
      model_store_root wiring).

Hermeticity: every endpoint under test only uses DAO add/flush operations
plus the add-only ``audit_action`` path — no commits — so the savepoint
rollback ``dbsession`` fixture isolates each test cleanly. Superuser
endpoints that write audit rows seed REAL users (audit_events.actor_id is
an FK to users.id, SET NULL). ``created_at``/``updated_at`` use
``server_default=func.now()``, so seeded rows are refreshed after flush
before DTO serialization.
"""

import uuid
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.llm_dao import ArtifactDAO, ModelDAO, ProviderDAO
from faden_backend.db.models.llm import (
    DownloadJob,
    DownloadJobStatus,
    LLMModel,
    LLMProvider,
    LLMRuntime,
    ModelSource,
    ModelStatus,
    ProviderTarget,
    ProviderType,
    RuntimeStatus,
)
from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.llm.gateway_sync import GatewaySyncService
from faden_backend.services.llm.service import LLMService
from faden_backend.web.api.llm.dependencies import get_llm_service


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


async def _seed_user(dbsession: AsyncSession, *, is_superuser: bool, email: str) -> User:
    """Insert a real user row (audit_events.actor_id is an FK to users.id)."""
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password="not-a-real-hash",
        is_active=True,
        is_verified=True,
        is_superuser=is_superuser,
    )
    dbsession.add(user)
    await dbsession.flush()
    return user


@pytest.fixture(autouse=True)
def llm_service(fastapi_app: FastAPI) -> LLMService:
    """The service the app holds once started; the test app is never started."""
    service = LLMService(SimpleNamespace(), gateway_sync=GatewaySyncService(None))  # type: ignore[arg-type]
    fastapi_app.dependency_overrides[get_llm_service] = lambda: service
    return service


@pytest.fixture
async def superuser(dbsession) -> User:
    return await _seed_user(dbsession, is_superuser=True, email="superuser@example.com")


@pytest.fixture
async def regular_user(dbsession) -> User:
    return await _seed_user(dbsession, is_superuser=False, email="regular@example.com")


# ---------------------------------------------------------------------------
# Seed helpers (flush only — the dbsession savepoint rolls back per test)
# ---------------------------------------------------------------------------


async def _seed_provider(dbsession: AsyncSession, **overrides) -> LLMProvider:
    base = dict(
        name="vllm-local",
        type_=ProviderType.VLLM,
        target=ProviderTarget.LOCAL_DOCKER,
        endpoint_url="http://vllm:8000/v1",
        capabilities={"max_context": 8192},
        extra_params={},
    )
    base.update(overrides)
    provider = await ProviderDAO(session=dbsession).create(**base)
    await dbsession.refresh(provider)
    return provider


async def _seed_model(dbsession: AsyncSession, **overrides) -> LLMModel:
    base = dict(
        display_name="llama-3-8b",
        source=ModelSource.HUGGINGFACE,
        hf_repo_id="huggingface.co/llama-3-8b",
        hf_revision="main",
        license_ack_required=False,
        tags=["open-weight"],
        status=ModelStatus.AVAILABLE,
    )
    base.update(overrides)
    model = await ModelDAO(session=dbsession).create(**base)
    await dbsession.refresh(model)
    return model


async def _seed_runtime(dbsession: AsyncSession, **overrides) -> LLMRuntime:
    base = dict(
        id=uuid.uuid4(),
        name="runtime-1",
        provider_id=None,
        model_id=None,
        status=RuntimeStatus.RUNNING,
        desired_state="running",
        execution_target="local",
        openai_compat=True,
    )
    base.update(overrides)
    runtime = LLMRuntime(**base)
    dbsession.add(runtime)
    await dbsession.flush()
    await dbsession.refresh(runtime)
    return runtime


async def _seed_job(dbsession: AsyncSession, **overrides) -> DownloadJob:
    base = dict(id=uuid.uuid4(), model_id=None)
    base.update(overrides)
    job = DownloadJob(**base)
    dbsession.add(job)
    await dbsession.flush()
    await dbsession.refresh(job)
    return job


async def _seed_artifact(dbsession: AsyncSession, model_id, **overrides):
    base = dict(format="safetensors", path="/models/m/weights.safetensors", size_bytes=1024)
    base.update(overrides)
    artifacts = await ArtifactDAO(session=dbsession).create_batch(model_id, [base])
    await dbsession.refresh(artifacts[0])
    return artifacts[0]


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------


async def test_list_providers_empty(client: AsyncClient, fastapi_app: FastAPI, superuser) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get("/api/llm/providers")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == []


async def test_list_providers_seeded(client, fastapi_app, dbsession, superuser) -> None:
    p = await _seed_provider(
        dbsession,
        name="cloud-provider",
        type_=ProviderType.CLOUD,
        endpoint_url="https://api.example.com",
    )
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get("/api/llm/providers")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == str(p.id)
    assert data[0]["name"] == "cloud-provider"
    assert data[0]["type"] == ProviderType.CLOUD.value
    assert data[0]["endpoint_url"] == "https://api.example.com"
    assert data[0]["remote_model"] is None
    assert data[0]["created_at"] is not None


async def test_get_provider_ok(client, fastapi_app, dbsession, superuser) -> None:
    p = await _seed_provider(dbsession, name="ollama-local", type_=ProviderType.OLLAMA)
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get(f"/api/llm/providers/{p.id}")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["name"] == "ollama-local"


async def test_get_provider_missing(client, fastapi_app, superuser) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get("/api/llm/providers/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.json()["detail"] == "Provider not found"


async def test_patch_provider_name_and_endpoint(client, fastapi_app, dbsession, superuser) -> None:
    p = await _seed_provider(dbsession, name="old-name", endpoint_url="http://old:8000/v1")
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.patch(
        f"/api/llm/providers/{p.id}",
        json={"name": "new-name", "endpoint_url": "http://new:8000/v1"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["name"] == "new-name"
    # Normalised as on create: the gateway appends /v1/... to it.
    assert resp.json()["endpoint_url"] == "http://new:8000"
    # untouched fields are preserved
    assert resp.json()["type"] == ProviderType.VLLM.value
    assert resp.json()["target"] == ProviderTarget.LOCAL_DOCKER.value


async def test_patch_provider_missing(client, fastapi_app, superuser) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.patch(
        "/api/llm/providers/00000000-0000-0000-0000-000000000000", json={"name": "x"}
    )
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.json()["detail"] == "Provider not found"


async def test_patch_provider_cascade_rename(client, fastapi_app, dbsession, superuser) -> None:
    """Renaming the remote_model of a REMOTE_ENDPOINT provider cascades the
    rename to the still-matching auto-provisioned model + runtime."""
    provider = await _seed_provider(
        dbsession,
        name="remote",
        type_=ProviderType.CLOUD,
        target=ProviderTarget.REMOTE_ENDPOINT,
        endpoint_url="https://remote.example.com/v1",
        capabilities={"remote_model": "mistral-small"},
    )
    model = await _seed_model(
        dbsession,
        display_name="mistral-small",
        source=ModelSource.REMOTE,
        tags=["remote", "auto-provisioned"],
        status=ModelStatus.AVAILABLE,
    )
    runtime = await _seed_runtime(
        dbsession,
        name="mistral-small",
        provider_id=provider.id,
        model_id=model.id,
    )

    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.patch(
        f"/api/llm/providers/{provider.id}",
        json={"remote_model": "gpt-4o-mini"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["remote_model"] == "gpt-4o-mini"

    # cascade applied to the auto-provisioned model + runtime
    fresh_model = (
        await dbsession.execute(select(LLMModel).where(LLMModel.id == model.id))
    ).scalar_one()
    fresh_runtime = (
        await dbsession.execute(select(LLMRuntime).where(LLMRuntime.id == runtime.id))
    ).scalar_one()
    assert fresh_model.display_name == "gpt-4o-mini"
    assert fresh_runtime.name == "gpt-4o-mini"


async def test_patch_provider_no_cascade_for_local_target(
    client, fastapi_app, dbsession, superuser
) -> None:
    """LOCAL_DOCKER targets never cascade, even with matching names."""
    provider = await _seed_provider(
        dbsession,
        name="local-vllm",
        target=ProviderTarget.LOCAL_DOCKER,
        capabilities={"remote_model": "llama-3-8b"},
    )
    model = await _seed_model(
        dbsession,
        display_name="llama-3-8b",
        source=ModelSource.HUGGINGFACE,
        tags=["remote", "auto-provisioned"],
    )
    runtime = await _seed_runtime(
        dbsession, name="llama-3-8b", provider_id=provider.id, model_id=model.id
    )

    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.patch(
        f"/api/llm/providers/{provider.id}",
        json={"remote_model": "llama-3-70b"},
    )
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["remote_model"] == "llama-3-70b"
    fresh_model = (
        await dbsession.execute(select(LLMModel).where(LLMModel.id == model.id))
    ).scalar_one()
    fresh_runtime = (
        await dbsession.execute(select(LLMRuntime).where(LLMRuntime.id == runtime.id))
    ).scalar_one()
    assert fresh_model.display_name == "llama-3-8b"
    assert fresh_runtime.name == "llama-3-8b"


async def test_patch_provider_no_cascade_when_model_custom_named(
    client, fastapi_app, dbsession, superuser
) -> None:
    """A model whose display_name was customized keeps its name; a runtime
    still named after the old remote model is still renamed."""
    provider = await _seed_provider(
        dbsession,
        name="remote2",
        target=ProviderTarget.REMOTE_ENDPOINT,
        endpoint_url="https://r2.example.com/v1",
        capabilities={"remote_model": "gpt-4o"},
    )
    model = await _seed_model(
        dbsession,
        display_name="my-custom-name",  # != remote model id
        source=ModelSource.REMOTE,
        tags=["remote", "auto-provisioned"],
        status=ModelStatus.AVAILABLE,
    )
    runtime = await _seed_runtime(
        dbsession, name="gpt-4o", provider_id=provider.id, model_id=model.id
    )

    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.patch(
        f"/api/llm/providers/{provider.id}",
        json={"remote_model": "gpt-4o-mini"},
    )
    assert resp.status_code == status.HTTP_200_OK
    fresh_model = (
        await dbsession.execute(select(LLMModel).where(LLMModel.id == model.id))
    ).scalar_one()
    fresh_runtime = (
        await dbsession.execute(select(LLMRuntime).where(LLMRuntime.id == runtime.id))
    ).scalar_one()
    assert fresh_model.display_name == "my-custom-name"
    assert fresh_runtime.name == "gpt-4o-mini"


async def _litellm_remote(dbsession: AsyncSession, *, litellm_model: str, remote_model: str | None):
    provider = await _seed_provider(
        dbsession,
        name="OpenRouter",
        type_=ProviderType.CLOUD,
        target=ProviderTarget.REMOTE_ENDPOINT,
        litellm_provider="openrouter",
        litellm_model=litellm_model,
        capabilities={"remote": True, **({"remote_model": remote_model} if remote_model else {})},
    )
    name = remote_model or litellm_model
    model = await _seed_model(
        dbsession, display_name=name, source=ModelSource.REMOTE,
        tags=["remote", "auto-provisioned"], status=ModelStatus.AVAILABLE,
    )
    runtime = await _seed_runtime(dbsession, name=name, provider_id=provider.id, model_id=model.id)
    return provider, runtime


async def test_renaming_a_litellm_model_asks_for_the_new_one_upstream(
    client, fastapi_app, dbsession, superuser, llm_service, monkeypatch,
) -> None:
    """Found on the release VM: the alias became "deepseek/..." while OpenRouter was still asked for nemotron."""
    published: list[dict] = []

    async def publish(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(llm_service.gateway_sync, "publish_runtime", publish)
    provider, runtime = await _litellm_remote(
        dbsession, litellm_model="nvidia/nemotron-3-super-120b-a12b", remote_model="nvidia/nemotron-3-super-120b-a12b",
    )

    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.patch(
        f"/api/llm/providers/{provider.id}", json={"remote_model": "deepseek/deepseek-v4.1-flash"},
    )
    assert resp.status_code == status.HTTP_200_OK
    fresh = (await dbsession.execute(select(LLMProvider).where(LLMProvider.id == provider.id))).scalar_one()
    assert fresh.litellm_model == "deepseek/deepseek-v4.1-flash"
    assert runtime.name == "deepseek/deepseek-v4.1-flash"
    # Published again under the new name, which drops the old one (gateway_sync).
    assert [(p["alias"], p["litellm_model"]) for p in published] == [
        ("deepseek/deepseek-v4.1-flash", "deepseek/deepseek-v4.1-flash"),
    ]


async def test_a_provider_named_after_its_litellm_model_is_renamed_too(
    client, fastapi_app, dbsession, superuser, llm_service, monkeypatch,
) -> None:
    """Made without a remote model: the runtime and alias carry the LiteLLM model's name."""
    monkeypatch.setattr(llm_service.gateway_sync, "publish_runtime", _noop_publish)
    provider, runtime = await _litellm_remote(dbsession, litellm_model="openai/gpt-4.1-mini", remote_model=None)

    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.patch(f"/api/llm/providers/{provider.id}", json={"remote_model": "openai/gpt-4.1"})
    assert resp.status_code == status.HTTP_200_OK
    fresh = (await dbsession.execute(select(LLMProvider).where(LLMProvider.id == provider.id))).scalar_one()
    assert fresh.litellm_model == "openai/gpt-4.1"
    assert runtime.name == "openai/gpt-4.1"


async def test_a_litellm_model_set_apart_from_the_name_is_kept(
    client, fastapi_app, dbsession, superuser, llm_service, monkeypatch,
) -> None:
    monkeypatch.setattr(llm_service.gateway_sync, "publish_runtime", _noop_publish)
    provider, runtime = await _litellm_remote(
        dbsession, litellm_model="anthropic/claude-sonnet-4", remote_model="sonnet",
    )

    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.patch(f"/api/llm/providers/{provider.id}", json={"remote_model": "sonnet-4"})
    assert resp.status_code == status.HTTP_200_OK
    fresh = (await dbsession.execute(select(LLMProvider).where(LLMProvider.id == provider.id))).scalar_one()
    assert fresh.litellm_model == "anthropic/claude-sonnet-4"
    assert runtime.name == "sonnet-4"


async def _noop_publish(**_kwargs) -> None:
    return None


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


async def test_list_models_empty(client: AsyncClient, fastapi_app: FastAPI, superuser) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get("/api/llm/models")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == []


async def test_list_models_with_instances(client, fastapi_app, dbsession, superuser) -> None:
    provider = await _seed_provider(dbsession)
    model = await _seed_model(dbsession)
    await _seed_runtime(
        dbsession,
        name="my-runtime",
        provider_id=provider.id,
        model_id=model.id,
        status=RuntimeStatus.RUNNING,
    )
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get("/api/llm/models")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data) == 1
    entry = data[0]
    assert entry["id"] == str(model.id)
    assert entry["display_name"] == "llama-3-8b"
    assert entry["source"] == ModelSource.HUGGINGFACE.value
    assert len(entry["instances"]) == 1
    inst = entry["instances"][0]
    assert inst["runtime_name"] == "my-runtime"
    assert inst["runtime_status"] == RuntimeStatus.RUNNING.value
    assert inst["provider_name"] == "vllm-local"
    assert inst["provider_type"] == ProviderType.VLLM.value
    assert inst["execution_target"] == "local"
    assert inst["node_id"] is None  # unassigned to a node


async def test_get_model_ok(client, fastapi_app, dbsession, superuser) -> None:
    model = await _seed_model(dbsession)
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get(f"/api/llm/models/{model.id}")
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["id"] == str(model.id)
    assert body["display_name"] == "llama-3-8b"
    assert body["source"] == ModelSource.HUGGINGFACE.value
    assert body["hf_repo_id"] == "huggingface.co/llama-3-8b"
    assert body["tags"] == ["open-weight"]
    assert body["status"] == ModelStatus.AVAILABLE.value


async def test_get_model_missing(client, fastapi_app, superuser) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get("/api/llm/models/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.json()["detail"] == "Model not found"


async def test_list_artifacts(client, fastapi_app, dbsession, superuser) -> None:
    model = await _seed_model(dbsession)
    await _seed_artifact(
        dbsession,
        model.id,
        format="safetensors",
        path="/m/a.safetensors",
        size_bytes=2048,
        sha256="abc123",
    )
    await _seed_artifact(
        dbsession, model.id, format="gguf", path="/m/b.gguf", size_bytes=4096
    )

    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get(f"/api/llm/models/{model.id}/artifacts")
    assert resp.status_code == status.HTTP_200_OK
    data = resp.json()
    assert len(data) == 2
    by_format = {a["format"]: a for a in data}
    assert by_format["safetensors"]["path"] == "/m/a.safetensors"
    assert by_format["safetensors"]["size_bytes"] == 2048
    assert by_format["safetensors"]["sha256"] == "abc123"
    assert by_format["gguf"]["path"] == "/m/b.gguf"
    for a in data:
        assert a["model_id"] == str(model.id)
        assert a["created_at"] is not None


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------


async def test_list_jobs_empty(client: AsyncClient, fastapi_app: FastAPI, superuser) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get("/api/llm/jobs")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json() == []


async def test_list_jobs_status_filter(client, fastapi_app, dbsession, superuser) -> None:
    model_a = await _seed_model(dbsession)
    model_b = await _seed_model(dbsession, display_name="other-model")
    job_a = await _seed_job(dbsession, model_id=model_a.id, status=DownloadJobStatus.RUNNING)
    await _seed_job(dbsession, model_id=model_a.id, status=DownloadJobStatus.FAILED)
    await _seed_job(dbsession, model_id=model_b.id, status=DownloadJobStatus.SUCCESS)

    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get("/api/llm/jobs", params={"status": "running"})
    assert resp.status_code == status.HTTP_200_OK
    assert [j["id"] for j in resp.json()] == [str(job_a.id)]


async def test_list_jobs_model_id_filter(client, fastapi_app, dbsession, superuser) -> None:
    model_a = await _seed_model(dbsession)
    model_b = await _seed_model(dbsession, display_name="other-model")
    job_a = await _seed_job(dbsession, model_id=model_a.id, status=DownloadJobStatus.RUNNING)
    job_a_failed = await _seed_job(
        dbsession, model_id=model_a.id, status=DownloadJobStatus.FAILED
    )
    await _seed_job(dbsession, model_id=model_b.id, status=DownloadJobStatus.SUCCESS)

    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get("/api/llm/jobs", params={"model_id": str(model_a.id)})
    assert resp.status_code == status.HTTP_200_OK
    assert {j["id"] for j in resp.json()} == {str(job_a.id), str(job_a_failed.id)}


async def test_list_jobs_combined_filters(client, fastapi_app, dbsession, superuser) -> None:
    model_a = await _seed_model(dbsession)
    model_b = await _seed_model(dbsession, display_name="other-model")
    job_a = await _seed_job(dbsession, model_id=model_a.id, status=DownloadJobStatus.RUNNING)
    await _seed_job(dbsession, model_id=model_b.id, status=DownloadJobStatus.RUNNING)

    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get(
        "/api/llm/jobs", params={"status": "running", "model_id": str(model_a.id)}
    )
    assert resp.status_code == status.HTTP_200_OK
    assert [j["id"] for j in resp.json()] == [str(job_a.id)]


async def test_get_job_ok(client, fastapi_app, dbsession, superuser) -> None:
    model = await _seed_model(dbsession)
    job = await _seed_job(
        dbsession, model_id=model.id, status=DownloadJobStatus.RUNNING, progress=42
    )
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get(f"/api/llm/jobs/{job.id}")
    assert resp.status_code == status.HTTP_200_OK
    body = resp.json()
    assert body["id"] == str(job.id)
    assert body["model_id"] == str(model.id)
    assert body["status"] == DownloadJobStatus.RUNNING.value
    assert body["progress"] == 42


async def test_get_job_missing(client, fastapi_app, superuser) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.get("/api/llm/jobs/00000000-0000-0000-0000-000000000000")
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.json()["detail"] == "Job not found"


async def test_cancel_queued_job(client, fastapi_app, dbsession, superuser) -> None:
    model = await _seed_model(dbsession)
    job = await _seed_job(dbsession, model_id=model.id, status=DownloadJobStatus.QUEUED)
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.post(f"/api/llm/jobs/{job.id}/cancel")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["status"] == DownloadJobStatus.CANCELED.value


async def test_cancel_running_job(client, fastapi_app, dbsession, superuser) -> None:
    model = await _seed_model(dbsession)
    job = await _seed_job(dbsession, model_id=model.id, status=DownloadJobStatus.RUNNING)
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.post(f"/api/llm/jobs/{job.id}/cancel")
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["status"] == DownloadJobStatus.CANCELED.value


async def test_cancel_terminal_job_conflict(client, fastapi_app, dbsession, superuser) -> None:
    model = await _seed_model(dbsession)
    job = await _seed_job(dbsession, model_id=model.id, status=DownloadJobStatus.SUCCESS)
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.post(f"/api/llm/jobs/{job.id}/cancel")
    assert resp.status_code == status.HTTP_409_CONFLICT
    assert "Cannot cancel job in status 'success'" in resp.json()["detail"]


async def test_cancel_missing_job(client, fastapi_app, superuser) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser
    resp = await client.post("/api/llm/jobs/00000000-0000-0000-0000-000000000000/cancel")
    assert resp.status_code == status.HTTP_404_NOT_FOUND
    assert resp.json()["detail"] == "Job not found"


# ---------------------------------------------------------------------------
# RBAC — require_permission("llm.providers" | "llm.models" | "llm.jobs", …)
# ---------------------------------------------------------------------------


async def test_unauthenticated_llm_endpoints(client, fastapi_app) -> None:
    """No override -> current_active_user falls through to JWT auth -> 401."""
    fastapi_app.dependency_overrides.pop(current_active_user, None)
    for path in ("/api/llm/providers", "/api/llm/models", "/api/llm/jobs"):
        resp = await client.get(path)
        assert resp.status_code == status.HTTP_401_UNAUTHORIZED


async def test_regular_user_denied_providers(
    client, fastapi_app, dbsession, regular_user
) -> None:
    await _seed_provider(dbsession)
    fastapi_app.dependency_overrides[current_active_user] = lambda: regular_user
    resp = await client.get("/api/llm/providers")
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["detail"] == "Permission denied: llm.providers:read"


async def test_regular_user_denied_models(client, fastapi_app, regular_user) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: regular_user
    resp = await client.get("/api/llm/models")
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["detail"] == "Permission denied: llm.models:read"


async def test_regular_user_denied_jobs(client, fastapi_app, regular_user) -> None:
    fastapi_app.dependency_overrides[current_active_user] = lambda: regular_user
    resp = await client.get("/api/llm/jobs")
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["detail"] == "Permission denied: llm.jobs:read"


async def test_regular_user_denied_patch_provider(
    client, fastapi_app, dbsession, regular_user
) -> None:
    p = await _seed_provider(dbsession)
    fastapi_app.dependency_overrides[current_active_user] = lambda: regular_user
    resp = await client.patch(f"/api/llm/providers/{p.id}", json={"name": "nope"})
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["detail"] == "Permission denied: llm.providers:update"


async def test_regular_user_denied_cancel_job(
    client, fastapi_app, dbsession, regular_user
) -> None:
    model = await _seed_model(dbsession)
    job = await _seed_job(dbsession, model_id=model.id, status=DownloadJobStatus.QUEUED)
    fastapi_app.dependency_overrides[current_active_user] = lambda: regular_user
    resp = await client.post(f"/api/llm/jobs/{job.id}/cancel")
    assert resp.status_code == status.HTTP_403_FORBIDDEN
    assert resp.json()["detail"] == "Permission denied: llm.jobs:cancel"


async def test_a_remote_model_can_be_left_without_the_gateways_tools(
    client, fastapi_app, dbsession, superuser, llm_service, monkeypatch,
) -> None:
    """OpenRouter's free router picks only among tool models when a request carries tools (2026-09-27)."""
    published: list[dict] = []

    async def publish(**kwargs):
        published.append(kwargs)

    monkeypatch.setattr(llm_service.gateway_sync, "publish_runtime", publish)
    provider, _runtime = await _litellm_remote(dbsession, litellm_model="openrouter/free", remote_model="openrouter/free")
    fastapi_app.dependency_overrides[current_active_user] = lambda: superuser

    resp = await client.patch(f"/api/llm/providers/{provider.id}", json={"calls_tools": False})
    assert resp.status_code == status.HTTP_200_OK
    assert resp.json()["calls_tools"] is False
    assert resp.json()["remote_model"] == "openrouter/free", "the rest of its capabilities kept"
    assert [p["tools"] for p in published] == [False], "its route says so, and the gateway offers none"

    # Back to the default: the route no longer says anything.
    resp = await client.patch(f"/api/llm/providers/{provider.id}", json={"calls_tools": None})
    assert resp.json()["calls_tools"] is None
    assert published[-1]["tools"] is None
