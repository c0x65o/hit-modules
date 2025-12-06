"""Automatic event publishing for SQLAlchemy models.

This module provides zero-configuration event publishing for database changes.
No decorators or PostgreSQL triggers needed - just configure in hit.yaml.

Usage in hit.yaml:
    services:
      - name: api
        events:
          publish:
            - "*"           # All models
            # OR specific:
            - users         # users.created, users.updated, users.deleted
            - orders        # orders.created, orders.updated, orders.deleted

Usage in code:
    from hit_modules.auto_events import install_auto_events
    from app.database import engine

    # ONE LINE - publishes events for all models
    install_auto_events(engine)

    # Or specific models
    install_auto_events(engine, models=["users", "orders"])

    # Or with custom event type prefix
    install_auto_events(engine, models="*", event_prefix="db")
    # Publishes: db.users.created, db.orders.updated, etc.

How it works:
1. SQLAlchemy after_flush event captures all pending changes
2. After successful commit, events are published to the Events Module
3. Events are isolated per project via HIT_PROJECT_SLUG

Event payload format:
    {
        "id": "<primary_key>",
        "action": "created" | "updated" | "deleted",
        "data": { ...all_model_fields... },
        "old_data": { ...previous_values... },  # Only for updates
        "timestamp": "2024-01-01T00:00:00Z"
    }
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable
from weakref import WeakSet

from sqlalchemy import event, inspect
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, Mapper

from .events import publish_event
from .logger import get_logger

logger = get_logger(__name__)

# Track which engines have auto-events installed
_installed_engines: WeakSet[Engine] = WeakSet()

# Pending events queue (cleared after commit)
_pending_events: dict[int, list[dict[str, Any]]] = {}


@dataclass
class AutoEventsConfig:
    """Configuration for auto event publishing."""
    
    models: list[str] | str  # List of table names or "*" for all
    event_prefix: str | None = None  # Optional prefix (e.g., "db" -> "db.users.created")
    project_slug: str | None = None  # Override project slug
    include_old_data: bool = True  # Include old values for updates
    exclude_fields: list[str] = field(default_factory=lambda: ["password", "password_hash", "secret"])
    
    def should_publish(self, table_name: str) -> bool:
        """Check if events should be published for this table."""
        if self.models == "*":
            return True
        if isinstance(self.models, list):
            return table_name in self.models
        return False
    
    def get_event_type(self, table_name: str, action: str) -> str:
        """Get the event type for a table and action."""
        if self.event_prefix:
            return f"{self.event_prefix}.{table_name}.{action}"
        return f"{table_name}.{action}"


def _get_model_dict(obj: Any, exclude_fields: list[str]) -> dict[str, Any]:
    """Convert SQLAlchemy model to dictionary, excluding sensitive fields."""
    mapper = inspect(obj.__class__)
    result = {}
    
    for column in mapper.columns:
        key = column.key
        if key not in exclude_fields:
            value = getattr(obj, key, None)
            # Handle datetime serialization
            if isinstance(value, datetime):
                value = value.isoformat()
            result[key] = value
    
    return result


def _get_primary_key(obj: Any) -> Any:
    """Get the primary key value(s) for a model instance."""
    mapper = inspect(obj.__class__)
    pk_columns = mapper.primary_key
    
    if len(pk_columns) == 1:
        return getattr(obj, pk_columns[0].key, None)
    
    # Composite primary key
    return {col.key: getattr(obj, col.key, None) for col in pk_columns}


def _queue_event(
    session: Session,
    obj: Any,
    action: str,
    config: AutoEventsConfig,
    old_data: dict[str, Any] | None = None,
) -> None:
    """Queue an event for publishing after commit."""
    session_id = id(session)
    
    if session_id not in _pending_events:
        _pending_events[session_id] = []
    
    table_name = obj.__tablename__
    
    event_data = {
        "table_name": table_name,
        "event_type": config.get_event_type(table_name, action),
        "payload": {
            "id": _get_primary_key(obj),
            "action": action,
            "data": _get_model_dict(obj, config.exclude_fields),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "project_slug": config.project_slug,
    }
    
    if old_data and action == "updated":
        event_data["payload"]["old_data"] = old_data
    
    _pending_events[session_id].append(event_data)
    logger.debug(f"Queued event: {event_data['event_type']} for {table_name}")


async def _publish_queued_events(session_id: int) -> None:
    """Publish all queued events for a session."""
    events = _pending_events.pop(session_id, [])
    
    for event_data in events:
        try:
            await publish_event(
                event_data["event_type"],
                event_data["payload"],
                project_slug=event_data["project_slug"],
            )
            logger.debug(f"Published event: {event_data['event_type']}")
        except Exception as e:
            logger.error(f"Failed to publish event {event_data['event_type']}: {e}")


def _run_async_publish(session_id: int) -> None:
    """Run async publish in the current event loop or create one."""
    try:
        loop = asyncio.get_running_loop()
        # We're in an async context, schedule the task
        loop.create_task(_publish_queued_events(session_id))
    except RuntimeError:
        # No running loop, create one for this publish
        asyncio.run(_publish_queued_events(session_id))


def install_auto_events(
    engine: Engine,
    *,
    models: list[str] | str = "*",
    event_prefix: str | None = None,
    project_slug: str | None = None,
    include_old_data: bool = True,
    exclude_fields: list[str] | None = None,
) -> None:
    """Install automatic event publishing for SQLAlchemy models.
    
    Events are published after successful commits to the Events Module.
    
    Args:
        engine: SQLAlchemy engine to monitor
        models: List of table names or "*" for all tables
        event_prefix: Optional prefix for event types (e.g., "db")
        project_slug: Override project slug (defaults to HIT_PROJECT_SLUG)
        include_old_data: Include old values in update events
        exclude_fields: Fields to exclude from event payloads
    
    Example:
        # Publish events for all models
        install_auto_events(engine)
        
        # Publish events for specific models
        install_auto_events(engine, models=["users", "orders"])
        
        # With custom prefix
        install_auto_events(engine, event_prefix="db")
        # Events: db.users.created, db.orders.updated, etc.
    """
    if engine in _installed_engines:
        logger.debug("Auto-events already installed for this engine")
        return
    
    # Get default exclude fields
    default_exclude = ["password", "password_hash", "secret", "token", "api_key"]
    final_exclude = exclude_fields if exclude_fields is not None else default_exclude
    
    config = AutoEventsConfig(
        models=models,
        event_prefix=event_prefix,
        project_slug=project_slug or os.getenv("HIT_PROJECT_SLUG"),
        include_old_data=include_old_data,
        exclude_fields=final_exclude,
    )
    
    # Track old values for updates
    old_values: dict[int, dict[str, Any]] = {}
    
    @event.listens_for(Session, "before_flush")
    def before_flush(session: Session, flush_context: Any, instances: Any) -> None:
        """Capture old values before flush for update events."""
        if not config.include_old_data:
            return
        
        for obj in session.dirty:
            if not hasattr(obj, "__tablename__"):
                continue
            if not config.should_publish(obj.__tablename__):
                continue
            
            # Get the state before changes
            state = inspect(obj)
            old_data = {}
            
            for attr in state.attrs:
                hist = attr.load_history()
                if hist.has_changes():
                    # Get the old value
                    if hist.deleted:
                        old_data[attr.key] = hist.deleted[0]
                    elif hist.unchanged:
                        old_data[attr.key] = hist.unchanged[0]
            
            if old_data:
                old_values[id(obj)] = old_data
    
    @event.listens_for(Session, "after_flush")
    def after_flush(session: Session, flush_context: Any) -> None:
        """Queue events for all changes after successful flush."""
        # New objects (INSERT)
        for obj in session.new:
            if not hasattr(obj, "__tablename__"):
                continue
            if config.should_publish(obj.__tablename__):
                _queue_event(session, obj, "created", config)
        
        # Modified objects (UPDATE)
        for obj in session.dirty:
            if not hasattr(obj, "__tablename__"):
                continue
            if config.should_publish(obj.__tablename__):
                old_data = old_values.pop(id(obj), None)
                _queue_event(session, obj, "updated", config, old_data)
        
        # Deleted objects (DELETE)
        for obj in session.deleted:
            if not hasattr(obj, "__tablename__"):
                continue
            if config.should_publish(obj.__tablename__):
                _queue_event(session, obj, "deleted", config)
    
    @event.listens_for(Session, "after_commit")
    def after_commit(session: Session) -> None:
        """Publish all queued events after successful commit."""
        session_id = id(session)
        if session_id in _pending_events:
            _run_async_publish(session_id)
    
    @event.listens_for(Session, "after_rollback")
    def after_rollback(session: Session) -> None:
        """Clear pending events on rollback."""
        session_id = id(session)
        if session_id in _pending_events:
            count = len(_pending_events[session_id])
            del _pending_events[session_id]
            logger.debug(f"Discarded {count} pending events due to rollback")
    
    _installed_engines.add(engine)
    
    model_desc = models if models != "*" else "all models"
    logger.info(f"Auto-events installed for {model_desc}")


def install_auto_events_from_config(
    engine: Engine,
    events_config: dict[str, Any] | None = None,
) -> None:
    """Install auto-events from hit.yaml configuration.
    
    Reads the events.publish configuration and installs appropriate listeners.
    
    Args:
        engine: SQLAlchemy engine to monitor
        events_config: Events configuration from hit.yaml (optional, reads from env if not provided)
    
    Config format (from hit.yaml):
        events:
          publish:
            - "*"                    # All tables
            # OR
            - users                  # Specific tables
            - orders
            # OR detailed config
            - model: users
              on: [created, updated]  # Only these actions
    
    Example:
        # With config dict
        install_auto_events_from_config(engine, {"publish": ["*"]})
        
        # Let it read from HIT_EVENTS_PUBLISH env var
        # HIT_EVENTS_PUBLISH="*" or HIT_EVENTS_PUBLISH="users,orders"
        install_auto_events_from_config(engine)
    """
    # Get configuration
    if events_config is None:
        # Try to read from environment
        publish_env = os.getenv("HIT_EVENTS_PUBLISH", "")
        if publish_env:
            if publish_env == "*":
                models: list[str] | str = "*"
            else:
                models = [m.strip() for m in publish_env.split(",") if m.strip()]
        else:
            logger.debug("No events.publish configuration found, skipping auto-events")
            return
    else:
        publish_config = events_config.get("publish", [])
        
        if not publish_config:
            logger.debug("events.publish is empty, skipping auto-events")
            return
        
        # Handle different config formats
        if publish_config == ["*"] or publish_config == "*":
            models = "*"
        elif isinstance(publish_config, list):
            # Could be list of strings or list of dicts
            models = []
            for item in publish_config:
                if isinstance(item, str):
                    if item == "*":
                        models = "*"
                        break
                    models.append(item)
                elif isinstance(item, dict):
                    # Detailed config: {"model": "users", "on": ["created"]}
                    if "model" in item:
                        models.append(item["model"])
        else:
            logger.warning(f"Invalid events.publish config: {publish_config}")
            return
    
    install_auto_events(engine, models=models)


# Convenience function for common use case
def auto_publish_all(engine: Engine) -> None:
    """Shorthand to publish events for all database models.
    
    Equivalent to: install_auto_events(engine, models="*")
    
    Example:
        from hit_modules.auto_events import auto_publish_all
        
        auto_publish_all(engine)  # That's it!
    """
    install_auto_events(engine, models="*")

