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
  ``X-Faden-Events``); otherwise it is refused, saying so. The client's own
  tools run on the client: a call of one is marked as it is handed over
  (``faden.approval_required`` without an ``approvalId``, the client asks),
  and a tool a rule says needs approval is not offered to a client that
  cannot be told.

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

#: The category of the objection a ``fail_closed`` check makes when it fails: nothing was decided.
UNAVAILABLE = "unavailable"
#: What the user is told then: the check failed -- not that a rule refused.
_CHECK_FAILED = {
    GovernanceStage.TOOL: "A check failed, so this tool call did not run. Try again shortly.",
}
_COULD_NOT_CHECK = "This could not be checked right now; try again shortly."


async def decide(checks: Sequence[GovernanceCheck], ctx: GovernanceContext) -> Verdict | None:
    """Run every check for *ctx*'s stage; the most restrictive objection, or ``None``.

    A check that fails is skipped (logged), unless it says ``fail_closed``: a
    broken content policy should not stop every chat, unless its owner says
    it must. Core's tool checks do (ASVS 5.0 V16.5.3: no fail-open). The
    objection a failed check makes is :data:`UNAVAILABLE` and says so.
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
                    outcome="block", policy_id=f"{check.name}:{UNAVAILABLE}", category=UNAVAILABLE,
                    message=_CHECK_FAILED.get(ctx.stage, _COULD_NOT_CHECK),
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


#: Among the rules that match a tool, deny wins, then "Ask first", then allow.
_RULE_ORDER = {"deny": 0, "approval_required": 1, "allow": 2}


class ToolRuleCheck:
    """The tenant's tool rules (``llm_gateway_tool_rule``): deny wins, then "Ask first", then allow.

    The action decides, whatever the priority, so an ``allow`` never switches off a deny or an "Ask first"
    that also matches (ASVS 5.0 V8.2.1: a tool the admin denied runs for nobody). Priority orders rules of
    the same action: the first of them gives the message. An ``allow`` is only on older rules (the admin
    API takes no new one) and changes nothing.
    """

    name = "tool_rules"
    stages = frozenset({GovernanceStage.TOOL})
    output_window_chars = 0
    #: A rule that cannot be checked refuses the call (ASVS 5.0 V16.5.3): its "Ask first" is a floor item.
    fail_closed = True

    def __init__(self, rules: Sequence[Rule]) -> None:
        self.rules = rules

    def match(self, tool_id: str) -> Rule | None:
        matching = (r for r in self.rules if fnmatch.fnmatchcase(tool_id, r.pattern))
        return min(matching, key=lambda r: _RULE_ORDER.get(r.action, len(_RULE_ORDER)), default=None)

    async def check(self, ctx: GovernanceContext) -> GovernanceDecision | None:
        rule = self.match(ctx.tool_id or "")
        if rule is None or rule.action not in ("deny", "approval_required"):
            return None
        return GovernanceDecision(
            outcome="block" if rule.action == "deny" else "approval_required",
            policy_id=rule.id, category=rule.category, message=rule.message, policy_name=rule.name,
        )


class ToolRules:
    """Each tenant's tool rules, read whenever calls are checked, by default.

    They decide whether a tool may run, so a rule changed holds for the next call that runs, in every worker
    (ASVS 5.0 V8.3.2): a request reads them as it starts, again at each tool round, and again after the user
    answered a call that waited (``service._read_tool_policy``). They live in the gateway's own database,
    which the count of changes to access does not cover. A *ttl_sec* above 0 keeps them that long (tests).
    """

    def __init__(self, ttl_sec: float = 0.0) -> None:
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
    answer_scope: str | None = None,
    arguments_sha256: str | None = None,
) -> tuple[uuid.UUID, str | None]:
    """Write the decision record (not committed); its id and the text's HMAC.

    *answer_scope*: what the user's answer to a waiting call said it covers. *arguments_sha256*: the call's
    arguments' SHA-256 as the answering client hashed them (sdk/51 8.10), kept as sent.
    """
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
        answer_scope=answer_scope,
        arguments_sha256=arguments_sha256,
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
    answer_scope: str | None = None, arguments_sha256: str | None = None,
) -> tuple[uuid.UUID | None, str | None]:
    """:func:`record`, committed on a session of its own; ``(None, None)`` when the database fails.

    During a stream the request's session is gone; a record that cannot be
    written must not turn a refusal into a broken answer.
    """
    try:
        async with AsyncSession(bind, expire_on_commit=False) as own:
            found = await record(
                own, ctx, verdict, text=text, outcome=outcome, answer_scope=answer_scope,
                arguments_sha256=arguments_sha256,
            )
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
    *, tool_class: str = "unknown", rule: bool = True, open_world: bool = False,
) -> dict[str, Any]:
    """A gateway tool call waits for the user: answer at ``/v1/approvals/{approvalId}``.

    *tool_class* is the class the gateway holds for the tool; *rule* says whether an admin's rule (or a
    policy) asks, rather than the chat's tool mode: then the client asks every time, never "for this chat".
    *open_world* (sent only when true): the gateway holds the tool open-world, so below Auto the client
    offers "for this chat" at most, never "always" (sdk/51 F08 §4).
    """
    return {"type": "CUSTOM", "name": "faden.approval_required", "value": {
        "approvalId": approval_id,
        "toolCallId": call.get("id", ""),
        "toolCallName": name,
        "message": message,
        "timeoutS": int(timeout_s),
        "toolClass": tool_class,
        "rule": rule,
        **({"openWorld": True} if open_world else {}),
    }}


def client_approval_event(
    call: Mapping[str, Any], name: str, message: str, decision_id: uuid.UUID | None,
    *, tool_class: str, rule: bool, open_world: bool = False,
) -> dict[str, Any]:
    """A call of the client's own tool the user must approve first: the client asks; nothing waits here.

    The same event as :func:`approval_event`, without an ``approvalId``. An older client that only answers
    events with an ``approvalId`` ignores it; one before 0.3.0 ignores ``openWorld`` and decides by the class.
    """
    return {"type": "CUSTOM", "name": "faden.approval_required", "value": {
        "toolCallId": call.get("id", ""),
        "toolCallName": name,
        "message": message,
        "decisionId": str(decision_id) if decision_id else None,
        "toolClass": tool_class,
        "rule": rule,
        **({"openWorld": True} if open_world else {}),
    }}


def denied_result(name: str, message: str, *, unchecked: bool = False) -> str:
    """What the model is told about a refused call; *unchecked*: a check failed, nothing refused it."""
    if unchecked:
        return f"Tool '{name}' did not run: {message}"
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
    Events without text go straight through while nothing waits. A comment
    (a keep-alive) always does: it carries nothing to check. The gateway's
    own events (``event: faden``: a tool round starting, a call waiting for
    the person) end the text before them, which is checked and goes out
    first: a call that waits for an answer is seen while it waits.
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
                if all(line.startswith(":") for line in raw.split("\n")):
                    yield event  # a comment: nothing to check, nothing to keep in order
                    continue
                text, ended = _text_and_end(raw)
                done = raw == "data: [DONE]"
                if not held and not text and not done:
                    yield event  # nothing waits on a check
                    continue
                if raw.startswith("event: faden\n"):
                    # The text before the gateway's own event is complete: checked, it goes out first.
                    verdict, out = await checked(final=True)
                    if verdict is not None:
                        async for piece in on_block(verdict, passed + waiting()):
                            yield piece
                        return
                    for piece in out:
                        yield piece
                    yield event
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


#: What an approval says it covers: this call, this chat, or always (on the device that answered).
ApprovalScope = Literal["once", "chat", "always"]

#: What the gateway sends while a call waits for the person: an SSE comment, which readers skip (a line that
#: starts with ``:``), so that a client's idle limit or a proxy does not take the quiet stream for a dead one.
KEEPALIVE = b": keep-alive\n\n"

#: How long a taken answer is kept, so that the same answer sent again -- a client's retry after the response
#: to its first try was lost -- is answered as the first was, also after the call stopped waiting.
ANSWER_KEPT_S = 600

#: How often a waiting call reads its stored answer itself, besides being woken on the answer's channel.
_POLL_S = 1.0
#: How long registering a call waits for Redis to confirm the subscription to the answer's channel.
_SUBSCRIBE_WAIT_S = 2.0


@dataclass(frozen=True, slots=True)
class Answer:
    """The user's answer to a call that waited."""

    approved: bool
    #: What the approval covers; ``None`` from a client that does not say.
    scope: str | None = None
    #: The call's arguments' SHA-256 as the answering client hashed them (sdk/51 8.10), for the decision
    #: record. Not part of what the answer says: the same answer sent again with another is still the same.
    arguments_sha256: str | None = field(default=None, compare=False)


