from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.services.extensions import UsageRecord
from faden_gateway.services.extensions import registry as extension_registry
from faden_gateway.services.gateway import usage_hooks
from faden_gateway.services.gateway.pricing import PricingService

logger = logging.getLogger(__name__)


class AuditService:
    """Writes gateway request logs."""

    def __init__(
        self,
        dao: GatewayDAO,
        pricing_service: PricingService | None = None,
    ) -> None:
        self.dao = dao
        self.pricing_service = pricing_service

    async def log(
        self,
        *,
        request_id: str,
        trace_id: str | None,
        tenant_id: str,
        user_id: str,
        model_alias: str | None,
        provider_instance_id: str | None,
        endpoint: str,
        status_code: int,
        latency_ms: int,
        ttft_ms: int | None,
        prompt_tokens: int | None,
        completion_tokens: int | None,
        total_tokens: int | None,
        error_code: str | None,
        stream: bool | None = None,
        cached_tokens: int | None = None,
        cache_write_tokens: int | None = None,
        reasoning_tokens: int | None = None,
        usage_source: str | None = None,
        estimated_tokens: int = 0,
        provider_name: str | None = None,
        session_id: str | None = None,
        finish_reason: str | None = None,
        retry_count: int | None = None,
        skills_used: list[dict] | None = None,
        rag_context: dict | None = None,
        mcp_tool_call_count: int | None = None,
        mcp_tool_loop_iterations: int | None = None,
        group_id: str | None = None,
        group_path: tuple[str, ...] = (),
        project_id: str | None = None,
        tool_calls: list[dict] | None = None,
        agent_id: str | None = None,
        device_id: str | None = None,
        conversation_id: str | None = None,
        turn_id: str | None = None,
    ) -> None:
        """Persist one request log row with optional cost estimation.

        Then, in the background, the extensions' usage listeners hear of it (``UsageRecord``, with
        *estimated_tokens*: what admission reserved for the request).
        """
        parsed_provider_id: uuid.UUID | None = None
        if provider_instance_id:
            parsed_provider_id = uuid.UUID(provider_instance_id)

        # Compute cost estimate if pricing service is available
        estimated_input_cost = None
        estimated_output_cost = None
        estimated_total_cost = None
        currency: str | None = None
        price_catalog_id: uuid.UUID | None = None
        cost_estimate_status: str | None = None

        if self.pricing_service is not None:
            estimate = self.pricing_service.compute_cost(
                provider_name=provider_name,
                model_alias=model_alias,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                cached_tokens=cached_tokens,
            )
            estimated_input_cost = estimate.estimated_input_cost
            estimated_output_cost = estimate.estimated_output_cost
            estimated_total_cost = estimate.estimated_total_cost
            currency = estimate.currency
            price_catalog_id = estimate.price_catalog_id
            cost_estimate_status = estimate.status

        log_row = await self.dao.insert_request_log(
            request_id=request_id,
            trace_id=trace_id,
            tenant_id=tenant_id,
            user_id=user_id,
            model_alias=model_alias,
            provider_instance_id=parsed_provider_id,
            endpoint=endpoint,
            status_code=status_code,
            latency_ms=latency_ms,
            ttft_ms=ttft_ms,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            error_code=error_code,
            stream=stream,
            cached_tokens=cached_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
            usage_source=usage_source,
            estimated_input_cost=estimated_input_cost,
            estimated_output_cost=estimated_output_cost,
            estimated_total_cost=estimated_total_cost,
            currency=currency,
            price_catalog_id=price_catalog_id,
            cost_estimate_status=cost_estimate_status,
            session_id=session_id,
            finish_reason=finish_reason,
            retry_count=retry_count,
            skills_used=skills_used,
            rag_context=rag_context,
            mcp_tool_call_count=mcp_tool_call_count,
            mcp_tool_loop_iterations=mcp_tool_loop_iterations,
            group_id=group_id,
            # The team's path at the time of use; none without a team.
            group_path=list(group_path) if group_id and group_path else None,
            project_id=project_id,
            agent_id=agent_id,
            device_id=device_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
        )

        # Persist per-tool-call telemetry
        if tool_calls:
            try:
                await self.dao.insert_tool_call_logs(
                    request_log_id=log_row.id,
                    request_id=request_id,
                    tool_calls=tool_calls,
                )
            except Exception:
                logger.warning(
                    "Failed to persist tool call logs for %s", request_id,
                    exc_info=True,
                )

        listeners = extension_registry.usage_listeners
        if listeners:
            priced = estimated_total_cost is not None or price_catalog_id is not None
            usage_hooks.notify(listeners, UsageRecord(
                tenant_id=tenant_id,
                user_id=user_id,
                group_id=group_id,
                project_id=project_id,
                agent_id=agent_id,
                model_alias=model_alias,
                provider_instance_id=provider_instance_id,
                request_id=request_id,
                status_code=status_code,
                input_tokens=prompt_tokens or 0,
                cached_input_tokens=cached_tokens or 0,
                output_tokens=completion_tokens or 0,
                reasoning_tokens=reasoning_tokens or 0,
                cost=Decimal(str(estimated_total_cost)) if estimated_total_cost is not None else None,
                currency=currency if priced else None,
                estimated_tokens=estimated_tokens,
                created_at=datetime.now(timezone.utc),
                group_path=tuple(group_path) if group_id else (),
            ))
