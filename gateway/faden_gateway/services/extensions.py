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
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger(__name__)

CORE_FEATURES = ("agents", "agent_tokens", "agent_events", "skills", "sessions", "streaming")

#: ``(user_id, agent_id) -> reason`` to deny, or ``None`` to allow.
AgentAccessRule = Callable[[str, str], Awaitable[str | None]]
#: ``extensions -> module names`` the SDK must have.
RequiredModules = Callable[[dict[str, Any]], list[str]]


@dataclass
class ExtensionRegistry:
    """What Enterprise adds; defaults are core's behaviour."""

    agent_access_rules: list[AgentAccessRule] = field(default_factory=list)
    agent_extensions: set[str] = field(default_factory=set)
    required_client_modules: list[RequiredModules] = field(default_factory=list)
    features: set[str] = field(default_factory=lambda: set(CORE_FEATURES))
    edition: str = "community"
    governance_checks: list[Any] = field(default_factory=list)
    tool_mode_ceilings: list[Any] = field(default_factory=list)

    def client_modules(self, extensions: dict[str, Any]) -> list[str]:
        """Every SDK module an agent with *extensions* needs, sorted."""
        needed: set[str] = set()
        for rule in self.required_client_modules:
            needed.update(rule(extensions))
        return sorted(needed)

    def unenforced(self, extensions: dict[str, Any]) -> list[str]:
        """Keys of *extensions* no installed extension enforces."""
        return sorted(key for key in extensions if key not in self.agent_extensions)


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