class Answered(StrEnum):
    """What became of an answer."""

    #: Taken; also for the same answer sent again, while :data:`ANSWER_KEPT_S` keeps the first.
    TAKEN = "taken"
    #: No call of this user's waits for it (it never did, or its wait ended without an answer).
    UNKNOWN = "unknown"
    #: The call takes ``once`` only, and the approval named another scope.
    ONCE_ONLY = "once_only"
    #: The call takes ``once`` only, the approval named no scope, and the gateway is set to refuse that.
    SCOPE_REQUIRED = "scope_required"
    #: The call was answered already, differently: the first answer stands.
    ALREADY_ANSWERED = "already_answered"


def refusal(*, once_only: bool, approved: bool, scope: str | None, require_scope: bool) -> Answered | None:
    """Why the gateway refuses an answer, or ``None`` when it takes it.

    A destructive or unclassified call, or one a rule asks about (*once_only*), is approved for itself
    alone: an approval that stands for the chat or always on a device must not answer it (38 §6; 39 F1,
    F6). The gateway holds to this whatever the client does (ASVS 5.0 V8.3.1). A "no" is always taken.
    """
    if not approved or not once_only:
        return None
    if scope is None:
        return Answered.SCOPE_REQUIRED if require_scope else None
    return None if scope == "once" else Answered.ONCE_ONLY


