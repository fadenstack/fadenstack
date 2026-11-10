"""Governance for admins: tool rules, tool classes, the decisions checks made, and the grants running.

What ran under a grant (its calls, with their places, arguments and results, which the gateway decrypts for the
read) is for holders of the role ``approvals_auditor`` only, whatever else they are, and each read is in the
audit trail (sdk/09 L-64, L-65).

All live in the gateway's database (``llm_gateway_tool_rule``,
``llm_gateway_policy_decision``, ``approval_grant``). The gateway reads a tenant's rules at most
30 s old, so a change takes effect within half a minute on every worker.

A rule's ``message`` is what users are told; its ``name`` is for admins. The
decision log holds no text: a refused prompt or answer shows as an HMAC
under the tenant's key, which says "the same text again" and nothing more.
The policy's name in a decision (the auditor's view) is EE.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from faden_backend.db.dao.audit_dao import AuditDAO
from faden_backend.db.dao.rbac_dao import APPROVALS_EVIDENCE_AUDIT, RbacDAO
from faden_backend.db.dependencies import get_db_session
from faden_backend.db.models.containers import AuditResult
from faden_backend.db.models.users import User, current_active_user
from faden_backend.settings import settings
from faden_backend.web.api.admin.dependencies import require_superuser

router = APIRouter()
logger = logging.getLogger(__name__)

Admin = Annotated[User, Depends(require_superuser)]


class ToolRuleIn(BaseModel):
    """A tool rule, as an admin writes it."""

    tenant_id: str = Field(default="default", max_length=128)
    name: str = Field(min_length=1, max_length=256)
    #: A glob over tool names: ``mcp_github__*``, ``knowledge_search``, ``*``.
    tool_pattern: str = Field(min_length=1, max_length=256)
    #: No ``allow``: a matching deny or "Ask first" wins over it, so it would change nothing. Rules made
    #: with it before are still listed, as ``no_effect``.
    action: Literal["deny", "approval_required"]
    #: What the user is told.
    message: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="tool_rule", max_length=64)
    #: Orders rules of the same action (the first gives the message); a matching deny wins over
    #: "Ask first", whatever the priority.
    priority: int = Field(default=100, ge=0, le=100_000)
    enabled: bool = True

    @field_validator("action", mode="before")
    @classmethod
    def _no_allow(cls, action: object) -> object:
        if action == "allow":
            msg = (
                'An allow rule would change nothing: a deny or "Ask first" that matches wins over it. '
                "To let a tool run without asking, classify it for the tool modes."
            )
            raise ValueError(msg)
        return action


def _gateway(request: Request) -> Any:
    factory = getattr(request.app.state, "llm_graph_trace_session_factory", None)
    if factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Gateway DB is not configured.",
        )
    return factory


_RULE_COLUMNS = (
    "id, tenant_id, name, tool_pattern, action, message, category, priority, enabled, created_at, updated_at"
)


def _rule(row: Any) -> dict[str, Any]:
    out = dict(row._mapping)  # noqa: SLF001
    out["id"] = str(out["id"])
    for key in ("created_at", "updated_at"):
        out[key] = out[key].isoformat() if out.get(key) else None
    return out


def _tool_rule(row: Any) -> dict[str, Any]:
    out = _rule(row)
    #: An allow rule made before they were refused: it changes nothing.
    out["no_effect"] = out["action"] == "allow"
    return out


@router.get("/tool-rules", name="governance_tool_rules")
async def list_tool_rules(request: Request, _admin: Admin, tenant_id: str = "default") -> dict[str, Any]:
    """The tenant's rules, by priority; an old ``allow`` rule is marked ``no_effect``."""
    async with _gateway(request)() as db:
        rows = await db.execute(
            text(
                f"SELECT {_RULE_COLUMNS} FROM llm_gateway_tool_rule "  # noqa: S608 - fixed column list
                "WHERE tenant_id = :tenant_id ORDER BY priority, created_at",
            ),
            {"tenant_id": tenant_id},
        )
        return {"data": [_tool_rule(r) for r in rows]}


