from sqlalchemy.orm import DeclarativeBase

from faden_gateway.db.meta import meta


class Base(DeclarativeBase):
    """Base for all models."""

    metadata = meta