@dataclass(frozen=True, slots=True)
class _Taken:
    """An answer taken for a call: whose call it was, and what was answered."""

    tenant_id: str
    user_id: str
    answer: Answer
    #: Until when it is kept here (``time.monotonic``); Redis keeps its own copy for :data:`ANSWER_KEPT_S`.
    until: float = 0.0


#: Closing a wait runs to its end even when the request is cancelled meanwhile (the client went away).
_closing: set[asyncio.Future[None]] = set()


def _owner_key(approval_id: str) -> str:
    """Whose call waits for *approval_id*, and whether it takes ``once`` only; set while it waits."""
    return f"faden:approval:{approval_id}"


def _answer_key(approval_id: str) -> str:
    """The first answer taken for *approval_id*, or the mark that its wait ended without one."""
    return f"faden:approval:{approval_id}:answer"


def _channel(approval_id: str) -> str:
    """Where the waiting worker is woken when an answer is taken on another."""
    return f"faden:approval:{approval_id}"


def _record(stored: Any) -> _Taken | None:
    """A stored answer record read back; ``None`` for none, for the mark of a wait that ended, or garbage."""
    if not stored:
        return None
    try:
        said = json.loads(stored.decode() if isinstance(stored, bytes) else stored)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(said, dict) or said.get("ended") or not isinstance(said.get("approved"), bool):
        return None
    scope = said.get("scope")
    digest = said.get("arguments_sha256")
    return _Taken(
        tenant_id=str(said.get("tenant") or ""), user_id=str(said.get("user") or ""),
        answer=Answer(
            approved=said["approved"], scope=scope if isinstance(scope, str) else None,
            arguments_sha256=digest if isinstance(digest, str) else None,
        ),
    )


