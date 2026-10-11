"""``/v1/client/ws``: the device channel, where a device offers the tools it runs.

Who may connect is decided here, before the device registers anything (see
``services/gateway/client_ws.py`` for the channel itself):

- an agent token: the agent it names must still let the user in (the same
  check as the agent routes); its ``did`` is the device, and the device only
  serves that agent's calls;
- a user's own token: the device serves the user's sessions.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, WebSocket

from faden_gateway.db.dao.gateway_dao import GatewayDAO
from faden_gateway.db.dao.session_dao import SessionDAO
from faden_gateway.services.extensions import registry
from faden_gateway.services.gateway.agents import AgentLookupError, decide
from faden_gateway.services.gateway.auth import (
    AGENT_TOKEN_TYPE,
    _refresh_jwt_secret_if_needed,
    caller_context,
    get_auth_context_from_claims,
    verify_token,
)
from faden_gateway.services.gateway.client_ws import DeviceIdentity, handle_client_websocket
from faden_gateway.services.gateway.errors import GatewayError

router = APIRouter()


def _agent_of(claims: dict[str, Any]) -> str | None:
    aud = claims.get("aud")
    for value in aud if isinstance(aud, list) else [aud]:
        if isinstance(value, str) and value.startswith("agent:"):
            return value[len("agent:"):]
    return None


async def authenticate_device(app: Any, token: str) -> DeviceIdentity:
    """Who *token* is, for the device channel; ``PermissionError(code, message)`` when refused."""
    await _refresh_jwt_secret_if_needed()
    try:
        agent_token = verify_token(token, check_audience=False).get("typ") == AGENT_TOKEN_TYPE
        # A user's token keeps the configured audience check.
        claims = verify_token(token, check_audience=not agent_token)
        auth = get_auth_context_from_claims(claims, token)
    except GatewayError as exc:
        raise PermissionError(exc.code or "invalid_token", exc.message) from exc
    if not agent_token:
        try:
            auth = await caller_context(app, claims, token)
        except GatewayError as exc:
            raise PermissionError(exc.code or "invalid_token", exc.message) from exc
        return DeviceIdentity(tenant_id=auth.tenant_id, user_id=auth.user_id, device_id=None, agent_id=None)

    agent_id = _agent_of(claims)
    directory = getattr(app.state, "agent_directory", None)
    if agent_id is None or directory is None:
        raise PermissionError("agents_unavailable", "Agents are not available here.")
    try:
        profile = await directory.agent(auth.tenant_id, agent_id)
        user = await directory.user(auth.user_id) if profile is not None else None
    except AgentLookupError as exc:
        raise PermissionError("agent_lookup_failed", "The agent could not be looked up; try again.") from exc
    if profile is None or profile.id != agent_id:
        raise PermissionError("agent_not_found", "No such agent.")
    resolver = getattr(app.state, "group_membership_resolver", None)
    groups = (await resolver.resolve(auth.user_id) or ()) if resolver is not None else ()
    decision = await decide(profile, user_id=auth.user_id, user=user, groups=groups, registry=registry)
    if not decision.allowed:
        raise PermissionError(decision.code, decision.message)
    device = str(claims.get("did") or "").strip()
    if not device:
        raise PermissionError("invalid_token", "The agent token names no device.")
    return DeviceIdentity(tenant_id=auth.tenant_id, user_id=auth.user_id, device_id=device, agent_id=agent_id)


class _Briefly:
    """The DAO calls the channel makes, each on a session of its own.

    A device stays connected for hours: a request-scoped session would hold a
    pooled connection all that time. (The route took one through
    ``get_db_session``, which needs a ``Request`` a WebSocket route does not
    get: every connection failed with a 500.)
    """

    def __init__(self, factory: Any) -> None:
        self._factory = factory

    async def register_client_capabilities(
        self, session_id: uuid.UUID, client_id: str, tools: list[Any],
    ) -> None:
        async with self._factory() as db:
            await GatewayDAO(db).register_client_capabilities(session_id, client_id, tools)
            await db.commit()

    async def mark_client_tools_unavailable(self, session_id: uuid.UUID, client_id: str) -> None:
        async with self._factory() as db:
            await GatewayDAO(db).mark_client_tools_unavailable(session_id, client_id)
            await db.commit()

    async def owns_session(self, identity: DeviceIdentity, session_id: uuid.UUID) -> bool:
        async with self._factory() as db:
            found = await SessionDAO(db).get_session(
                session_id=session_id, tenant_id=identity.tenant_id, user_id=identity.user_id,
            )
            return found is not None


@router.websocket("/v1/client/ws")
async def client_websocket(ws: WebSocket) -> None:
    """A device's connection: authenticated first, then its tools, then calls."""
    dao = _Briefly(ws.app.state.db_session_factory)

    async def authenticate(token: str) -> DeviceIdentity:
        return await authenticate_device(ws.app, token)

    await handle_client_websocket(
        ws, dao,  # type: ignore[arg-type]
        authenticate=authenticate, owns_session=dao.owns_session,
    )
