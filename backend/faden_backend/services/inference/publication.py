"""Generic inference publication layer (Phase 5).

Translates normalized :class:`~faden_backend.db.models.inference.InferenceDeployment`
and :class:`~faden_backend.db.models.inference.InferenceEndpoint` states into
generic gateway provider availability, completely separated from driver-specific
runtimes (Ray, Dynamo, vLLM).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import select

from faden_backend.db.dao.inference_dao import EndpointDAO
from faden_backend.db.models.inference import (
    DeploymentDesiredState,
    DeploymentPhase,
    EndpointStatus,
    InferenceDeployment,
    InferenceEndpoint,
)
from faden_backend.db.models.llm import LLMProvider, ProviderTarget, ProviderType
from faden_backend.services.llm.kinds import IMAGES, deployment_calls_tools, deployment_kind

#: Marks a provider row as owned by a deployment.  The same string the
#: gateway stamps on ``llm_provider_instance``, so ownership reads the same
#: at both layers.
DERIVED_SOURCE_KIND = "inference_deployment"

#: vLLM's own ``max_num_seqs`` for its OpenAI server on most GPUs, for a
#: deployment that does not set one (GPUs of 70 GB and more default higher).
DEFAULT_MAX_NUM_SEQS = 256
#: Requests a picture model's copy takes at once: it makes one request's
#: pictures at a time (seconds each) and queues the rest.
IMAGE_REQUESTS_PER_COPY = 4

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession
    from faden_backend.services.llm.gateway_sync import GatewaySyncService

log = logging.getLogger(__name__)


def gateway_concurrency(deployment: InferenceDeployment) -> int:
    """How many requests the gateway lets through to *deployment* at once.

    Each ready copy batches up to its ``max_num_seqs`` and queues the rest
    itself, and Ray Serve LLM sets no limit of its own in front of a copy.
    The gateway's fixed 16 refused the 17th chat of a three-copy deployment
    whose copies take 128 each (.61, 2026-10-01).

    A picture model's copy makes one request's pictures at a time: a few wait
    in its queue (``IMAGE_REQUESTS_PER_COPY``), the gateway holds the rest.
    """
    if deployment_kind(deployment.spec_json) == IMAGES:
        return max(deployment.ready_replicas or 0, 1) * IMAGE_REQUESTS_PER_COPY
    config = ((deployment.spec_json or {}).get("engine") or {}).get("config") or {}
    try:
        per_copy = int(config.get("max_num_seqs") or DEFAULT_MAX_NUM_SEQS)
    except (TypeError, ValueError):
        per_copy = DEFAULT_MAX_NUM_SEQS
    if per_copy < 1:
        per_copy = DEFAULT_MAX_NUM_SEQS
    return max(deployment.ready_replicas or 0, 1) * per_copy


def _capacity_hints(service: dict) -> dict | None:
    """What the gateway reads per request from the routing row, or None for its defaults.

    ``first_token_timeout_sec``: how long a request may wait for its reply to
    start (``service.first_token_timeout_sec``). None clears a value set
    before, so removing the setting brings the default back.
    """
    wait = service.get("first_token_timeout_sec")
    if isinstance(wait, bool) or not isinstance(wait, (int, float)) or wait <= 0:
        return None
    return {"first_token_timeout_sec": float(wait)}


def _watermark(published: object) -> dict | None:
    """The endpoint's watermark as the driver published it: algorithm, key version, context width."""
    if not isinstance(published, dict) or not isinstance(published.get("algorithm"), str):
        return None
    fields = ("algorithm", "key_version", "context_width")
    return {key: published[key] for key in fields if published.get(key) is not None}


