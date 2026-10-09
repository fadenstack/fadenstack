"""A runtime's image is an image reference, never a ``docker run`` option.

``provider_config.image`` went unchanged to the machine's agent, which put it
right before the command: ``--privileged`` was read as an option there and the
command's first word as the image. And ``POST /system/nodes/{id}/commands``
sent any command, a deploy with any image included.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from pydantic import ValidationError
from starlette import status

from faden_backend.db.models.users import User, current_active_user
from faden_backend.services.docker.image_ref import check_image_ref, is_image_ref
from faden_backend.services.inference.bundles import default_bundle_registry
from faden_backend.services.nodes.service import NodeControlService
from faden_backend.settings import settings
from faden_backend.web.api.admin.system.views import get_node_control_service
from faden_backend.web.api.llm.schema import RuntimeCreateRequest, RuntimeUpdateRequest

DIGEST = "sha256:" + "a" * 64


def test_every_image_the_product_runs_is_a_reference() -> None:
    images = [
        settings.default_vllm_image, settings.default_vllm_rocm_image,
        settings.default_vllm_nvidia_arm_image, settings.default_vllm_legacy_image,
        *(bundle.container.image for bundle in default_bundle_registry.list_bundles()),
        "localhost:5000/faden/ray-vllm-gb10:ray2.58", f"ghcr.io/fadenstack/runtime@{DIGEST}",
        f"vllm/vllm-openai:v0.6.6@{DIGEST}",
    ]
    assert [image for image in images if not is_image_ref(image)] == []


@pytest.mark.parametrize("image", ["--privileged", "-v", "alpine --privileged", " alpine", "a\tb", "x" * 513, 7])
def test_an_option_or_a_space_is_not(image: Any) -> None:
    with pytest.raises(ValueError, match="not an image reference"):
        check_image_ref(image)


def _create(provider_config: dict[str, Any] | None) -> RuntimeCreateRequest:
    return RuntimeCreateRequest(
        name="m", provider_id=uuid.uuid4(), model_id=uuid.uuid4(), provider_config=provider_config,
    )


def test_a_runtime_takes_only_an_image_reference() -> None:
    with pytest.raises(ValidationError, match=r"provider_config\.image"):
        _create({"image": "--privileged", "command": ["alpine", "sh"]})
    with pytest.raises(ValidationError, match=r"provider_config\.image"):
        RuntimeUpdateRequest(provider_config={"image": "-v"})
    # What the console sends: an image, or none (the default one).
    assert _create({"image": "nvcr.io/nvidia/vllm:26.03-py3", "shm_size": "16g"}).provider_config
    assert _create({"image": ""}).provider_config == {"image": ""}
    assert _create({"engine_args": {"max-model-len": 8192}}).provider_config
    assert _create(None).provider_config is None
    assert RuntimeUpdateRequest(provider_config=None).provider_config is None


@pytest.mark.anyio
@pytest.mark.parametrize("command_type", ["deploy_workload", "start_workload", "restart_workload", "update_workload"])
async def test_no_workload_command_carries_such_an_image(command_type: str) -> None:
    """Also one built from a runtime saved before the API checked it."""
    dao = MagicMock()
    service = NodeControlService(dao, pepper="p", enrollment_ttl_minutes=5, default_command_timeout_sec=30)
    for payload in ({"image": "--privileged"}, {"provider_config": {"image": "--privileged"}}):
        with pytest.raises(ValueError, match="not an image reference"):
            await service.issue_command(
                node_id=uuid.uuid4(), command_type=command_type, payload=payload,
                issued_by=None, correlation_id=None, timeout_sec=None, idempotency_key=None,
            )
    dao.get_node_by_id.assert_not_called()


# ── Commands sent to a machine by hand ────────────────────────────


class _Service:
    def __init__(self) -> None:
        self.issued: list[tuple[str, dict[str, Any]]] = []

    async def issue_command(self, *, node_id: uuid.UUID, command_type: str, payload: dict[str, Any], **_: Any) -> Any:
        self.issued.append((command_type, payload))
        return SimpleNamespace(node_id=node_id, command_type=command_type, payload=payload)

    @staticmethod
    def serialize_command(command: Any) -> dict[str, Any]:
        return {
            "id": str(uuid.uuid4()), "node_id": str(command.node_id), "command_type": command.command_type,
            "status": "queued", "idempotency_key": "k", "payload": command.payload,
            "issued_at": datetime.now(UTC).isoformat(),
        }


@pytest.fixture()
def hand(fastapi_app: FastAPI) -> tuple[FastAPI, _Service]:
    user = MagicMock(spec=User)
    user.id, user.is_active, user.is_superuser, user.is_verified = uuid.uuid4(), True, True, True
    service = _Service()
    fastapi_app.dependency_overrides[current_active_user] = lambda: user
    fastapi_app.dependency_overrides[get_node_control_service] = lambda: service
    return fastapi_app, service


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"command_type": "refresh_inventory"},
        {"command_type": "collect_diagnostics"},
        {"command_type": "check_system_updates"},
        {"command_type": "apply_system_updates", "payload": {"scope": "all"}},
        {"command_type": "apply_system_updates", "payload": {"scope": "system", "reboot_policy": "if_required"}},
    ],
)
async def test_the_consoles_hand_commands_are_sent(
    hand: tuple[FastAPI, _Service], client: AsyncClient, body: dict[str, Any],
) -> None:
    app, service = hand
    r = await client.post(app.url_path_for("system_node_command_issue", node_id=str(uuid.uuid4())), json=body)
    assert r.status_code == status.HTTP_200_OK, r.text
    assert service.issued == [(body["command_type"], body.get("payload", {}))]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "body",
    [
        {"command_type": "deploy_workload", "payload": {"runtime_id": "x", "image": "alpine"}},
        {"command_type": "sync_node_profile", "payload": {"profile": {}}},
        {"command_type": "start_ray_head", "payload": {}},
        {"command_type": "refresh_inventory", "payload": {"image": "alpine"}},
        {"command_type": "apply_system_updates", "payload": {"scope": "everything"}},
        {"command_type": "apply_system_updates", "payload": {"scope": ["all"]}},
    ],
)
async def test_anything_else_is_refused(
    hand: tuple[FastAPI, _Service], client: AsyncClient, body: dict[str, Any],
) -> None:
    app, service = hand
    r = await client.post(app.url_path_for("system_node_command_issue", node_id=str(uuid.uuid4())), json=body)
    assert r.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, r.text
    assert service.issued == []
