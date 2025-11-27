"""Shared database utilities for Hit modules.

Provides common database connection patterns for multi-tenant services.
"""

import os
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


def get_database_url() -> str:
    """Get database URL from environment.
    
    Returns:
        Database connection string
    
    Raises:
        ValueError: If DATABASE_URL not set
    """
    url = os.getenv("DATABASE_URL")
    if not url:
        raise ValueError("DATABASE_URL environment variable not set")
    return url


def create_session_factory(database_url: str | None = None) -> sessionmaker:
    """Create a SQLAlchemy session factory.
    
    Args:
        database_url: Optional database URL (defaults to env var)
    
    Returns:
        Configured session factory
    """
    if database_url is None:
        database_url = get_database_url()
    
    engine = create_engine(database_url, pool_pre_ping=True)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@contextmanager
def get_db_session(
    session_factory: sessionmaker | None = None,
) -> Generator[Session, None, None]:
    """Get a database session with automatic cleanup.
    
    Usage:
        with get_db_session() as session:
            session.query(...)
    
    Args:
        session_factory: Optional session factory (creates default if None)
    
    Yields:
        Database session
    """
    if session_factory is None:
        session_factory = create_session_factory()
    
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db_dependency(session_factory: sessionmaker):
    """FastAPI dependency for database sessions.
    
    Usage in FastAPI:
        SessionFactory = create_session_factory()
        get_db = get_db_dependency(SessionFactory)
        
        @app.get("/users")
        def list_users(db: Session = Depends(get_db)):
            return db.query(User).all()
    
    Args:
        session_factory: Session factory to use
    
    Returns:
        Dependency callable for FastAPI
    """

    def dependency() -> Generator[Session, None, None]:
        session = session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    return dependency