@router.post("/tool-rules", name="governance_tool_rule_create", status_code=status.HTTP_201_CREATED)
async def create_tool_rule(body: ToolRuleIn, request: Request, _admin: Admin) -> dict[str, Any]:
    """Add a rule."""
    async with _gateway(request)() as db:
        row = (await db.execute(
            text(
                "INSERT INTO llm_gateway_tool_rule "  # noqa: S608 - a fixed column list
                "(id, tenant_id, name, tool_pattern, action, message, category, priority, enabled, "
                "created_at, updated_at) VALUES (:id, :tenant_id, :name, :tool_pattern, :action, :message, "
                f":category, :priority, :enabled, NOW(), NOW()) RETURNING {_RULE_COLUMNS}",
            ),
            {"id": uuid.uuid4(), **body.model_dump()},
        )).one()
        await db.commit()
        return _tool_rule(row)


@router.put("/tool-rules/{rule_id}", name="governance_tool_rule_update")
async def update_tool_rule(
    rule_id: uuid.UUID, body: ToolRuleIn, request: Request, _admin: Admin,
) -> dict[str, Any]:
    """Change a rule."""
    async with _gateway(request)() as db:
        row = (await db.execute(
            text(
                "UPDATE llm_gateway_tool_rule SET tenant_id = :tenant_id, name = :name, "  # noqa: S608
                "tool_pattern = :tool_pattern, action = :action, message = :message, category = :category, "
                "priority = :priority, enabled = :enabled, updated_at = NOW() "
                f"WHERE id = :id RETURNING {_RULE_COLUMNS}",
            ),
            {"id": rule_id, **body.model_dump()},
        )).one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such rule.")
        await db.commit()
        return _tool_rule(row)


