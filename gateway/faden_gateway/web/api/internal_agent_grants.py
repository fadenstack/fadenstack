"""End an agent's running grants at the backend's word: the agent turned off, or on again (sdk/51 8.3).

The backend keeps the agent's switch; the grants are kept here. Its request carries a grant only the backend
can sign -- a short JWT with the users' secret, ``typ`` ``agent_switch``, naming the agent -- so a caller who
reaches the gateway directly cannot end anyone's grants.
"""

from __future__ import annotations

import jwt
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.dependencies import get_db_session
from faden_gateway.services.gateway import grants
from faden_gateway.settings import settings

router = APIRouter()

#: The header with the grant, and the grant's ``typ``.
AGENT_SWITCH_HEADER = "X-Faden-Agent-Switch"
AGENT_SWITCH_TOKEN_TYPE = "agent_switch"  # noqa: S105 - a claim value, not a secret
#: What the ended grants say ended them.
ENDED_BY = "agent_turned_off"


class EndGrantsRequest(BaseModel):
    """Whose grants to end: one agent, by its id."""

    agent: str = Field(min_length=1, max_length=64)


def _granted(token: str | None, body: EndGrantsRequest) -> bool:
    if not token or not settings.jwt_secret:
        return False
    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
            options={"verify_aud": False, "require": ["exp", "typ", "agent"]},
        )
    except jwt.PyJWTError:
        return False
    return claims.get("typ") == AGENT_SWITCH_TOKEN_TYPE and str(claims.get("agent")) == body.agent


@router.post("/internal/agent-grants-end", include_in_schema=False)
async def end_agent_grants(
    body: EndGrantsRequest,
    grant: str | None = Header(default=None, alias=AGENT_SWITCH_HEADER),
    session: AsyncSession = Depends(get_db_session),
) -> dict[str, int]:
    """End every running or paused grant of the agent, on every device; how many ended. Idempotent."""
    if not _granted(grant, body):
        raise HTTPException(status_code=403, detail="No agent switch grant for this.")
    ended = await grants.end_agent(session, body.agent, by=ENDED_BY)
    await session.commit()
    return {"ended": ended}
