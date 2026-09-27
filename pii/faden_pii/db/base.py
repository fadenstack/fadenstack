from sqlalchemy.orm import DeclarativeBase

from faden_pii.db.meta import meta


class Base(DeclarativeBase):
    """Base for all models."""

    metadata = meta