class InferencePublicationCoordinator:
    """Synchronizes normalized inference endpoint status to the Gateway."""

    def __init__(
        self,
        session: AsyncSession,
        gateway_sync: GatewaySyncService | None = None,
    ) -> None:
        self.session = session
        self.gateway_sync = gateway_sync
        self.endpoint_dao = EndpointDAO(session)

    async def reconcile_deployment_publication(
        self,
        deployment: InferenceDeployment,
    ) -> None:
        """Reconcile the gateway provider representation of this deployment.

        Adheres strictly to the Phase 5 publication invariants:
        - 1 Deployment = 1 logical LLMProviderInstance
        - Publication is driven by normalized InferenceEndpoint state, not raw engine state
        - Degraded deployment with healthy endpoint and ready_replicas > 0 remains routable
        - Stopping a deployment deactivates the source without deleting alias or membership
        - Deleting a deployment retires the source

        The provider row is reconciled first and unconditionally. It is the
        backend's own record -- what an operator manages this deployment from
        on the providers screen -- and it has to exist whether or not a
        gateway is configured. Inside the guard below, a stack without one
        would list no provider for a cluster that was happily serving.
        """
        await self.reconcile_derived_provider(deployment)

        if self.gateway_sync is None or not self.gateway_sync.enabled:
            return

        source_id = deployment.id
        source_kind = "inference_deployment"

        # 1. Handle explicit desired or observed stopped state
        if (
            deployment.desired_state == DeploymentDesiredState.STOPPED
            or deployment.phase == DeploymentPhase.STOPPED
        ):
            await self.gateway_sync.deactivate_source(
                source_kind=source_kind,
                source_id=source_id,
            )
            return

        # 2. Handle explicit desired or observed deleted state
        if (
            deployment.desired_state == DeploymentDesiredState.DELETED
            or deployment.phase == DeploymentPhase.DELETED
        ):
            await self.gateway_sync.retire_source(
                source_kind=source_kind,
                source_id=source_id,
            )
            return

        # 3. Retrieve normalized primary inference endpoint
        endpoints = await self.endpoint_dao.list_for_deployment(deployment.id)
        primary_endpoint: InferenceEndpoint | None = next(
            (e for e in endpoints if e.name == "openai"),
            endpoints[0] if endpoints else None,
        )

        if primary_endpoint is None:
            # No endpoint yet (pending deployment)
            return

        # 4. Evaluate endpoint readiness and routability
        endpoint_published = primary_endpoint.status == EndpointStatus.PUBLISHED
        ready_replicas = deployment.ready_replicas

        # Base URL from endpoint address + path, or published_json
        address = primary_endpoint.address
        path = primary_endpoint.path or "/v1"
        base_url = (primary_endpoint.published_json or {}).get("base_url") or f"{address}{path}"
        served_model_name = (primary_endpoint.published_json or {}).get("model")

        # Determine alias: operator-specified in spec_json.service.alias, or None.
        # Do not invent alias = deployment.name when none was specified (P5-01).
        spec_service = (deployment.spec_json or {}).get("service") or {}
        alias = spec_service.get("alias")

        # A deployment is routable if its endpoint is PUBLISHED and has at least 1 ready replica
        # Even if deployment phase is DEGRADED, as long as ready_replicas > 0, it stays routable
        if endpoint_published and ready_replicas > 0:
            is_routable = True
            health_status = "healthy"

            await self.gateway_sync.publish_inference_endpoint(
                deployment_id=deployment.id,
                endpoint_id=primary_endpoint.id,
                base_url=base_url,
                alias=alias,
                served_model_name=served_model_name,
                backend_provider_type="vllm",
                health_status=health_status,
                is_routable=is_routable,
                max_concurrency=gateway_concurrency(deployment),
                capacity_hints=_capacity_hints(spec_service),
                # What it serves: pictures for an image engine, embeddings or
                # scores for a pooling model, chat otherwise.
                task=deployment_kind(deployment.spec_json),
                tools=deployment_calls_tools(deployment.spec_json),
                # Whether the model sees pictures: said on the deployment (the marketplace
                # fills it in from the model's facts), unknown otherwise.
                vision=spec_service.get("vision") if isinstance(spec_service.get("vision"), bool) else None,
                # Behind the machine's model-TLS proxy: reached with the
                # gateway's machine certificate (services/machine_tls).
                machine_tls=bool((primary_endpoint.published_json or {}).get("machine_tls")),
                # How the runtime watermarks the answers (services/provenance/watermark.py):
                # the gateway's receipt for an answer names it.
                watermark=_watermark((primary_endpoint.published_json or {}).get("watermark")),
            )
        elif primary_endpoint.status in (EndpointStatus.FAILED, EndpointStatus.RETIRED) or ready_replicas == 0:
            # Endpoint is unhealthy or has 0 ready replicas
            await self.gateway_sync.set_source_health(
                source_kind=source_kind,
                source_id=source_id,
                health_status="unhealthy",
                enabled=False,
            )


    async def reconcile_derived_provider(
        self, deployment: InferenceDeployment
    ) -> None:
        """Bring the provider row this deployment owns into line with it.

        Three outcomes, and the distinction between the last two is the point:

        * **Deleted** -- the row goes. A provider for a deployment that no
          longer exists is exactly the stale entry the providers screen was
          full of.
        * **Stopped, failed, or with no ready replicas** -- the row stays. It
          is still a real provider that is not serving, and the moment it
          stops serving is the moment an operator goes looking for it. A
          stopped local runtime keeps its row for the same reason.
        * **Serving** -- the row is refreshed with the address requests should
          go to.

        Never raises: a deployment reconcile must not fail because a display
        record could not be written.
        """
        try:
            if (
                deployment.desired_state == DeploymentDesiredState.DELETED
                or deployment.phase == DeploymentPhase.DELETED
            ):
                await self.remove_derived_provider(deployment.id)
                return

            base_url: str | None = None
            serving = False
            if not (
                deployment.desired_state == DeploymentDesiredState.STOPPED
                or deployment.phase == DeploymentPhase.STOPPED
            ):
                endpoints = await self.endpoint_dao.list_for_deployment(deployment.id)
                primary = next(
                    (e for e in endpoints if e.name == "openai"),
                    endpoints[0] if endpoints else None,
                )
                if primary is not None:
                    published = primary.published_json or {}
                    base_url = published.get("base_url") or (
                        f"{primary.address}{primary.path or '/v1'}"
                    )
                    serving = (
                        primary.status == EndpointStatus.PUBLISHED
                        and deployment.ready_replicas > 0
                    )
                elif deployment.phase != DeploymentPhase.RUNNING:
                    # Nothing published yet on a deployment still coming up.
                    # Creating a provider now would put a row on the screen
                    # for something that has never served.
                    return

            await self.sync_derived_provider(
                deployment, serving=serving, base_url=base_url
            )
        except Exception:  # noqa: BLE001 - a display record never fails a reconcile
            log.exception(
                "Could not reconcile the provider row for deployment %s",
                deployment.id,
            )

    # ------------------------------------------------------------------
    # The provider row an operator manages this deployment from
    # ------------------------------------------------------------------

    async def sync_derived_provider(
        self,
        deployment: InferenceDeployment,
        *,
        serving: bool,
        base_url: str | None = None,
    ) -> LLMProvider | None:
        """Create or refresh the provider row this deployment owns.

        Keyed on ``(source_kind, source_id)`` rather than on name, so renaming
        a deployment moves its provider instead of leaving one behind and
        making another.

        Only the fields the deployment is authoritative for are written. There
        is nothing else on the row to preserve today, but the moment there is
        -- an operator's note, a rate limit -- a blind overwrite would erase it
        on every reconcile.
        """
        existing = await self._derived_provider(deployment.id)
        name = deployment.name or f"deployment-{deployment.id}"

        if existing is None:
            existing = LLMProvider(
                name=name,
                type=ProviderType.VLLM,
                target=ProviderTarget.INFERENCE_CLUSTER,
                source_kind=DERIVED_SOURCE_KIND,
                source_id=str(deployment.id),
            )
            self.session.add(existing)

        existing.name = name
        existing.target = ProviderTarget.INFERENCE_CLUSTER
        if base_url:
            existing.endpoint_url = base_url
        # Everything else about this provider's state lives on the deployment,
        # and is read from there rather than copied here: a copy is a second
        # answer that can disagree with the first.
        await self.session.flush()
        log.debug(
            "Provider row for deployment %s is %s",
            deployment.id,
            "serving" if serving else "not serving",
        )
        return existing

    async def remove_derived_provider(self, deployment_id) -> bool:
        """Delete the provider row a deleted deployment owned.

        Returns whether one was there. Idempotent: a deployment can converge
        to deleted more than once, and reconcile passes repeat.
        """
        existing = await self._derived_provider(deployment_id)
        if existing is None:
            return False
        await self.session.delete(existing)
        await self.session.flush()
        log.info("Removed the provider row owned by deployment %s", deployment_id)
        return True

    async def _derived_provider(self, deployment_id) -> LLMProvider | None:
        rows = await self.session.execute(
            select(LLMProvider).where(
                LLMProvider.source_kind == DERIVED_SOURCE_KIND,
                LLMProvider.source_id == str(deployment_id),
            )
        )
        return rows.scalars().first()
