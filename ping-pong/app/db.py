"""Database connection for ping-pong service."""

import os
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker


class Base(DeclarativeBase):
    """Base class for all models."""

    pass


# Database URL from environment
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://localhost/ping_pong")

# Create engine and session factory
engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency for database sessions.
    
    Yields:
        Database session with automatic commit/rollback
    """
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

