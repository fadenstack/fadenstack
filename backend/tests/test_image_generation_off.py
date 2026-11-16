"""Image generation is off in 0.7.1 (EU AI Act Art. 5 from 2 December 2026): the backend's and console's side.

The gateway decides; the backend mirrors its switch: no new image deployment (from the marketplace, before
anything is downloaded, or from the deployments API), existing ones left alone, and the console told.
"""

from __future__ import annotations

import ast
import pathlib
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from faden_backend.services.inference.service import DeploymentService, ImagesDisabledError
from faden_backend.services.llm import image_generation
from tests.test_marketplace import _dgx_cluster, authed, fake_hub  # noqa: F401 - fixtures

GATEWAY_SWITCH = (
    pathlib.Path(__file__).resolve().parents[2] / "gateway" / "faden_gateway" / "services" / "gateway"
    / "image_generation.py"
)

PICTURE_SPEC: dict[str, Any] = {
    "api_version": "inference.fadenstack.com/v1alpha1",
    "engine": {"name": "diffusers", "config": {"num_inference_steps": 8}},
    "scale": {"replicas": 1},
    "resources": {"replica": {"gpus": 1}},
    "service": {"path": "/v1", "openai": True, "alias": "z-image-turbo"},
}
CHAT_SPEC: dict[str, Any] = {
    "api_version": "inference.fadenstack.com/v1alpha1",
    "engine": {"name": "vllm", "config": {}},
    "scale": {"replicas": 1},
    "resources": {"replica": {"gpus": 1}},
    "service": {"path": "/v1", "openai": True, "alias": "chat"},
}


def _gateway_constants() -> dict[str, Any]:
    """ENABLED, CODE and NOTE as the gateway's module sets them, read from its source (no import)."""
    tree = ast.parse(GATEWAY_SWITCH.read_text(encoding="utf-8"))
    found: dict[str, Any] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.value is not None:
            found[node.target.id] = ast.literal_eval(node.value)
    return found


def test_it_is_off_in_this_version() -> None:
    assert image_generation.ENABLED is False


def test_the_console_switch_is_the_gateways() -> None:
    """One switch: a later version flips the gateway's and this one together, or this test fails."""
    gateway = _gateway_constants()
    assert gateway["ENABLED"] is image_generation.ENABLED
    assert gateway["CODE"] == image_generation.CODE == "images_disabled"
    assert gateway["NOTE"] == image_generation.NOTE
    assert "EU AI Act (Article 5)" in image_generation.NOTE and "2 December 2026" in image_generation.NOTE


def test_no_setting_turns_it_on() -> None:
    """Neither the settings nor the environment know it: only the code does."""
    from faden_backend import settings as settings_module

    source = pathlib.Path(settings_module.__file__).read_text(encoding="utf-8").lower()
    assert "image_generation" not in source and "images_enabled" not in source


@pytest.mark.anyio
async def test_the_console_hears_that_image_generation_is_off(
    authed: FastAPI, client: AsyncClient,  # noqa: F811 - the fixture
) -> None:
    r = await client.get("/api/llm/marketplace/image-generation")
    assert r.status_code == 200, r.text
    assert r.json() == {"enabled": False, "code": "images_disabled"}


class _Llm:
    """The download service, recording what it was asked to fetch."""

    def __init__(self) -> None:
        self.started: list[str] = []

    async def start_download(self, model_dao: Any, job_dao: Any, *, hf_repo_id: str, **kw: Any) -> Any:
        from faden_backend.db.models.llm import ModelSource, ModelStatus

        self.started.append(hf_repo_id)
        model = await model_dao.create(display_name=hf_repo_id, source=ModelSource.HUGGINGFACE, hf_repo_id=hf_repo_id,
                                       hf_revision=None, tags=kw.get("tags"), status=ModelStatus.DOWNLOADING)
        return model, await job_dao.create(model.id)


async def _host_z_image(client: AsyncClient, env_id: str) -> Any:
    return await client.post("/api/llm/marketplace/host", json={
        "repo_id": "Tongyi-MAI/Z-Image-Turbo", "environment_id": env_id, "name": "z-image-turbo",
        "alias": "z-image-turbo", "copies": 1, "gpus_per_copy": 1,
    })


