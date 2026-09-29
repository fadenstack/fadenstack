"""Governance: checks that may refuse a prompt, an answer or a tool call.

Core ships the pipeline and its contract; the checks that read content are
Enterprise (content policies). Every check -- core's tool rules or an EE
policy -- produces the same outcome for the client, so an SDK or a UI handles
one contract:

- **Input** (before anything is stored or sent to a model): a refused prompt
  is HTTP 422, ``governance_error`` / ``policy_blocked``, with a
  ``faden.governance`` object. The turn stays in the session as
  ``blocked_input`` -- the admin's message, no text.
- **Output** (only when a check has an output stage): the answer is checked in
  windows before it is forwarded. A refused answer ends with
  ``finish_reason: content_filter`` (and a ``faden.governance`` event for
  clients that asked for events); streamed or whole, the partial answer is
  not stored.
- **Tool**: a refused call is answered to the model with the admin's message
  and told to the client as ``faden.tool_denied``. A call that needs approval
  waits for the user when the client can ask them (a stream with
  ``X-Faden-Events``); otherwise it is refused, saying so.

A check objects with a :class:`GovernanceDecision` or returns ``None``. The
most restrictive decision wins (block over approval); all are recorded.
Decisions are stored with an HMAC of the text under the tenant's own key --
never the text: the same prompt refused twice shows as the same value, and
nobody can read it back or test guesses without the key.

EE registers checks on :data:`faden_gateway.services.extensions.registry`
(``governance_checks``). With none registered, nothing here costs anything:
no output buffering, no extra query.
"""

from __future__ import annotations

import asyncio
import contextlib
import fnmatch
import hashlib
import hmac
import json
import logging
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from faden_gateway.db.models.gateway import GovernanceKey, PolicyDecisionRecord, ToolRule
from faden_gateway.services.gateway.errors import GatewayError

logger = logging.getLogger(__name__)


class GovernanceStage(StrEnum):
    """Where a check runs."""

    INPUT = "input"
    OUTPUT = "output"
    TOOL = "tool"


@dataclass(frozen=True, slots=True)
class GovernanceContext:
    """What a check sees."""

    stage: GovernanceStage
    tenant_id: str
    user_id: str
    agent_id: str | None
    session_id: str | None
    request_id: str
    messages: Sequence[Mapping[str, Any]] = ()  # input: the request's messages
    text: str = ""  # output: the window being checked
    tool_id: str | None = None  # tool
    arguments: Mapping[str, Any] | None = None  # tool


@dataclass(frozen=True, slots=True)
class GovernanceDecision:
    """An objection."""

    outcome: Literal["block", "approval_required"]
    policy_id: str
    category: str
    #: Shown to the user.
    message: str
    #: The policy's name: for admins and auditors, never shown to the user.
    policy_name: str | None = None


class GovernanceCheck(Protocol):
    """A check; EE registers its own on ``registry.governance_checks``."""

    name: str
    stages: frozenset[GovernanceStage]
    #: The answer is checked in windows of this many characters; 0 without an output stage.
    output_window_chars: int

    async def check(self, ctx: GovernanceContext) -> GovernanceDecision | None: ...


@dataclass(frozen=True, slots=True)
class Verdict:
    """The winning objection, and every objection, for the record."""

    decision: GovernanceDecision
    check: str
    reasons: list[dict[str, Any]]

    @property
    def blocked(self) -> bool:
        return self.decision.outcome == "block"


_RANK = {"block": 2, "approval_required": 1}


