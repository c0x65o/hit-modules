"""Shared model utilities for Hit modules.

Provides base models with common fields (timestamps, namespace, etc.).
"""

from datetime import datetime

from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


class TimestampMixin:
    """Mixin for created_at timestamp."""

    created_at: Mapped[datetime] = mapped_column(
        default=datetime.utcnow, nullable=False
    )


class NamespaceMixin:
    """Mixin for multi-tenancy via namespace field.
    
    Usage:
        class User(Base, NamespaceMixin):
            __tablename__ = "users"
            id: Mapped[int] = mapped_column(primary_key=True)
            email: Mapped[str]
    
    All queries should filter by namespace:
        users = session.query(User).filter(User.namespace == "myapp").all()
    """

    namespace: Mapped[str] = mapped_column(
        String(100), nullable=False, index=True
    )


class TimestampNamespaceMixin(TimestampMixin, NamespaceMixin):
    """Combined timestamp and namespace mixin."""

    pass