@router.delete(
    "/tool-rules/{rule_id}", name="governance_tool_rule_delete", status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_tool_rule(rule_id: uuid.UUID, request: Request, _admin: Admin) -> None:
    """Remove a rule."""
    async with _gateway(request)() as db:
        gone = await db.execute(text("DELETE FROM llm_gateway_tool_rule WHERE id = :id"), {"id": rule_id})
        if not gone.rowcount:  # type: ignore[attr-defined]
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such rule.")
        await db.commit()


class ToolClassIn(BaseModel):
    """What a tool does, as an admin says it (the classes: gateway ``services/gateway/tool_classes.py``)."""

    tenant_id: str = Field(default="default", max_length=128)
    #: A glob over tool names: ``mcp.github.*``, ``read_page``.
    tool_pattern: str = Field(min_length=1, max_length=256)
    #: ``annotations``: trust what these tools' MCP server says about them.
    tool_class: Literal["read", "write", "destructive", "annotations"]
    #: Reaches outside the organisation. Recorded and shown; no mode acts on it yet.
    open_world: bool | None = None
    note: str | None = Field(default=None, max_length=2000)
    priority: int = Field(default=100, ge=0, le=100_000)


_CLASS_COLUMNS = "id, tenant_id, tool_pattern, tool_class, open_world, note, priority, created_at, updated_at"


@router.get("/tool-classes", name="governance_tool_classes")
async def list_tool_classes(request: Request, _admin: Admin, tenant_id: str = "default") -> dict[str, Any]:
    """The tenant's classification rules, in the order they are tried.

    A tool no rule classifies (and that is not one of the gateway's own) is
    ``unknown``: it always waits for the user.
    """
    async with _gateway(request)() as db:
        rows = await db.execute(
            text(
                f"SELECT {_CLASS_COLUMNS} FROM llm_gateway_tool_classification "  # noqa: S608 - fixed columns
                "WHERE tenant_id = :tenant_id ORDER BY priority, created_at",
            ),
            {"tenant_id": tenant_id},
        )
        return {"data": [_rule(r) for r in rows]}


@router.post("/tool-classes", name="governance_tool_class_create", status_code=status.HTTP_201_CREATED)
async def create_tool_class(body: ToolClassIn, request: Request, _admin: Admin) -> dict[str, Any]:
    """Classify tools."""
    async with _gateway(request)() as db:
        row = (await db.execute(
            text(
                "INSERT INTO llm_gateway_tool_classification "  # noqa: S608 - a fixed column list
                "(id, tenant_id, tool_pattern, tool_class, open_world, note, priority, "
                "created_at, updated_at) VALUES (:id, :tenant_id, :tool_pattern, :tool_class, "
                ":open_world, :note, :priority, NOW(), NOW()) "
                f"RETURNING {_CLASS_COLUMNS}",
            ),
            {"id": uuid.uuid4(), **body.model_dump()},
        )).one()
        await db.commit()
        return _rule(row)


@router.put("/tool-classes/{rule_id}", name="governance_tool_class_update")
async def update_tool_class(
    rule_id: uuid.UUID, body: ToolClassIn, request: Request, _admin: Admin,
) -> dict[str, Any]:
    """Change a classification rule."""
    async with _gateway(request)() as db:
        row = (await db.execute(
            text(
                "UPDATE llm_gateway_tool_classification SET tenant_id = :tenant_id, "  # noqa: S608
                "tool_pattern = :tool_pattern, tool_class = :tool_class, open_world = :open_world, "
                "note = :note, "
                f"priority = :priority, updated_at = NOW() WHERE id = :id RETURNING {_CLASS_COLUMNS}",
            ),
            {"id": rule_id, **body.model_dump()},
        )).one_or_none()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such rule.")
        await db.commit()
        return _rule(row)


@router.delete(
    "/tool-classes/{rule_id}", name="governance_tool_class_delete", status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_tool_class(rule_id: uuid.UUID, request: Request, _admin: Admin) -> None:
    """Remove a classification rule."""
    async with _gateway(request)() as db:
        gone = await db.execute(
            text("DELETE FROM llm_gateway_tool_classification WHERE id = :id"), {"id": rule_id},
        )
        if not gone.rowcount:  # type: ignore[attr-defined]
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such rule.")
        await db.commit()


@router.get("/decisions", name="governance_decisions")
async def list_decisions(
    request: Request,
    _admin: Admin,
    tenant_id: str = "default",
    stage: Literal["input", "output", "tool"] | None = None,
    outcome: str | None = None,
    user_id: str | None = None,
    agent_id: str | None = None,
    since: datetime | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> dict[str, Any]:
    """What the checks decided, newest first -- never the text, and not the policy's name (EE)."""
    async with _gateway(request)() as db:
        rows = await db.execute(
            text(
                "SELECT id, user_id, agent_id, session_id, request_id, stage, outcome, policy_id, category, "
                'message, "check", content_hmac, tool_id, created_at FROM llm_gateway_policy_decision '

                "WHERE tenant_id = :tenant_id "
                "AND (CAST(:stage AS TEXT) IS NULL OR stage = :stage) "
                "AND (CAST(:outcome AS TEXT) IS NULL OR outcome = :outcome) "
                "AND (CAST(:user_id AS TEXT) IS NULL OR user_id = :user_id) "
                "AND (CAST(:agent_id AS TEXT) IS NULL OR agent_id = :agent_id) "
                "AND (CAST(:since AS TIMESTAMPTZ) IS NULL OR created_at >= :since) "
                "ORDER BY created_at DESC LIMIT :limit",
            ),
            {"tenant_id": tenant_id, "stage": stage, "outcome": outcome, "user_id": user_id,
             "agent_id": agent_id, "since": since, "limit": limit},
        )
        data = []
        for row in rows:
            item = dict(row._mapping)  # noqa: SLF001
            item["id"] = str(item["id"])
            item["created_at"] = item["created_at"].isoformat()
            data.append(item)
        return {"data": data}


def _grant(row: Any) -> dict[str, Any]:
    out = dict(row._mapping)  # noqa: SLF001
    out["id"] = str(out["id"])
    if isinstance(out.get("scope"), str):
        out["scope"] = json.loads(out["scope"])
    for key in ("issued_at", "expires_at", "paused_at", "step_up_at"):
        out[key] = out[key].isoformat() if out.get(key) else None
    return out


@router.get("/grants", name="governance_grants")
async def list_grants(request: Request, _admin: Admin, tenant_id: str = "default") -> dict[str, Any]:
    """The grants running now (sdk/38 §6, D9): active or paused, not ended, not spent; the newest first.

    What a person let an agent on one of their devices do for a while without asking: one tool, reads and
    writes in named places, or -- Bypass -- whatever the chat's mode would ask about. The time left is the
    database's, as the gateway counts it. ``flags``: calls of a Bypass session where the device's report and
    the gateway's record differ (L-61). ``step_up_method``, ``step_up_provider``, ``step_up_at``: how and when
    the person signed in again to switch Bypass on (sdk/41, L-59).
    """
    async with _gateway(request)() as db:
        rows = await db.execute(
            text(
                "SELECT id, kind, status, user_id, agent_id, agent_version, device_id, conversation_id, "
                "scope, issued_at, expires_at, paused_at, max_calls, calls_used, "
                "step_up_method, step_up_provider, step_up_at, "
                "GREATEST(0, EXTRACT(EPOCH FROM expires_at - clock_timestamp()))::int AS remaining_s, "
                "(SELECT count(*) FROM approval_grant_flag f WHERE f.grant_id = approval_grant.id)::int "
                "AS flags "
                "FROM approval_grant WHERE tenant_id = :tenant_id AND status IN ('active', 'paused') "
                "AND expires_at > clock_timestamp() AND (max_calls IS NULL OR calls_used < max_calls) "
                "ORDER BY issued_at DESC LIMIT 500",
            ),
            {"tenant_id": tenant_id},
        )
        return {"data": [_grant(row) for row in rows]}


@router.delete(
    "/grants/{grant_id}", name="governance_grant_revoke", status_code=status.HTTP_204_NO_CONTENT,
)
async def revoke_grant(grant_id: uuid.UUID, request: Request, admin: Admin) -> None:
    """End a running grant from its next call on (D9); a call already running finishes.

    The grant keeps who ended it (``revoked_by`` ``admin``) and when; which admin goes to the security log
    (ASVS 5.0 16.3.2: authorization decisions are logged).
    """
    async with _gateway(request)() as db:
        ended = await db.execute(
            text(
                "UPDATE approval_grant SET status = 'revoked', revoked_at = clock_timestamp(), "
                "revoked_by = 'admin' WHERE id = :id AND status <> 'revoked' "
                "AND expires_at > clock_timestamp() RETURNING tenant_id",
            ),
            {"id": grant_id},
        )
        tenant = ended.scalar_one_or_none()
        if tenant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such running grant.")
        await db.commit()
    logger.info("Admin %s revoked grant %s of tenant %s", admin.id, grant_id, tenant)


def _recorded_call(row: Any) -> dict[str, Any]:
    out = dict(row._mapping)  # noqa: SLF001
    out["id"] = str(out["id"])
    for key in ("created_at", "reported_at"):
        out[key] = out[key].isoformat() if out.get(key) else None
    return out


async def _evidence(grant_id: uuid.UUID, reader: uuid.UUID, tenant_id: str) -> dict[str, Any] | None:
    """The grant's calls with their places, arguments, outcomes and summaries, as the gateway decrypts them.

    ``None`` when the gateway does not hand them over: the list then shows what this database holds.
    """
    from faden_backend.services.chat.gateway_client import EvidenceUnavailable, shared_client  # noqa: PLC0415

    try:
        return await shared_client().grant_evidence(
            {"grant": str(grant_id), "part": "calls"}, reader=str(reader), tenant_id=tenant_id,
            secret=settings.users_secret,
        )
    except EvidenceUnavailable as exc:
        logger.warning("The evidence of grant %s could not be read from the gateway: %s", grant_id, exc)
        return None


@router.get("/grants/{grant_id}/calls", name="governance_grant_calls")
async def grant_calls(
    grant_id: uuid.UUID,
    request: Request,
    user: Annotated[User, Depends(current_active_user)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> dict[str, Any]:
    """Every call recorded under one grant: tool, side, time, whether the device reported it (L-64).

    With each call its place, arguments, outcome and the start of its result, as far as the grant recorded
    them: they stay encrypted under the gateway's own key (``grant-evidence``), so the gateway decrypts them
    for this read (``evidence``: ``unavailable`` when it could not). Only for holders of
    ``approvals.evidence:audit`` through a role (``approvals_auditor``); superuser status alone does not count
    (L-65: a dedicated audit permission). Every read and every refusal is in the audit trail (ASVS 5.0
    16.3.2), with the grant and whose it was, before anything is decrypted.
    """
    allowed = await RbacDAO(session).has_permission(user.id, *APPROVALS_EVIDENCE_AUDIT)
    async with _gateway(request)() as db:
        grant = (await db.execute(
            text(
                "SELECT id, user_id, kind, tenant_id, step_up_method, step_up_provider, step_up_at "
                "FROM approval_grant WHERE id = :id",
            ),
            {"id": grant_id},
        )).first()
        if grant is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such grant.")
        await AuditDAO(session).log(
            action="approvals.evidence.view", target_type="approval_grant", target_id=str(grant_id),
            result=AuditResult.ALLOW if allowed else AuditResult.DENY, actor_id=user.id, severity="high",
            metadata_json=json.dumps(
                {"person_id": grant.user_id, "kind": grant.kind,
                 **({"arguments": True} if allowed else {"refused": "not_auditor"})},
            ),
        )
        if not allowed:
            # The audit row goes in with the refusal (the session commits it on the way out).
            await session.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Reading what ran under a grant needs the role approvals_auditor.",
            )
        await session.commit()  # the read is recorded before the gateway decrypts anything
        rows = await db.execute(
            text(
                "SELECT c.id, c.request_id, c.call_id, c.tool_id, c.side, c.created_at, "
                "c.reported_at, (SELECT f.kind FROM approval_grant_flag f WHERE f.grant_id = c.grant_id "
                "AND f.call_id = c.call_id LIMIT 1) AS flag "
                "FROM approval_grant_call c WHERE c.grant_id = :id ORDER BY c.created_at LIMIT 5000",
            ),
            {"id": grant_id},
        )
        device_only = await db.execute(
            text(
                "SELECT call_id, created_at FROM approval_grant_flag WHERE grant_id = :id "
                "AND kind = 'device_only' ORDER BY created_at",
            ),
            {"id": grant_id},
        )
        calls = [_recorded_call(row) for row in rows]
        flagged = [{"call_id": r.call_id, "created_at": r.created_at.isoformat()} for r in device_only]
    found = await _evidence(grant_id, user.id, grant.tenant_id)
    decrypted = {c["id"]: c for c in (found or {}).get("calls") or ()}
    for call in calls:
        held = decrypted.get(call["id"])
        if held is not None:
            call.update({k: held.get(k) for k in ("target", "arguments", "outcome", "result", "result_at")})
    return {
        "data": calls,
        "device_only": flagged,
        "kind": grant.kind,
        "evidence": "unavailable" if found is None else "shown",
        "evidence_level": (found or {}).get("evidence_level"),
        "reason": (found or {}).get("reason"),
        # How and when the person signed in again to switch Bypass on (sdk/41).
        "step_up": (
            {
                "method": grant.step_up_method, "provider": grant.step_up_provider,
                "at": grant.step_up_at.isoformat() if grant.step_up_at else None,
            }
            if grant.step_up_method else None
        ),
    }