def _repeat(taken: _Taken | None, tenant_id: str, user_id: str, said: Answer) -> Answered:
    """An answer to a call that was answered already, or no longer waits: the first answer stands.

    The same answer again is taken, as the first was (a client's retry); another one is refused. Another
    person's answer is told nothing about the call (ASVS 5.0 V8.3.1).
    """
    if taken is None or (taken.tenant_id, taken.user_id) != (tenant_id, user_id):
        return Answered.UNKNOWN
    return Answered.TAKEN if taken.answer == said else Answered.ALREADY_ANSWERED


class PendingApproval:
    """One call waiting for the person, from before it is announced to the end of its wait.

    Made by :meth:`Approvals.open`. The caller announces the call only then, so an answer that comes at once
    -- on this worker or another -- finds it. :meth:`answer` waits a while for the person's answer, so the
    caller can send :data:`KEEPALIVE` in between; :meth:`close` ends the wait and says what was answered.
    """

    def __init__(
        self, approvals: Approvals, *, approval_id: str, tenant_id: str, user_id: str, once_only: bool,
        timeout: float,
    ) -> None:
        loop = asyncio.get_running_loop()
        self.approval_id = approval_id
        self.tenant_id = tenant_id
        self.user_id = user_id
        #: Takes an approval for this call alone (:func:`refusal`).
        self.once_only = once_only
        self.future: asyncio.Future[Answer] = loop.create_future()
        self._deadline = loop.time() + timeout
        self._ran_out = False
        self._approvals = approvals
        #: Its channel's subscription and the task that listens on it (with Redis).
        self.pubsub: Any = None
        self.listener: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def expired(self) -> bool:
        """Whether the call's time to wait is over (also when the loop's timer ran out a tick early)."""
        return self._ran_out or asyncio.get_running_loop().time() >= self._deadline

    @property
    def answered(self) -> Answer | None:
        """The answer taken for the call, if one was."""
        return self.future.result() if self.future.done() and not self.future.cancelled() else None

    async def answer(self, within: float) -> Answer | None:
        """The person's answer, waiting at most *within* seconds, and never past the call's time."""
        remaining = self._deadline - asyncio.get_running_loop().time()
        left = min(within, remaining)
        if self.future.done() or left <= 0:
            self._ran_out = self._ran_out or not self.future.done()
            return self.answered
        try:
            return await asyncio.wait_for(asyncio.shield(self.future), left)
        except TimeoutError:
            # Waited to the call's end: over, even if the timer fired a clock tick before it.
            self._ran_out = self._ran_out or left >= remaining
            return None

    async def close(self) -> Answer | None:
        """End the wait: no answer is taken for the call from now on. The answer taken, or ``None``.

        Ending is settled as an answer is taken -- whichever comes first counts -- so an answer that came at
        the last moment still counts, and none is taken for a call that no longer waits. It runs to its end
        even when the request is cancelled meanwhile.
        """
        if not self._closed:
            self._closed = True
            ending = asyncio.ensure_future(self._approvals.finish(self))
            _closing.add(ending)
            ending.add_done_callback(_closing.discard)
            await asyncio.shield(ending)
        return self.answered