async def decide(checks: Sequence[GovernanceCheck], ctx: GovernanceContext) -> Verdict | None:
    """Run every check for *ctx*'s stage; the most restrictive objection, or ``None``.

    A check that fails is skipped (logged), unless it says ``fail_closed``: a
    broken content policy should not stop every chat, unless its owner says
    it must.
    """
    ours = [c for c in checks if ctx.stage in c.stages]
    if not ours:
        return None
    results = await asyncio.gather(*(c.check(ctx) for c in ours), return_exceptions=True)
    found: list[tuple[GovernanceDecision, str]] = []
    for check, result in zip(ours, results, strict=True):
        if isinstance(result, BaseException):
            logger.error("Governance check %s failed at %s", check.name, ctx.stage, exc_info=result)
            if getattr(check, "fail_closed", False):
                found.append((GovernanceDecision(
                    outcome="block", policy_id=f"{check.name}:unavailable", category="unavailable",
                    message="This could not be checked right now; try again shortly.",
                ), check.name))
            continue
        if result is not None:
            found.append((result, check.name))
    if not found:
        return None
    found.sort(key=lambda pair: -_RANK.get(pair[0].outcome, 0))
    reasons = [
        {"check": name, "outcome": d.outcome, "policy_id": d.policy_id, "category": d.category}
        for d, name in found
    ]
    return Verdict(decision=found[0][0], check=found[0][1], reasons=reasons)


def output_window(checks: Sequence[GovernanceCheck]) -> int:
    """The window the answer is checked in; 0 when no check reads answers."""
    return max(
        (c.output_window_chars or 1 for c in checks if GovernanceStage.OUTPUT in c.stages),
        default=0,
    )


# ---------------------------------------------------------------------------
# Core's check: the tenant's tool rules
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Rule:
    """One tool rule, as the check reads it."""

    id: str
    name: str
    pattern: str
    action: str
    message: str
    category: str


class ToolRuleCheck:
    """The tenant's tool rules (``llm_gateway_tool_rule``); the first that matches decides."""

    name = "tool_rules"
    stages = frozenset({GovernanceStage.TOOL})
    output_window_chars = 0

    def __init__(self, rules: Sequence[Rule]) -> None:
        self.rules = rules

    def match(self, tool_id: str) -> Rule | None:
        return next((r for r in self.rules if fnmatch.fnmatchcase(tool_id, r.pattern)), None)

    async def check(self, ctx: GovernanceContext) -> GovernanceDecision | None:
        rule = self.match(ctx.tool_id or "")
        if rule is None or rule.action not in ("deny", "approval_required"):
            return None
        return GovernanceDecision(
            outcome="block" if rule.action == "deny" else "approval_required",
            policy_id=rule.id, category=rule.category, message=rule.message, policy_name=rule.name,
        )


class ToolRules:
    """Each tenant's tool rules, read once per 30 s per worker."""

    def __init__(self, ttl_sec: float = 30.0) -> None:
        self.ttl_sec = ttl_sec
        self._cache: dict[str, tuple[float, tuple[Rule, ...]]] = {}

    def invalidate(self, tenant_id: str | None = None) -> None:
        if tenant_id is None:
            self._cache.clear()
        else:
            self._cache.pop(tenant_id, None)

    async def for_tenant(self, session: AsyncSession, tenant_id: str) -> tuple[Rule, ...]:
        cached = self._cache.get(tenant_id)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]
        rows = (await session.execute(
            select(ToolRule)
            .where(ToolRule.tenant_id == tenant_id, ToolRule.enabled.is_(True))
            .order_by(ToolRule.priority, ToolRule.created_at),
        )).scalars().all()
        rules = tuple(
            Rule(id=str(r.id), name=r.name, pattern=r.tool_pattern, action=r.action,
                 message=r.message, category=r.category)
            for r in rows
        )
        self._cache[tenant_id] = (time.monotonic() + self.ttl_sec, rules)
        return rules


tool_rules = ToolRules()


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


class KeyRing:
    """Each tenant's HMAC key, made on first use and kept in memory."""

    def __init__(self) -> None:
        self._keys: dict[str, bytes] = {}

    async def key(self, session: AsyncSession, tenant_id: str) -> bytes:
        known = self._keys.get(tenant_id)
        if known is not None:
            return known
        query = select(GovernanceKey.key).where(GovernanceKey.tenant_id == tenant_id)
        stored = await session.scalar(query)
        if stored is None:
            await session.execute(
                insert(GovernanceKey)
                .values(tenant_id=tenant_id, key=secrets.token_hex(32))
                .on_conflict_do_nothing(index_elements=["tenant_id"]),
            )
            stored = await session.scalar(query)
        assert stored is not None  # noqa: S101
        key = bytes.fromhex(stored)
        self._keys[tenant_id] = key
        return key


