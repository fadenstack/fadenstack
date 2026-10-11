"""API tokens: the keys people make for their own apps and scripts on the gateway's ``/v1``.

The token is a JWT the gateway verifies with the shared secret; this row is what makes it revocable.
The row's id is the token's ``jti``, so the gateway can look a token up without the token or a hash of
it being stored: an id is no use to anyone without the secret that signs tokens.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from faden_backend.db.base import Base

#: The ``typ`` claim of an API token. The gateway checks such a token against its row.
API_TOKEN_TYPE = "api"  # noqa: S105 - a claim value, not a secret

#: The tenant every account is in, and so every token made for one: console sign-ins carry no other.
#: The server sets it; a request never chooses it. The gateway takes a token's tenant from its row.
ACCOUNT_TENANT = "default"


class ApiToken(Base):
    """One API token. Revoking sets ``revoked_at``; the row stays, for the owner's list."""

    __tablename__ = "api_tokens"

    #: The token's ``jti``.
    id: Mapped[uuid.UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("user.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(String(128), nullable=False, default=ACCOUNT_TENANT)
    #: What the owner called it, to tell their tokens apart.
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(),
    )
    #: ``None``: the token does not expire.
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
