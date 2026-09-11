"""
SQLAlchemy 2.0 Declarative Base and Core Mixins for Slate VFX.
Provides standard timestamps and optimistic concurrency control (OCC).
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import DateTime, Integer
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Root declarative base for all Slate VFX relational entities."""
    pass


class TimestampMixin:
    """Provides automated ISO timestamping for created_at and last_updated."""
    created_at: Mapped[datetime] = mapped_column(
        DateTime, 
        default=datetime.utcnow, 
        nullable=False
    )
    last_updated: Mapped[datetime] = mapped_column(
        DateTime, 
        default=datetime.utcnow, 
        onupdate=datetime.utcnow, 
        nullable=False
    )


class VersionMixin:
    """
    Optimistic Concurrency Control (OCC) mixin.
    Increments version on every update to detect concurrent overwrites.
    """
    version: Mapped[int] = mapped_column(
        Integer, 
        default=1, 
        nullable=False
    )
