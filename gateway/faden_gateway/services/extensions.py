"""Where Enterprise extensions plug into the gateway.

Core is complete on its own: every extension point has a working default,
and core never imports Enterprise code. When ``faden_ee`` is installed, its
gateway plugin gets this registry once (``register(registry)``) and adds to
it.

- ``agent_access_rules``: extra conditions on who may use an agent, on top of
  its grants (EE: user exclusions). Each may only deny.
- ``agent_extensions``: the keys of an agent version's ``extensions`` some
  installed extension enforces. An agent carrying any other key is usable by
  its maintainers only -- those settings restrict access or data, and nothing
  here would enforce them.
- ``agent_restrictions``: the names of restrictions an installed extension
  enforces on agents beyond their grants (EE: ``exclusions``). An agent whose
  ``restricted_by`` names one not listed here is usable by its maintainers only.
- ``tenant_restrictions``: the names of restrictions on a whole tenant an
  installed extension enforces (EE: ``tool_mode_ceilings``). A tenant marked
  with one not listed here fails closed (``services/gateway/tenant_restrictions.py``).
- ``required_client_modules``: which SDK modules an agent needs, from its
  ``extensions`` (EE: ``pii.local`` for device-only PII). The SDK refuses to
  run an agent it lacks a module for.
- ``features`` and ``edition``: what ``GET /v1/features`` reports.
- ``governance_checks``: checks on prompts, answers and tool calls
  (``services/gateway/governance.py``; EE: content policies). Core's own
  check, the tenant's tool rules, is added per request.
- ``tool_mode_ceilings``: the most a user may pick as a chat's tool mode
  (``services/gateway/tool_classes.py``; EE: per tenant, team, agent,
  user). Each is ``async (CeilingContext) -> ToolMode | None``; the narrowest
  applies, and one that fails counts as ``read_only``.
- ``admission_checks``: limits of their own on a request (EE: budgets), each
  ``async (AdmissionContext) -> Refusal | None``. They run after core's caps
  (``services/gateway/ratelimit.py``), side by side; the first refusal in list
  order is answered, and core's reservation for the request is given back. One
  that raises is logged and does not refuse: an extension that wants to fail
  closed returns a ``Refusal`` itself.
- ``usage_listeners``: told each request's usage (``UsageRecord``), in the
  background after its request-log row is written (EE: budget counters). One
  that raises is logged.

Both cost a request nothing while they are empty
(``services/gateway/usage_hooks.py``).
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)

CORE_FEATURES = ("agents", "agent_tokens", "agent_events", "skills", "sessions", "streaming")

#: ``(user_id, agent_id) -> reason`` to deny, or ``None`` to allow.
AgentAccessRule = Callable[[str, str], Awaitable[str | None]]
#: ``extensions -> module names`` the SDK must have.
RequiredModules = Callable[[dict[str, Any]], list[str]]


@dataclass(frozen=True)
class AdmissionContext:
    """A request about to be admitted, as an admission check sees it: who, for what, and how big."""

    tenant_id: str
    user_id: str
    #: The person's usage group (team), the chat's project and the agent the request runs under.
    group_id: str | None
    project_id: str | None
    agent_id: str | None
    model_alias: str
    endpoint: str
    request_id: str
    #: What core's caps reserved for it: the input estimate and, for a chat, its answer (``max_tokens``, else
    #: 1024). The real usage follows in the ``UsageRecord``.
    estimated_tokens: int
    #: The team's path at the time of use, root first, ending with ``group_id``; empty without a team.
    group_path: tuple[str, ...] = ()


@dataclass(frozen=True)
class Refusal:
    """An admission check's no, answered as OpenAI clients read a 429.

    ``error_type`` ``insufficient_quota`` for a budget or a quota, ``rate_limit_error`` for a rate. The
    message names the kind of limit and says whom to ask (M20); nothing about anyone else's usage.
    ``retry_after_sec`` becomes ``Retry-After``; above a minute ``x-should-retry: false`` goes with it, so
    the OpenAI SDKs do not retry in vain.
    """

    status: int = 429
    error_type: str = "insufficient_quota"
    code: str = "budget_exceeded"
    message: str = ""
    retry_after_sec: int | None = None
    headers: Mapping[str, str] = field(default_factory=dict)


#: ``AdmissionContext -> Refusal`` to refuse, or ``None`` to let the request in.
AdmissionCheck = Callable[[AdmissionContext], Awaitable[Refusal | None]]


@dataclass(frozen=True)
class UsageRecord:
    """One request's usage, as its request-log row has it, with what core's caps reserved for it.

    Counted as the OpenTelemetry GenAI conventions count: ``input_tokens`` includes ``cached_input_tokens``,
    ``output_tokens`` includes ``reasoning_tokens``. ``cost`` is the estimate in ``currency``, ``None``
    when no price applies. A refused or failed request comes with no tokens.
    """

    tenant_id: str
    user_id: str
    group_id: str | None
    project_id: str | None
    agent_id: str | None
    model_alias: str | None
    provider_instance_id: str | None
    request_id: str
    status_code: int
    input_tokens: int
    cached_input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cost: Decimal | None
    currency: str | None
    #: What admission reserved for the request (``AdmissionContext.estimated_tokens``); 0 when it was not
    #: admitted. A listener reconciles its own reservation with it.
    estimated_tokens: int
    created_at: datetime
    #: The team's path at the time of use, as the request log keeps it; empty without a team.
    group_path: tuple[str, ...] = ()


#: Told each request's usage; what it returns is not used.
UsageListener = Callable[[UsageRecord], Awaitable[None]]


@dataclass
class ExtensionRegistry:
    """What Enterprise adds; defaults are core's behaviour."""

    agent_access_rules: list[AgentAccessRule] = field(default_factory=list)
    agent_extensions: set[str] = field(default_factory=set)
    agent_restrictions: set[str] = field(default_factory=set)
    tenant_restrictions: set[str] = field(default_factory=set)
    required_client_modules: list[RequiredModules] = field(default_factory=list)
    features: set[str] = field(default_factory=lambda: set(CORE_FEATURES))
    edition: str = "community"
    governance_checks: list[Any] = field(default_factory=list)
    tool_mode_ceilings: list[Any] = field(default_factory=list)
    admission_checks: list[AdmissionCheck] = field(default_factory=list)
    usage_listeners: list[UsageListener] = field(default_factory=list)

    def client_modules(self, extensions: dict[str, Any]) -> list[str]:
        """Every SDK module an agent with *extensions* needs, sorted."""
        needed: set[str] = set()
        for rule in self.required_client_modules:
            needed.update(rule(extensions))
        return sorted(needed)

    def unenforced(self, extensions: dict[str, Any], restricted_by: tuple[str, ...] = ()) -> list[str]:
        """Keys of *extensions*, and restrictions in *restricted_by*, no installed extension enforces."""
        keys = sorted(key for key in extensions if key not in self.agent_extensions)
        return keys + sorted(name for name in restricted_by if name not in self.agent_restrictions)


registry = ExtensionRegistry()
_loaded: set[int] = set()


def load_plugin(plugin: object | None) -> None:
    """Let the Enterprise gateway plugin register itself, if it can -- once."""
    register = getattr(plugin, "register", None)
    if register is None or id(plugin) in _loaded:
        return
    _loaded.add(id(plugin))
    try:
        register(registry)
        # The plugin may also hand over its governance checks this way.
        checks = getattr(plugin, "governance_checks", None)
        if callable(checks):
            registry.governance_checks.extend(checks())
    except Exception:
        log.exception("The Enterprise plugin failed to register its extensions")
