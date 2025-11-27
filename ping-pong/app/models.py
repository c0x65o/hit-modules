"""Database models for ping-pong service."""

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class Counter(Base):
    """Counter model for ping-pong test service."""

    __tablename__ = "counters"

    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    def __repr__(self) -> str:
        return f"<Counter(id={self.id}, value={self.value})>"

