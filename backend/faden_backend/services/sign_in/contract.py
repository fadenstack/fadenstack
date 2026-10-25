"""What the backend and a sign-in module say to each other.

A sign-in module speaks a protocol with an identity provider (OIDC, SAML, a directory) and answers the
backend in these shapes; the backend alone talks to the browser, keeps the accounts and issues sign-ins. The
module is reached at ``settings.auth_service_url`` with ``Authorization: Bearer <auth_service_token>``:

``GET  /api/v1/providers/public``
    ``[ProviderPublic]``: the providers people may sign in with.
``POST /api/v1/sign-in/start``  ``StartRequest`` -> ``SignInStart``
    Where to send the browser; ``transaction`` comes back with the provider's answer.
``POST /api/v1/sign-in/finish``  ``FinishRequest`` -> ``SignInAnswer``
    The provider's answer (the callback's query, or a posted form) turned into an identity.
``POST /api/v1/sign-in/password``  ``PasswordRequest`` -> ``SignInAnswer``
    A directory account's name and password, checked by the module.
``GET  /api/v1/providers/{key}/metadata``
    The service-provider metadata a provider is set up with (SAML), served as is.
``GET  /api/v1/upkeep``
    ``[ProviderUpkeep]``: what the hourly upkeep does for each provider's people.
``POST /api/v1/directory``  ``DirectoryRequest`` -> ``DirectoryAnswer``
    Which of a directory provider's people it still has, disabled or not, and their groups now.
``/api/v1/admin/...``
    Provider settings, forwarded unchanged for administrators.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field

#: A provider's key: what its addresses are made of (``/api/auth/sso/<key>/...``).
KEY_PATTERN = r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"


class ProviderKind(StrEnum):
    """How a person signs in with a provider."""

    REDIRECT = "redirect"  # the browser goes to the provider and comes back (OIDC, SAML)
    PASSWORD = "password"  # noqa: S105 -- name and password on Fadenstack's own page, checked by the module (LDAP)


class ProviderPublic(BaseModel):
    """A provider as the sign-in page shows it."""

    key: str = Field(pattern=KEY_PATTERN)
    name: str = Field(min_length=1, max_length=128)
    kind: ProviderKind
    #: The answer comes back as a cross-site POST (SAML): its binding cookie must be ``SameSite=None``.
    posts_back: bool = False


class StartRequest(BaseModel):
    """Begin a sign-in with *provider*; the answer is to come back to *return_url*."""

    provider: str
    return_url: str


class SignInStart(BaseModel):
    """Where to send the browser, and the token the answer will carry (``state``, ``RelayState``)."""

    redirect_url: str
    transaction: str = Field(min_length=16, max_length=512)


class FinishRequest(BaseModel):
    """The provider's answer for the sign-in begun as *transaction*."""

    provider: str
    transaction: str
    return_url: str
    params: dict[str, str]


class PasswordRequest(BaseModel):
    """A directory account's name and password."""

    provider: str
    username: str = Field(min_length=1, max_length=256)
    password: str = Field(min_length=1, max_length=1024)


class ExternalIdentity(BaseModel):
    """Who signed in, and what the provider's settings say to do with them.

    ``subject`` is the person's stable ID at the provider; it, not the e-mail, finds the account again.
    ``email_trusted`` says the module vouches for the address (verified by the provider, in an allowed
    domain); only a trusted address may make a new account or meet an existing one.
    """

    provider: str
    provider_name: str
    subject: str = Field(min_length=1, max_length=320)
    email: str | None = Field(default=None, max_length=320)
    email_trusted: bool = False
    display_name: str | None = None
    #: The groups the provider's rules give this person. With ``manage_groups``, the memberships this provider
    #: gave before and no longer gives are taken away; memberships an administrator set are never touched.
    group_ids: list[uuid.UUID] = Field(default_factory=list)
    manage_groups: bool = False
    #: For a new account: whether one may be made, and with which roles.
    create_account: bool = False
    default_role_ids: list[uuid.UUID] = Field(default_factory=list)
    #: How long this sign-in lasts at most; the shorter of this and the backend's own lifetime applies.
    session_lifetime_s: int | None = Field(default=None, ge=60)


class Refusal(StrEnum):
    """Why a sign-in did not happen; the sign-in page says it in the person's language."""

    ACCOUNT_DISABLED = "account_disabled"
    ACCOUNT_NOT_VERIFIED = "account_not_verified"
    REGISTRATION_DISABLED = "registration_disabled"
    EMAIL_MISSING = "email_missing"
    EMAIL_NOT_TRUSTED = "email_not_trusted"
    LINK_NEEDS_CONFIRMATION = "link_needs_confirmation"
    LINK_CONFIRMATION_SENT = "link_confirmation_sent"
    WRONG_CREDENTIALS = "wrong_credentials"
    NOT_ALLOWED = "not_allowed"
    PROVIDER_ERROR = "provider_error"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    SIGN_IN_EXPIRED = "sign_in_expired"
    NEEDS_HTTPS = "needs_https"
    LICENSE = "license"
    NOT_OFFERED = "not_offered"


class SignInAnswer(BaseModel):
    """The module's answer: an identity, or why not."""

    outcome: Literal["identity", "refused"]
    identity: ExternalIdentity | None = None
    refusal: Refusal | None = None
    detail: str = ""


class SSOOnly(BaseModel):
    """The e-mail domains whose people sign in with a provider only: the backend refuses their passwords."""

    domains: list[str] = []


class ProviderUpkeep(BaseModel):
    """What the backend's hourly upkeep does for a provider's people."""

    key: str
    #: Accounts linked to this provider and not used this long are suspended (CIS v8 5.3).
    inactive_days: int | None = None
    #: The module can say which of the provider's people its directory still has (``/api/v1/directory``).
    directory: bool = False


class DirectoryRequest(BaseModel):
    """Which of these people the provider's directory still has, and how."""

    provider: str
    subjects: list[str] = Field(max_length=500)


class DirectoryPerson(BaseModel):
    """One person as the directory has them now."""

    subject: str
    #: ``missing``: not found; ``unknown``: not judged, because so many were missing at once that the
    #: directory, not the people, is likely wrong.
    status: Literal["active", "disabled", "missing", "unknown"]
    #: For an active person: the groups the provider's rules give them now.
    group_ids: list[uuid.UUID] = Field(default_factory=list)


class DirectoryAnswer(BaseModel):
    """The directory's word on the people asked about."""

    people: list[DirectoryPerson]
    manage_groups: bool = False
