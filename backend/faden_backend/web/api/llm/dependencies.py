"""Shared FastAPI dependencies for the /llm namespace."""

from __future__ import annotations

from fastapi import Depends, Request

from faden_backend.db.dao.llm_dao import ModelDAO, ProviderDAO, RuntimeDAO
from faden_backend.services.llm.calls_service import LLMCallsService
from faden_backend.services.llm.graph_service import LLMGraphService, ProviderOwners
from faden_backend.services.llm.service import LLMService


def get_llm_service(request: Request) -> LLMService:
    """Retrieve the shared LLMService from app state."""
    return request.app.state.llm_service  # type: ignore[no-any-return]


def get_llm_graph_service(
    request: Request,
    provider_dao: ProviderDAO = Depends(),
    runtime_dao: RuntimeDAO = Depends(),
    model_dao: ModelDAO = Depends(),
) -> LLMGraphService:
    """Build a graph service with DAO dependencies and optional trace DB access."""
    return LLMGraphService(
        provider_dao=provider_dao,
        runtime_dao=runtime_dao,
        model_dao=model_dao,
        trace_session_factory=getattr(request.app.state, "llm_graph_trace_session_factory", None),
    )


async def get_llm_calls_service(
    request: Request,
    provider_dao: ProviderDAO = Depends(),
    runtime_dao: RuntimeDAO = Depends(),
) -> LLMCallsService:
    """Build the calls explorer's service, knowing which provider each gateway instance serves."""
    providers = await provider_dao.list_all()
    runtimes = await runtime_dao.list_all()
    return LLMCallsService(
        getattr(request.app.state, "llm_graph_trace_session_factory", None),
        owners=ProviderOwners(runtimes=runtimes, providers=providers),
        provider_names={str(p.id): p.name for p in providers},
    )
