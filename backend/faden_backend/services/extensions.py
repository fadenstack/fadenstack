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
- ``member_roles``: roles beside ``maintainer`` (EE: ``auditor``). A member in another role sees the agent's
  details (versions, members, settings, activity) and changes nothing; using it still takes a grant. With the
  role no longer listed, the membership gives nothing.
- ``agent_restrictions``: the names of restrictions an installed extension enforces on agents beyond their
  grants (EE: ``exclusions``). An agent whose ``restricted_by`` names one not listed here is its maintainers'
  only: the restriction would go unenforced (core: none).
- ``tenant_restrictions``: the names of restrictions on a whole tenant an installed extension enforces (EE:
  ``tool_mode_ceilings``). One the tenant is marked with and nobody enforces fails closed as
  ``services/tenant_restrictions.py`` says for it (core: none).

Extension points for signing in:

- ``password_sign_in_checks``: extra conditions on signing in with a password, for anyone but a
  superuser (EE: addresses whose organisation signs in through its provider only). Each may only deny.

Extension points for mail:

- ``mail_sender``: delivers what the notification outbox holds (core: none, so core sends no mail and
  queues none; EE: its mailer). Whatever needs mail asks ``mail_available()`` first: password resets by
  mail, a code that links an outside sign-in to an account, administrators' alerts.

Extension points for the console:

- ``admin_notices``: what administrators should know now, shown above every admin page (core: none; EE:
  the licence running out). A source that fails is left out; the others still show.
- ``features``: what this server adds beyond core, by name (core: none). The console shows a feature's
  screens only while it is listed (``GET /api/about``).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

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
#: ``(email) -> reason`` to refuse a password sign-in, or ``None`` to allow it; *email* as typed, asked
#: whatever the password and whether or not an account has it.
PasswordSignInCheck = Callable[[str], Awaitable[str | None]]


class MailSender(Protocol):
    """Delivers the outbox's mail."""

    def available(self) -> bool:
        """Whether mail goes out now (asked before anything is queued, and by the sign-in page)."""
        ...

    async def send(self, kind: str, payload: dict[str, Any]) -> None:
        """Deliver one message of *kind* (``password_reset``, ``link_code``, ``admin_alert``), or raise."""
        ...

    def link_base(self) -> str:
        """The console's address that links in mail start with, as configured for mail.

        Never taken from a request's ``Host``: a forged one would send a reset link to someone else's site
        (OWASP Forgot Password Cheat Sheet, password reset poisoning).
        """
        ...

    def admin_recipients(self) -> list[str]:
        """Who gets administrators' alerts (``admin_alert``); none, and they are not queued."""
        ...


@dataclass(frozen=True)
class AdminNotice:
    """Something administrators should know now. The console words it: ``notices.<code>``, with *params*."""

    code: str
    severity: Literal["info", "warning", "error"]
    params: dict[str, str | int] = field(default_factory=dict)


#: ``(session) -> notices``: what a source has to say now.
AdminNoticeSource = Callable[[Any], Awaitable[list[AdminNotice]]]


@dataclass
class ExtensionRegistry:
    """What Enterprise adds; defaults are core's behaviour."""

    access_rules: list[AccessRule] = field(default_factory=list)
    token_lifetimes: TokenLifetimePolicy = field(default_factory=TenantLifetimes)
    version_extensions: dict[str, VersionExtension] = field(default_factory=dict)
    member_roles: set[str] = field(default_factory=lambda: {"maintainer"})
    agent_restrictions: set[str] = field(default_factory=set)
    tenant_restrictions: set[str] = field(default_factory=set)
    password_sign_in_checks: list[PasswordSignInCheck] = field(default_factory=list)
    mail_sender: MailSender | None = None
    admin_notices: list[AdminNoticeSource] = field(default_factory=list)
    features: set[str] = field(default_factory=set)


registry = ExtensionRegistry()
_loaded: set[int] = set()


def mail_available() -> bool:
    """Whether this server can send mail now: a sender is registered and says it can."""
    sender = registry.mail_sender
    if sender is None:
        return False
    try:
        return bool(sender.available())
    except Exception:
        log.exception("The mail sender could not say whether it is available; taking it as not")
        return False


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