keyring = KeyRing()


def content_hmac(key: bytes, text: str) -> str:
    """HMAC-SHA256 of *text*, hex."""
    return hmac.new(key, text.encode(), hashlib.sha256).hexdigest()


def text_of(messages: Sequence[Mapping[str, Any]]) -> str:
    """The text of the user's messages, as one string, for the HMAC."""
    parts: list[str] = []
    for message in messages:
        if message.get("role") != "user":
            continue
        content = message.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            parts.extend(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
    return "\n".join(parts)


async def record(
    session: AsyncSession,
    ctx: GovernanceContext,
    verdict: Verdict,
    *,
    text: str | None,
    outcome: str | None = None,
) -> tuple[uuid.UUID, str | None]:
    """Write the decision record (not committed); its id and the text's HMAC."""
    digest = content_hmac(await keyring.key(session, ctx.tenant_id), text) if text else None
    row = PolicyDecisionRecord(
        id=uuid.uuid4(),
        tenant_id=ctx.tenant_id,
        user_id=ctx.user_id,
        agent_id=ctx.agent_id,
        session_id=ctx.session_id,
        request_id=ctx.request_id,
        stage=ctx.stage.value,
        outcome=outcome or verdict.decision.outcome,
        policy_id=verdict.decision.policy_id,
        policy_name=verdict.decision.policy_name,
        category=verdict.decision.category,
        message=verdict.decision.message,
        check=verdict.check,
        content_hmac=digest,
        tool_id=ctx.tool_id,
        reasons=verdict.reasons,
    )
    session.add(row)
    await session.flush()
    return row.id, digest


async def record_on_own_session(
    bind: Any, ctx: GovernanceContext, verdict: Verdict, *, text: str | None, outcome: str | None = None,
) -> tuple[uuid.UUID | None, str | None]:
    """:func:`record`, committed on a session of its own; ``(None, None)`` when the database fails.

    During a stream the request's session is gone; a record that cannot be
    written must not turn a refusal into a broken answer.
    """
    try:
        async with AsyncSession(bind, expire_on_commit=False) as own:
            found = await record(own, ctx, verdict, text=text, outcome=outcome)
            await own.commit()
            return found
    except Exception:
        logger.exception("Could not record the governance decision")
        return None, None


# ---------------------------------------------------------------------------
# What the client gets
# ---------------------------------------------------------------------------


def governance_object(
    verdict: Verdict,
    *,
    outcome: str,
    stage: GovernanceStage,
    decision_id: uuid.UUID | None,
    request_id: str,
    session_id: str | None = None,
    message_id: uuid.UUID | None = None,
) -> dict[str, Any]:
    """The ``faden.governance`` object; never the policy's name."""
    found: dict[str, Any] = {
        "outcome": outcome,
        "stage": stage.value,
        "category": verdict.decision.category,
        "message": verdict.decision.message,
        "decision_id": str(decision_id) if decision_id else None,
        "request_id": request_id,
    }
    if session_id is not None:
        found["session_id"] = session_id
    if message_id is not None:
        found["message_id"] = str(message_id)
    return found


def prompt_blocked(governance: dict[str, Any]) -> GatewayError:
    """HTTP 422, the OpenAI error envelope, and ``faden.governance`` beside it."""
    return GatewayError(
        status_code=422,
        message=governance["message"],
        error_type="governance_error",
        code="policy_blocked",
        extra={"faden": {"governance": governance}},
    )


def governance_event(governance: dict[str, Any]) -> dict[str, Any]:
    """A refused answer, as a Faden event."""
    return {"type": "CUSTOM", "name": "faden.governance", "value": governance}


def tool_denied_event(
    call: Mapping[str, Any], name: str, message: str, decision_id: uuid.UUID | None,
) -> dict[str, Any]:
    """A refused tool call (or a client tool not offered), as a Faden event."""
    return {"type": "CUSTOM", "name": "faden.tool_denied", "value": {
        "toolCallId": call.get("id", ""),
        "toolCallName": name,
        "message": message,
        "decisionId": str(decision_id) if decision_id else None,
    }}


def approval_event(
    approval_id: str, call: Mapping[str, Any], name: str, message: str, timeout_s: float,
) -> dict[str, Any]:
    """A tool call waits for the user: answer at ``/v1/approvals/{approvalId}``."""
    return {"type": "CUSTOM", "name": "faden.approval_required", "value": {
        "approvalId": approval_id,
        "toolCallId": call.get("id", ""),
        "toolCallName": name,
        "message": message,
        "timeoutS": int(timeout_s),
    }}


def denied_result(name: str, message: str) -> str:
    """What the model is told about a refused call."""
    return f"Tool '{name}' is not allowed here: {message}"


# ---------------------------------------------------------------------------
# The answer, checked in windows
# ---------------------------------------------------------------------------


def _text_and_end(event: str) -> tuple[str, bool]:
    """The answer text in one SSE event, and whether it carries a finish_reason."""
    data = next((line[6:] for line in event.split("\n") if line.startswith("data: ")), None)
    if data is None or data == "[DONE]":
        return "", False
    try:
        body = json.loads(data)
    except json.JSONDecodeError:
        return "", False
    text, ended = [], False
    for choice in body.get("choices") or [] if isinstance(body, dict) else []:
        text.append((choice.get("delta") or {}).get("content") or "")
        ended = ended or bool(choice.get("finish_reason"))
    return "".join(text), ended


async def govern_sse(  # noqa: C901, PLR0912, PLR0915 - one loop, kept readable in one place
    stream: AsyncIterator[bytes],
    *,
    window: int,
    check: Callable[[str], Awaitable[Verdict | None]],
    on_block: Callable[[Verdict, str], AsyncIterator[bytes]],
) -> AsyncIterator[bytes]:
    """*stream*, with its answer text checked before it is forwarded.

    Every *window* new characters (and at each finish, and at the end) the
    text not yet forwarded -- after the last window already forwarded -- is
    checked. What passes goes out in order, except the last *window*
    characters, which wait for the next check: a phrase up to a window long is
    always checked whole before any of it leaves. Refused: nothing more goes
    out and *on_block* ``(verdict, the answer so far)`` ends the stream.
    Events without text go straight through while nothing waits.
    """
    held: list[tuple[bytes, str]] = []
    passed = ""
    unchecked = 0
    buffer = ""

    def waiting() -> str:
        return "".join(text for _, text in held)

    def release(final: bool) -> list[bytes]:
        nonlocal passed
        out: list[bytes] = []
        while held:
            rest = len(waiting()) - len(held[0][1])
            if not final and rest < window:
                break
            event, text = held.pop(0)
            passed += text
            out.append(event)
        return out

    async def checked(final: bool) -> tuple[Verdict | None, list[bytes]]:
        nonlocal unchecked
        pending = waiting()
        verdict = await check(passed[-window:] + pending) if pending and unchecked else None
        unchecked = 0
        if verdict is not None and verdict.blocked:
            return verdict, []
        return None, release(final)

    try:
        async for chunk in stream:
            buffer += chunk.decode()
            while "\n\n" in buffer:
                raw, buffer = buffer.split("\n\n", 1)
                event = (raw + "\n\n").encode()
                text, ended = _text_and_end(raw)
                done = raw == "data: [DONE]"
                if not held and not text and not done:
                    yield event  # nothing waits on a check
                    continue
                held.append((event, text))
                unchecked += len(text)
                if ended or done or unchecked >= window:
                    verdict, out = await checked(final=ended or done)
                    if verdict is not None:
                        async for piece in on_block(verdict, passed + waiting()):
                            yield piece
                        return
                    for piece in out:
                        yield piece
        if buffer:
            held.append((buffer.encode(), ""))
        unchecked = max(unchecked, 1)
        verdict, out = await checked(final=True)
        if verdict is not None:
            async for piece in on_block(verdict, passed + waiting()):
                yield piece
            return
        for piece in out:
            yield piece
    finally:
        closer = getattr(stream, "aclose", None)
        if closer is not None:
            with contextlib.suppress(Exception):
                await closer()


def content_filter_chunk(head: Mapping[str, Any]) -> bytes:
    """The final chunk of a refused answer."""
    body = {**{k: head[k] for k in ("id", "object", "created", "model") if k in head},
            "choices": [{"index": 0, "delta": {}, "finish_reason": "content_filter"}]}
    body.setdefault("object", "chat.completion.chunk")
    return f"data: {json.dumps(body)}\n\n".encode()


# ---------------------------------------------------------------------------
# Approvals: a tool call waits for the user
# ---------------------------------------------------------------------------


@dataclass
class _Waiting:
    tenant_id: str
    user_id: str
    future: asyncio.Future[bool] = field(default_factory=lambda: asyncio.get_running_loop().create_future())


class Approvals:
    """Calls waiting for a person, answered on any worker.

    The request waits here; ``POST /v1/approvals/{id}`` answers it. With
    Redis, the answer may arrive on another worker: it is published on the
    approval's own channel, which the waiting worker listens to.
    """

    def __init__(self) -> None:
        self._waiting: dict[str, _Waiting] = {}
        self._redis: Any = None

    def use(self, redis: Any | None) -> None:
        self._redis = redis

    async def wait(self, *, tenant_id: str, user_id: str, approval_id: str, timeout: float) -> bool | None:
        """``True`` approved, ``False`` rejected, ``None`` nobody answered in time."""
        waiting = _Waiting(tenant_id=tenant_id, user_id=user_id)
        self._waiting[approval_id] = waiting
        listener: asyncio.Task[None] | None = None
        pubsub = None
        try:
            if self._redis is not None:
                await self._redis.set(
                    f"faden:approval:{approval_id}", json.dumps({"tenant": tenant_id, "user": user_id}),
                    ex=int(timeout) + 60,
                )
                pubsub = self._redis.pubsub()
                await pubsub.subscribe(f"faden:approval:{approval_id}")
                listener = asyncio.create_task(self._listen(pubsub, waiting))
            try:
                return await asyncio.wait_for(asyncio.shield(waiting.future), timeout)
            except TimeoutError:
                return None
        finally:
            self._waiting.pop(approval_id, None)
            if listener is not None:
                listener.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await listener
            if pubsub is not None:
                with contextlib.suppress(Exception):
                    await pubsub.unsubscribe()
                    await pubsub.aclose()
            if self._redis is not None:
                with contextlib.suppress(Exception):
                    await self._redis.delete(f"faden:approval:{approval_id}")

    @staticmethod
    async def _listen(pubsub: Any, waiting: _Waiting) -> None:
        while not waiting.future.done():
            message = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
            if message and message.get("type") == "message" and not waiting.future.done():
                waiting.future.set_result(bool(json.loads(message["data"]).get("approved")))

    def waiting(self) -> list[str]:
        """The approvals this worker waits for."""
        return list(self._waiting)

    async def answer(self, *, tenant_id: str, user_id: str, approval_id: str, approved: bool) -> bool:
        """Answer a waiting call of this user's; ``False`` when there is none."""
        local = self._waiting.get(approval_id)
        if local is not None:
            if (local.tenant_id, local.user_id) != (tenant_id, user_id) or local.future.done():
                return False
            local.future.set_result(approved)
            return True
        if self._redis is None:
            return False
        owner = await self._redis.get(f"faden:approval:{approval_id}")
        if not owner:
            return False
        who = json.loads(owner.decode() if isinstance(owner, bytes) else owner)
        if (who.get("tenant"), who.get("user")) != (tenant_id, user_id):
            return False
        await self._redis.publish(f"faden:approval:{approval_id}", json.dumps({"approved": approved}))
        return True


approvals = Approvals()
