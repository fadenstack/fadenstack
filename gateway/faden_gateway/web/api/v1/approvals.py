"""``/v1/approvals/{id}``: the user answers a tool call that waits for approval.

A tool rule can say a tool needs the user's approval. In a streamed answer to
a client that reads events, the gateway then sends ``faden.approval_required``
(with an ``approvalId``) and waits; the client asks its user and answers here.
Only the user whose request waits can answer, from any worker.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from faden_gateway.services.gateway.agents import AgentRequest
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.services.gateway.errors import GatewayError, error_from
from faden_gateway.services.gateway.governance import approvals
from faden_gateway.web.api.v1.agents import agent_request

router = APIRouter()


class ApprovalAnswer(BaseModel):
    """The user's answer."""

    approved: bool


async def _answer(auth: AuthContext, approval_id: str, body: ApprovalAnswer) -> JSONResponse:
    answered = await approvals.answer(
        tenant_id=auth.tenant_id, user_id=auth.user_id, approval_id=approval_id, approved=body.approved,
    )
    if not answered:
        return error_from(GatewayError(
            status_code=404,
            message="No call of yours waits for this approval (it may have timed out).",
            code="approval_not_found",
        ))
    return JSONResponse({"approval_id": approval_id, "approved": body.approved})


@router.post("/v1/approvals/{approval_id}")
async def answer_approval(
    approval_id: str,
    body: ApprovalAnswer,
    auth: Annotated[AuthContext, Depends(get_auth_context)],
) -> JSONResponse:
    """Approve or decline a tool call waiting in one of the user's requests."""
    return await _answer(auth, approval_id, body)


@router.post("/v1/agents/{agent}/approvals/{approval_id}")
async def answer_agent_approval(
    approval_id: str,
    body: ApprovalAnswer,
    caller: Annotated[tuple[AuthContext, AgentRequest], Depends(agent_request)],
) -> JSONResponse:
    """The same, with an agent token."""
    return await _answer(caller[0], approval_id, body)
