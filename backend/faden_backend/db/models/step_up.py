"""The second sign-in before Bypass is switched on (sdk/41): a step-up, shaped as a device pairing (RFC 8628).

An app that wants Bypass for a chat starts a step-up with its agent token; the person opens the link in the
browser, signs in again there (``services/agents/step_up.py``) and approves; the app collects a short proof once,
which the gateway asks for when it makes the Bypass grant. Rows are deleted a day after they were made; the grant
keeps how and when the person signed in.
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from faden_backend.db.base import Base


class StepUpStatus(enum.StrEnum):
    """Where a step-up stands."""

    PENDING = "pending"  # waiting for the person to sign in again in the browser
    SIGNED_IN = "signed_in"  # signed in again; waiting for Approve or Deny on the page
    APPROVED = "approved"  # the app collects its proof at its next poll
    DENIED = "denied"
    EXPIRED = "expired"
    CONSUMED = "consumed"  # the proof was handed out, once


class BypassStepUp(Base):
    """One step-up: the device's code (as a hash), the code in the link (as an HMAC), and what came of it."""

    __tablename__ = "bypass_step_ups"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False)
    #: The person of the app's agent token: only they can approve it, signed in again as themselves.
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False, index=True,
    )
    agent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("agents.id", ondelete="CASCADE"), nullable=False,
    )
    #: The app's sign-in on its device (the agent token's ``did``) and its name there.
    device_id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False, index=True)
    device_label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    client_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The longest a Bypass may run that the page shows; the proof carries it and the gateway holds to it.
    longest_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: The app's code (256 bits), as its SHA-256; the link's code, as an HMAC under a key of its own.
    step_up_code_hash: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_code_hmac: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    #: The browser that opened the link: the SHA-256 of its cookie. Only it signs in and decides.
    browser_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default=StepUpStatus.PENDING)
    requester_ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    interval_s: Mapped[int] = mapped_column(Integer, nullable=False)
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The second sign-in: when this server checked it, how (``password``, ``oidc``, ``saml``, ``ldap``), with
    #: which provider, and when the provider says it authenticated the person.
    signed_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(64), nullable=True)
    idp_auth_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: The sign-in provider's transaction it began (its SHA-256), and when: a SAML answer posted back over plain
    #: HTTP in development brings no binding cookie, and is found by it.
    sso_tx_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    sso_asked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Wrong sign-ins on this step-up; at the limit it is denied.
    failed_sign_ins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class BypassStepUpFailure(Base):
    """A wrong or expired code, or a wrong sign-in, on the step-up page: counted per person and address."""

    __tablename__ = "bypass_step_up_failures"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(PGUUID(as_uuid=True), nullable=True, index=True)
    ip: Mapped[str | None] = mapped_column(INET, nullable=True)
    failed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