class Approvals:
    """Calls waiting for a person, answered on any worker.

    A call is registered (:meth:`open`) before it is announced, and waits there; ``POST /v1/approvals/{id}``
    answers it. With Redis, the answer may arrive on another worker: the first answer is stored once
    (``SET NX``) under the approval, and the waiting worker is woken on the approval's channel. It also
    reads the stored answer every second, so an answer whose wake-up it missed is still found. The
    approval's key says whose call it is and whether it takes ``once`` only, so the answering worker checks
    both. The first answer stands; the same answer sent again is taken as the first was, so a client may
    send it again when the response to it was lost (ASVS 5.0 V2.3.3, V2.3.4: one decision, recorded once).
    """

    def __init__(self) -> None:
        self._waiting: dict[str, PendingApproval] = {}
        #: Answers taken on this worker without Redis, kept for a repeat (:data:`ANSWER_KEPT_S`).
        self._taken: dict[str, _Taken] = {}
        self._redis: Any = None

    def use(self, redis: Any | None) -> None:
        self._redis = redis

    async def open(
        self, *, tenant_id: str, user_id: str, approval_id: str, timeout: float, once_only: bool = False,
    ) -> PendingApproval:
        """Register a call that waits for its user's answer, before the call is announced to them.

        With Redis the call's key is set and the worker subscribed to its channel, confirmed by Redis, before
        this returns: an answer that comes as soon as the person sees the call is taken and heard.
        """
        pending = PendingApproval(
            self, approval_id=approval_id, tenant_id=tenant_id, user_id=user_id, once_only=once_only,
            timeout=timeout,
        )
        self._waiting[approval_id] = pending
        if self._redis is None:
            return pending
        try:
            await self._redis.set(
                _owner_key(approval_id),
                json.dumps({"tenant": tenant_id, "user": user_id, "once_only": once_only}),
                ex=int(timeout) + 60,
            )
            pending.pubsub = self._redis.pubsub()
            await pending.pubsub.subscribe(_channel(approval_id))
            subscribed = asyncio.Event()
            pending.listener = asyncio.create_task(self._listen(pending, subscribed))
            try:
                await asyncio.wait_for(subscribed.wait(), _SUBSCRIBE_WAIT_S)
            except TimeoutError:
                logger.warning("Redis did not confirm an approval's channel in time; its answer is polled")
        except BaseException:
            await pending.close()
            raise
        return pending

    async def wait(
        self, *, tenant_id: str, user_id: str, approval_id: str, timeout: float, once_only: bool = False,
    ) -> Answer | None:
        """The user's answer; ``None`` when nobody answered in time (:meth:`open`, then the whole wait)."""
        pending = await self.open(
            tenant_id=tenant_id, user_id=user_id, approval_id=approval_id, timeout=timeout,
            once_only=once_only,
        )
        try:
            await pending.answer(timeout)
        finally:
            said = await pending.close()
        return said

    async def _listen(self, pending: PendingApproval, subscribed: asyncio.Event) -> None:
        """Give *pending* its answer: woken on its channel, or reading the stored answer every second."""
        complained = False
        while not pending.future.done():
            try:
                message = await pending.pubsub.get_message(timeout=_POLL_S)
                if message and message.get("type") == "subscribe":
                    subscribed.set()
                taken = _record(await self._redis.get(_answer_key(pending.approval_id)))
            except Exception:  # Redis away for a moment: the next round tries again
                if not complained:
                    complained = True
                    logger.warning("An approval's wait could not read Redis; it tries again", exc_info=True)
                await asyncio.sleep(_POLL_S)
                continue
            if taken is not None and not pending.future.done():
                pending.future.set_result(taken.answer)

    async def finish(self, pending: PendingApproval) -> None:
        """End *pending* (:meth:`PendingApproval.close`): ended, unless an answer was stored first."""
        approval_id = pending.approval_id
        try:
            if self._redis is not None and not pending.future.done():
                with contextlib.suppress(Exception):
                    ended = await self._redis.set(
                        _answer_key(approval_id), json.dumps({"ended": True}), nx=True, ex=ANSWER_KEPT_S,
                    )
                    taken = None if ended else _record(await self._redis.get(_answer_key(approval_id)))
                    if taken is not None and not pending.future.done():
                        pending.future.set_result(taken.answer)
        finally:
            if self._waiting.get(approval_id) is pending:
                del self._waiting[approval_id]
            if pending.listener is not None:
                pending.listener.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await pending.listener
            if pending.pubsub is not None:
                with contextlib.suppress(Exception):
                    await pending.pubsub.unsubscribe()
                    await pending.pubsub.aclose()
            if self._redis is not None:
                with contextlib.suppress(Exception):
                    await self._redis.delete(_owner_key(approval_id))

    def waiting(self) -> list[str]:
        """The approvals this worker waits for, and has not had an answer for yet."""
        return [approval_id for approval_id, pending in self._waiting.items() if not pending.future.done()]

    async def answer(
        self, *, tenant_id: str, user_id: str, approval_id: str, approved: bool, scope: str | None = None,
        require_scope: bool = False, arguments_sha256: str | None = None,
    ) -> Answered:
        """Answer a call of this user's. A refused answer leaves the call waiting for another.

        The first answer taken stands. The same answer sent again is taken as the first was, also once the
        call no longer waits (for :data:`ANSWER_KEPT_S`); another answer is :attr:`Answered.ALREADY_ANSWERED`.
        *arguments_sha256* goes with the answer into the decision record; a repeat's is not taken.
        """
        said = Answer(approved=approved, scope=scope, arguments_sha256=arguments_sha256)
        if self._redis is None:
            return self._answer_here(tenant_id, user_id, approval_id, said, require_scope=require_scope)
        local = self._waiting.get(approval_id)
        owner = await self._owner(approval_id, local)
        if owner is None:
            return _repeat(_record(await self._redis.get(_answer_key(approval_id))), tenant_id, user_id, said)
        if owner[:2] != (tenant_id, user_id):
            return Answered.UNKNOWN
        refused = refusal(once_only=owner[2], approved=approved, scope=scope, require_scope=require_scope)
        if refused is not None:
            return refused
        record = json.dumps({
            "tenant": tenant_id, "user": user_id, "approved": approved, "scope": scope,
            "arguments_sha256": arguments_sha256,
        })
        if not await self._redis.set(_answer_key(approval_id), record, nx=True, ex=ANSWER_KEPT_S):
            # Answered already, or the wait ended first: the stored record says which.
            return _repeat(_record(await self._redis.get(_answer_key(approval_id))), tenant_id, user_id, said)
        if local is not None and not local.future.done():
            local.future.set_result(said)
        else:
            # A wake-up: the waiting worker reads the stored answer (it would find it within a second anyway).
            woken = json.dumps({"approved": approved, "scope": scope})
            await self._redis.publish(_channel(approval_id), woken)
        return Answered.TAKEN

    def _answer_here(
        self, tenant_id: str, user_id: str, approval_id: str, said: Answer, *, require_scope: bool,
    ) -> Answered:
        """:meth:`answer` without Redis: one worker, so nothing awaits between the check and the answer."""
        now = time.monotonic()
        for kept in [k for k, taken in self._taken.items() if taken.until <= now]:
            del self._taken[kept]
        pending = self._waiting.get(approval_id)
        if pending is None or pending.future.done():
            return _repeat(self._taken.get(approval_id), tenant_id, user_id, said)
        if (pending.tenant_id, pending.user_id) != (tenant_id, user_id):
            return Answered.UNKNOWN
        refused = refusal(once_only=pending.once_only, approved=said.approved, scope=said.scope,
                          require_scope=require_scope)
        if refused is not None:
            return refused
        pending.future.set_result(said)
        self._taken[approval_id] = _Taken(tenant_id, user_id, said, until=now + ANSWER_KEPT_S)
        return Answered.TAKEN

    async def _owner(self, approval_id: str, local: PendingApproval | None) -> tuple[str, str, bool] | None:
        """Whose call waits for *approval_id* -- tenant, user -- and whether it takes ``once`` only."""
        if local is not None:
            return local.tenant_id, local.user_id, local.once_only
        stored = await self._redis.get(_owner_key(approval_id))
        if not stored:
            return None
        who = json.loads(stored.decode() if isinstance(stored, bytes) else stored)
        return who.get("tenant"), who.get("user"), bool(who.get("once_only"))


approvals = Approvals()