@pytest.mark.anyio
async def test_hosting_a_picture_model_is_refused_before_anything_is_downloaded(
    authed: FastAPI, client: AsyncClient, dbsession: AsyncSession, fake_hub: dict[str, Any],  # noqa: F811
) -> None:
    from faden_backend.db.models.inference import InferenceDeployment
    from faden_backend.web.api.llm.dependencies import get_llm_service

    env_id = await _dgx_cluster(dbsession)
    fake_hub["online"] = False  # the curated list says what Z-Image-Turbo is
    llm = _Llm()
    authed.dependency_overrides[get_llm_service] = lambda: llm

    r = await _host_z_image(client, env_id)

    assert r.status_code == 403, r.text
    assert r.json()["detail"] == {"code": "images_disabled", "message": image_generation.NOTE}
    assert llm.started == [], "nothing is downloaded"
    assert (await dbsession.execute(select(InferenceDeployment))).scalars().all() == []


@pytest.mark.anyio
async def test_with_the_switch_on_a_picture_model_is_hosted_as_before(
    authed: FastAPI, client: AsyncClient, dbsession: AsyncSession, fake_hub: dict[str, Any],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """What a later version gets back by flipping the switch: the hosting code is kept, and tested."""
    from faden_backend.web.api.llm.dependencies import get_llm_service

    monkeypatch.setattr(image_generation, "ENABLED", True)
    env_id = await _dgx_cluster(dbsession)
    fake_hub["online"] = False
    llm = _Llm()
    authed.dependency_overrides[get_llm_service] = lambda: llm

    r = await _host_z_image(client, env_id)

    assert r.status_code == 201, r.text
    assert r.json()["spec"]["engine"]["name"] == "diffusers"
    assert llm.started == ["Tongyi-MAI/Z-Image-Turbo"]


async def _environment_and_model(dbsession: AsyncSession) -> tuple[uuid.UUID, uuid.UUID]:
    from faden_backend.db.models.llm import LLMModel, ModelSource, ModelStatus

    env_id = uuid.UUID(await _dgx_cluster(dbsession))
    model = LLMModel(display_name="Z-Image-Turbo", source=ModelSource.HUGGINGFACE, status=ModelStatus.AVAILABLE,
                     hf_repo_id="Tongyi-MAI/Z-Image-Turbo")
    dbsession.add(model)
    await dbsession.flush()
    return env_id, model.id


@pytest.mark.anyio
async def test_the_deployments_api_refuses_a_new_picture_model(
    authed: FastAPI, client: AsyncClient, dbsession: AsyncSession,  # noqa: F811 - the fixture
) -> None:
    env_id, model_id = await _environment_and_model(dbsession)
    await dbsession.commit()

    r = await client.post("/api/inference/deployments", json={
        "environment_id": str(env_id), "model_id": str(model_id), "name": "z-image", "spec": PICTURE_SPEC,
    })

    assert r.status_code == 403, r.text
    assert r.json()["detail"]["code"] == "images_disabled"


@pytest.mark.anyio
async def test_an_existing_picture_deployment_is_left_alone(dbsession: AsyncSession) -> None:
    """An upgraded install keeps its image deployment: it may change, stop and go, as before (V16)."""
    from faden_backend.db.dao.inference_dao import DeploymentDAO

    env_id, model_id = await _environment_and_model(dbsession)
    # As 0.7.0 left it: created before image generation was switched off.
    existing = await DeploymentDAO(dbsession).create(
        environment_id=env_id, model_id=model_id, name="z-image-turbo", spec=PICTURE_SPEC,
    )
    service = DeploymentService(dbsession)

    scaled = await service.update(existing.id, spec={**PICTURE_SPEC, "scale": {"replicas": 2}})
    assert scaled.spec_json["scale"]["replicas"] == 2 and scaled.desired_state == "active"
    stopped = await service.update(existing.id, desired_state="stopped")
    assert stopped.desired_state == "stopped"
    await service.delete(existing.id)


@pytest.mark.anyio
async def test_no_deployment_becomes_a_picture_model(dbsession: AsyncSession) -> None:
    env_id, model_id = await _environment_and_model(dbsession)
    service = DeploymentService(dbsession)

    with pytest.raises(ImagesDisabledError) as refused:
        await service.create(environment_id=env_id, model_id=model_id, name="z-image", spec=PICTURE_SPEC)
    assert refused.value.detail == {"code": "images_disabled", "message": image_generation.NOTE}

    chat = await service.create(environment_id=env_id, model_id=model_id, name="chat", spec=CHAT_SPEC)
    with pytest.raises(ImagesDisabledError):
        await service.update(chat.id, spec=PICTURE_SPEC)
    assert (await service.get(chat.id)).spec_json["engine"]["name"] == "vllm"
