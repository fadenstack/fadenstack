"""Governance for admins: tool rules, tool classes, and the decisions checks made.

Both live in the gateway's database (``llm_gateway_tool_rule``,
``llm_gateway_policy_decision``). The gateway reads a tenant's rules at most
30 s old, so a change takes effect within half a minute on every worker.

A rule's ``message`` is what users are told; its ``name`` is for admins. The
decision log holds no text: a refused prompt or answer shows as an HMAC
under the tenant's key, which says "the same text again" and nothing more.
The policy's name in a decision (the auditor's view) is EE.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from starlette import status

from faden_backend.db.models.users import User
from faden_backend.web.api.admin.dependencies import require_superuser

router = APIRouter()

Admin = Annotated[User, Depends(require_superuser)]


class ToolRuleIn(BaseModel):
    """A tool rule, as an admin writes it."""

    tenant_id: str = Field(default="default", max_length=128)
    name: str = Field(min_length=1, max_length=256)
    #: A glob over tool names: ``mcp_github__*``, ``knowledge_search``, ``*``.
    tool_pattern: str = Field(min_length=1, max_length=256)
    action: Literal["deny", "approval_required", "allow"]
    #: What the user is told.
    message: str = Field(min_length=1, max_length=2000)
    category: str = Field(default="tool_rule", max_length=64)
    #: The lowest that matches decides; ``allow`` at a lower number makes an exception.
    priority: int = Field(default=100, ge=0, le=100_000)
    enabled: bool = True


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


@router.get("/tool-rules", name="governance_tool_rules")
async def list_tool_rules(request: Request, _admin: Admin, tenant_id: str = "default") -> dict[str, Any]:
    """The tenant's rules, in the order they are tried."""
    async with _gateway(request)() as db:
        rows = await db.execute(
            text(
                f"SELECT {_RULE_COLUMNS} FROM llm_gateway_tool_rule "  # noqa: S608 - fixed column list
                "WHERE tenant_id = :tenant_id ORDER BY priority, created_at",
            ),
            {"tenant_id": tenant_id},
        )
        return {"data": [_rule(r) for r in rows]}


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
        return _rule(row)


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
        return _rule(row)


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
