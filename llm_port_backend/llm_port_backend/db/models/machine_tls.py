"""The machine CA: the authority behind the TLS between the gateway and the models on the machines.

Separate from the install's own authority (the CLI's ``tls-ca/``, host-only),
which vouches for the server to browsers and agents. This one is trusted only
by the gateway and by each machine's model-TLS proxy, so the backend may hold
its key: whoever controls the backend already sees every prompt and commands
every machine. See ``services/machine_tls``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from llm_port_backend.db.base import Base


class MachineCA(Base):
    """One generation of the machine CA.

    The issuer is the newest row whose ``issuing_from`` has passed; every row
    not retired and not expired is trusted. A scheduled rotation adds the next
    one with ``issuing_from`` in the future, so every trust bundle holds it
    before anything it signed is presented.
    """

    __tablename__ = "machine_ca"

    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    cert_pem: Mapped[str] = mapped_column(Text, nullable=False)
    #: PKCS#8 PEM, Fernet-sealed with a key derived from the settings master key.
    key_sealed: Mapped[str] = mapped_column(Text, nullable=False)
    fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    issuing_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    #: Why it was made: ``first``, ``scheduled``, ``replaced`` (an administrator's rotation),
    #: ``unreadable`` (the previous key could not be opened with this install's master key).
    reason: Mapped[str] = mapped_column(String(32), nullable=False, default="first")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )


class MachineTlsClient(Base):
    """A client identity the machines' proxies accept: today only the gateway's."""

    __tablename__ = "machine_tls_client"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    cert_pem: Mapped[str] = mapped_column(Text, nullable=False)
    key_sealed: Mapped[str] = mapped_column(Text, nullable=False)
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ca_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("machine_ca.id", ondelete="CASCADE"), nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now(),
    )


class MachineCertificate(Base):
    """The last certificate issued to a machine: for the console, and to pace re-issues."""

    __tablename__ = "machine_certificate"

    node_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("infra_node.id", ondelete="CASCADE"), primary_key=True,
    )
    serial: Mapped[str] = mapped_column(String(64), nullable=False)
    names_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, default=list)
    not_after: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ca_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("machine_ca.id", ondelete="CASCADE"), nullable=False,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
