"""``/v1/approvals/{id}``: the user answers a tool call that waits for approval.

A tool rule can say a tool needs the user's approval. In a streamed answer to
a client that reads events, the gateway then sends ``faden.approval_required``
(with an ``approvalId``) and waits; the client asks its user and answers here.
Only the user whose request waits can answer, from any worker. A call of the
client's own tool gets the same event without an ``approvalId``: the client
runs that tool, so it asks and nothing is answered here.

An answer names what it covers (``scope``: ``once``, ``chat``, ``always``).
A destructive or unclassified call, or one a rule asks about, takes ``once``
only: another scope is refused (409), and so is none while
``approvals_require_answer_scope`` is on. The call goes on waiting for an
answer it takes. A "no" is always taken.

The first answer taken stands. Sending the same answer again (the same
``approved`` and ``scope``) is answered as the first was, 200 with the same
body, for ten minutes after it was taken, also once the call no longer
waits: a client may retry when the response to its answer was lost. Another
answer is refused (409 ``approval_already_answered``). While the call waits,
its stream carries a ``: keep-alive`` comment every
``governance_approval_keepalive_s`` (15 s by default).
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from faden_gateway.services.gateway.agents import AgentRequest
from faden_gateway.services.gateway.auth import AuthContext, get_auth_context
from faden_gateway.services.gateway.errors import GatewayError, error_from
from faden_gateway.services.gateway.governance import Answered, ApprovalScope, approvals
from faden_gateway.settings import settings
from faden_gateway.web.api.v1.agents import agent_request

router = APIRouter()

_REFUSED = {
    Answered.UNKNOWN: (
        404, "approval_not_found", "No call of yours waits for this approval (it may have timed out).",
    ),
    Answered.ONCE_ONLY: (
        409, "approval_once_only",
        (
            "This call can be approved for itself only (scope 'once'): it is destructive, unclassified, "
            "or a rule asks about it every time."
        ),
    ),
    Answered.SCOPE_REQUIRED: (
        409, "approval_scope_required",
        "Say what the approval covers: this call can be approved for itself only (scope 'once').",
    ),
    Answered.ALREADY_ANSWERED: (
        409, "approval_already_answered", "This call was answered already with another answer, which stands.",
    ),
}


class ApprovalAnswer(BaseModel):
    """The user's answer."""

    approved: bool
    #: What an approval covers: this call, this chat, or always on the device. Older clients send none.
    scope: ApprovalScope | None = None
    #: The call's arguments' SHA-256 (sdk/51 8.10). Taken, but not needed: the gateway holds the call, shows
    #: its arguments in ``faden.approval_required`` with ``argumentsSha256``, and its decision record keeps
    #: that hash, its own.
    arguments_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")


async def _answer(auth: AuthContext, approval_id: str, body: ApprovalAnswer) -> JSONResponse:
    answered = await approvals.answer(
        tenant_id=auth.tenant_id, user_id=auth.user_id, approval_id=approval_id, approved=body.approved,
        scope=body.scope, require_scope=settings.approvals_require_answer_scope,
        arguments_sha256=body.arguments_sha256,
    )
    if answered is not Answered.TAKEN:
        status, code, message = _REFUSED[answered]
        return error_from(GatewayError(status_code=status, message=message, code=code))
    return JSONResponse({"approval_id": approval_id, "approved": body.approved, "scope": body.scope})


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
