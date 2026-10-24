"""Where Enterprise extensions plug into the backend.

Core is complete on its own: every extension point here has a working
default, and core never imports Enterprise code. When ``faden_ee`` is
installed, its backend plugin gets this registry once at startup
(``register(registry)``) and adds to it.

Extension points for agents:

- ``access_rules``: extra conditions on who may use an agent, on top of the
  grants (EE: user exclusions). Each may only deny.
- ``token_lifetimes``: how long agent tokens live (core: tenant-wide
  settings; EE: per agent, under a tenant ceiling).
- ``version_extensions``: validators for the keys of
  ``AgentVersion.extensions``. A key nobody registered is refused, so no
  Enterprise setting is stored where nothing enforces it.
- ``member_roles``: roles beside ``maintainer`` (EE: ``auditor``).

Extension points for signing in:

- ``password_sign_in_checks``: extra conditions on signing in with a password, for anyone but a
  superuser (EE: addresses whose organisation signs in through its provider only). Each may only deny.
- ``link_confirmation``: what happens when an outside sign-in meets an existing account it is not linked
  to yet (core: refused, an administrator links them; EE: the account's owner confirms by e-mail).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from faden_backend.settings import settings

log = logging.getLogger(__name__)

@dataclass(frozen=True)
class Lifetimes:
    """Seconds an agent's tokens live."""

    access_s: int
    refresh_s: int
    idle_s: int


class TokenLifetimePolicy(Protocol):
    """How long the tokens of one agent live."""

    def lifetimes(self, extensions: dict[str, Any]) -> Lifetimes:
        """The lifetimes of an agent's tokens, from its version's extensions."""
        ...


class TenantLifetimes:
    """Core: the tenant-wide settings, the same for every agent."""

    def lifetimes(self, extensions: dict[str, Any]) -> Lifetimes:
        """The tenant-wide settings, whatever the agent's extensions say."""
        del extensions
        return Lifetimes(
            access_s=settings.agent_access_token_ttl_s,
            refresh_s=settings.agent_refresh_token_ttl_s,
            idle_s=settings.agent_refresh_idle_s,
        )


#: ``(user_id, agent_id) -> reason`` to deny, or ``None`` to allow.
AccessRule = Callable[[uuid.UUID, uuid.UUID], Awaitable[str | None]]
#: Validates one ``extensions`` key; raises ``ValueError`` when invalid.
VersionExtension = Callable[[Any], None]
#: ``(email) -> reason`` to refuse a password sign-in, or ``None`` to allow it.
PasswordSignInCheck = Callable[[str], Awaitable[str | None]]
#: ``(session, user, identity) -> Refusal``: an outside sign-in met *user*'s account, not linked to it yet.
LinkConfirmation = Callable[[Any, Any, Any], Awaitable[Any]]


@dataclass
class ExtensionRegistry:
    """What Enterprise adds; defaults are core's behaviour."""

    access_rules: list[AccessRule] = field(default_factory=list)
    token_lifetimes: TokenLifetimePolicy = field(default_factory=TenantLifetimes)
    version_extensions: dict[str, VersionExtension] = field(default_factory=dict)
    member_roles: set[str] = field(default_factory=lambda: {"maintainer"})
    password_sign_in_checks: list[PasswordSignInCheck] = field(default_factory=list)
    link_confirmation: LinkConfirmation | None = None


registry = ExtensionRegistry()
_loaded: set[int] = set()


def load_plugin(plugin: object | None) -> None:
    """Let the Enterprise backend plugin register itself, if it can -- once."""
    register = getattr(plugin, "register", None)
    if register is None or id(plugin) in _loaded:
        return
    _loaded.add(id(plugin))
    try:
        register(registry)
    except Exception:
        log.exception("The Enterprise plugin failed to register its extensions")
