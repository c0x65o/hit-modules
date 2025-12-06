"""Database event publishing via PostgreSQL NOTIFY/LISTEN.

This module provides automatic event publishing when database rows change,
using PostgreSQL triggers and NOTIFY/LISTEN. Events are published to Redis
for real-time distribution to connected clients.

Usage:
    from hit_modules.db_events import EventEmittingBase, emit_events

    @emit_events("counter.updated", fields=["id", "value"])
    class CounterModel(EventEmittingBase):
        __tablename__ = "ping_pong_counters"
        id = Column(String, primary_key=True)
        value = Column(Integer, default=0)

    # In your FastAPI lifespan:
    from hit_modules.db_events import start_db_event_listener

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        task = asyncio.create_task(start_db_event_listener(engine))
        yield
        task.cancel()

How it works:
1. @emit_events decorator registers the model with event metadata
2. EventEmittingBase.metadata.create_all() creates PostgreSQL triggers
3. start_db_event_listener() listens for pg_notify and publishes to Redis
4. Frontend receives events via WebSocket (handled by events module)

Benefits:
- Single source of truth: DB is the authority, events are automatic
- Works for raw SQL: Triggers fire for any change, not just ORM
- No manual publish calls: Just decorate your models
"""

from __future__ import annotations

import asyncio
import json
import select
from dataclasses import dataclass, field
from typing import Any, Callable

from sqlalchemy import event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase

from .events import publish_event
from .logger import get_logger

logger = get_logger(__name__)

# Channel name for PostgreSQL NOTIFY
PG_NOTIFY_CHANNEL = "hit_db_events"

# Registry of models decorated with @emit_events
_event_models: dict[str, "EventModelConfig"] = {}


@dataclass
class EventModelConfig:
    """Configuration for a model that emits events."""

    table_name: str
    event_type: str
    fields: list[str] | None = None  # None means all fields
    operations: list[str] = field(
        default_factory=lambda: ["INSERT", "UPDATE", "DELETE"]
    )


class EventEmittingBase(DeclarativeBase):
    """Base class for SQLAlchemy models that emit events on change.

    Use with @emit_events decorator to configure event publishing.
    Triggers are created automatically when metadata.create_all() is called.
    """

    pass


def emit_events(
    event_type: str,
    *,
    fields: list[str] | None = None,
    operations: list[str] | None = None,
) -> Callable[[type], type]:
    """Decorator to configure a model to emit events on database changes.

    Args:
        event_type: Event type to publish (e.g., "counter.updated")
        fields: List of field names to include in event payload (None = all)
        operations: List of operations to emit events for (default: INSERT, UPDATE, DELETE)

    Example:
        @emit_events("counter.updated", fields=["id", "value"])
        class CounterModel(EventEmittingBase):
            __tablename__ = "ping_pong_counters"
            id = Column(String, primary_key=True)
            value = Column(Integer, default=0)
    """

    def decorator(cls: type) -> type:
        if not hasattr(cls, "__tablename__"):
            raise ValueError(f"Model {cls.__name__} must have __tablename__ attribute")

        table_name = cls.__tablename__
        ops = operations or ["INSERT", "UPDATE", "DELETE"]

        # Register the model
        _event_models[table_name] = EventModelConfig(
            table_name=table_name,
            event_type=event_type,
            fields=fields,
            operations=ops,
        )

        logger.debug(f"Registered event-emitting model: {table_name} -> {event_type}")
        return cls

    return decorator


def get_notify_function_sql() -> str:
    """Get SQL to create the generic pg_notify trigger function."""
    return """
    CREATE OR REPLACE FUNCTION hit_notify_change() RETURNS TRIGGER AS $$
    DECLARE
        payload JSON;
        row_data JSON;
    BEGIN
        -- Get the row data based on operation
        IF TG_OP = 'DELETE' THEN
            row_data := row_to_json(OLD);
        ELSE
            row_data := row_to_json(NEW);
        END IF;
        
        -- Build the payload
        payload := json_build_object(
            'table', TG_TABLE_NAME,
            'event_type', TG_ARGV[0],
            'operation', TG_OP,
            'data', row_data,
            'old_data', CASE WHEN TG_OP = 'UPDATE' THEN row_to_json(OLD) ELSE NULL END,
            'timestamp', NOW()
        );
        
        -- Send notification
        PERFORM pg_notify('hit_db_events', payload::text);
        
        -- Return appropriate row
        IF TG_OP = 'DELETE' THEN
            RETURN OLD;
        ELSE
            RETURN NEW;
        END IF;
    END;
    $$ LANGUAGE plpgsql;
    """


def get_trigger_sql(table_name: str, event_type: str, operations: list[str]) -> str:
    """Get SQL to create a trigger for a table.

    Args:
        table_name: Name of the table
        event_type: Event type to pass to the trigger function
        operations: List of operations (INSERT, UPDATE, DELETE)

    Returns:
        SQL to create the trigger (wrapped in DO block for safety)
    """
    trigger_name = f"hit_notify_{table_name}"
    ops_clause = " OR ".join(operations)

    # Use DO block with exception handling to safely handle case where table
    # might not exist yet (e.g., during concurrent schema creation)
    return f"""
    DO $$
    BEGIN
        -- Drop existing trigger if it exists
        IF EXISTS (
            SELECT 1 FROM pg_trigger 
            WHERE tgname = '{trigger_name}'
        ) THEN
            DROP TRIGGER {trigger_name} ON {table_name};
        END IF;
        
        -- Create the trigger (only if table exists)
        IF EXISTS (
            SELECT 1 FROM information_schema.tables 
            WHERE table_name = '{table_name}'
        ) THEN
            CREATE TRIGGER {trigger_name}
                AFTER {ops_clause} ON {table_name}
                FOR EACH ROW
                EXECUTE FUNCTION hit_notify_change('{event_type}');
        END IF;
    END $$;
    """


def setup_pg_notify_triggers(engine: Engine, connection: Any = None) -> None:
    """Create PostgreSQL triggers for all registered event-emitting models.

    This should be called after metadata.create_all() to ensure tables exist.
    Safe to call multiple times (uses CREATE OR REPLACE / DROP IF EXISTS).

    Args:
        engine: SQLAlchemy engine to use for creating triggers
        connection: Optional existing connection to use (for use within transactions)
    """
    if not _event_models:
        logger.debug("No event-emitting models registered, skipping trigger setup")
        return

    def do_setup(conn: Any) -> None:
        # Create the generic notify function
        conn.execute(text(get_notify_function_sql()))
        logger.info("Created hit_notify_change() function")

        # Create triggers for each registered model
        for table_name, config in _event_models.items():
            trigger_sql = get_trigger_sql(
                table_name,
                config.event_type,
                config.operations,
            )
            try:
                conn.execute(text(trigger_sql))
                logger.info(f"Created trigger for {table_name} -> {config.event_type}")
            except Exception as e:
                # Log but don't fail - trigger will be created on next call
                logger.warning(f"Deferred trigger creation for {table_name}: {e}")

    if connection is not None:
        # Use the existing connection (within a transaction)
        do_setup(connection)
    else:
        # Create a new connection if none provided
        with engine.connect() as conn:
            do_setup(conn)
            conn.commit()


async def start_db_event_listener(
    engine: Engine,
    project_slug: str | None = None,
) -> None:
    """Start listening for PostgreSQL NOTIFY events and publish to Redis.

    This is a long-running async task that should be started in your app's lifespan.
    Supports both psycopg (v3, async-native) and psycopg2 (v2, sync with select).

    Args:
        engine: SQLAlchemy engine (used to get connection params)
        project_slug: Project slug for event channel isolation

    Example:
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            task = asyncio.create_task(start_db_event_listener(engine))
            yield
            task.cancel()
    """
    # Get connection URL from engine (with actual password, not masked)
    db_url = engine.url.render_as_string(hide_password=False)

    # Parse SQLAlchemy URL
    # Format: postgresql://user:pass@host:port/dbname or postgresql+psycopg://...
    from urllib.parse import urlparse, unquote

    parsed = urlparse(db_url)
    
    # Build connection params dict for psycopg3 (handles special chars in password)
    conn_params = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": parsed.username,
        "password": unquote(parsed.password) if parsed.password else "",
        "dbname": parsed.path.lstrip("/"),
    }

    logger.info(
        f"Starting PostgreSQL event listener on {parsed.hostname}:{parsed.port or 5432}/{parsed.path.lstrip('/')}"
    )

    # Try psycopg3 first (async-native), then fall back to psycopg2
    try:
        import psycopg

        await _start_psycopg3_listener(conn_params, project_slug)
    except ImportError:
        try:
            import psycopg2

            await _start_psycopg2_listener(parsed, project_slug)
        except ImportError:
            logger.error(
                "Either psycopg (v3) or psycopg2 is required for db_events. Install with: pip install 'psycopg[binary]' or pip install psycopg2-binary"
            )


async def _start_psycopg3_listener(conn_params: dict[str, Any], project_slug: str | None) -> None:
    """Start listener using psycopg v3 (async-native).
    
    Args:
        conn_params: Connection parameters dict with host, port, user, password, dbname
        project_slug: Project slug for event channel isolation
    """
    import psycopg

    retry_delay = 1.0
    max_retry_delay = 30.0

    while True:
        try:
            # psycopg v3 with async support - use keyword args for proper handling
            async with await psycopg.AsyncConnection.connect(
                autocommit=True,
                **conn_params
            ) as conn:
                logger.info(f"Listening on channel: {PG_NOTIFY_CHANNEL} (psycopg3)")

                # Reset retry delay on successful connection
                retry_delay = 1.0

                # Subscribe to notifications
                await conn.execute(f"LISTEN {PG_NOTIFY_CHANNEL}")

                # Listen for notifications (async generator)
                # Use timeout to periodically check for cancellation
                async for notify in conn.notifies():
                    await _handle_pg_notify(notify.payload, project_slug)

        except asyncio.CancelledError:
            logger.info("PostgreSQL event listener cancelled")
            break
        except Exception as e:
            logger.error(f"PostgreSQL listener error: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)


async def _start_psycopg2_listener(parsed: Any, project_slug: str | None) -> None:
    """Start listener using psycopg2 (sync with select)."""
    import psycopg2

    conn_params = {
        "host": parsed.hostname,
        "port": parsed.port or 5432,
        "user": parsed.username,
        "password": parsed.password,
        "dbname": parsed.path.lstrip("/"),
    }

    retry_delay = 1.0
    max_retry_delay = 30.0

    while True:
        conn = None
        try:
            # Connect to PostgreSQL
            conn = psycopg2.connect(**conn_params)
            conn.set_isolation_level(psycopg2.extensions.ISOLATION_LEVEL_AUTOCOMMIT)

            cursor = conn.cursor()
            cursor.execute(f"LISTEN {PG_NOTIFY_CHANNEL};")
            logger.info(f"Listening on channel: {PG_NOTIFY_CHANNEL} (psycopg2)")

            # Reset retry delay on successful connection
            retry_delay = 1.0

            # Listen for notifications
            while True:
                # Use select with timeout to allow for cooperative cancellation
                if select.select([conn], [], [], 1.0) == ([], [], []):
                    # Timeout - check if we should continue
                    await asyncio.sleep(0)
                    continue

                conn.poll()
                while conn.notifies:
                    notify = conn.notifies.pop(0)
                    await _handle_pg_notify(notify.payload, project_slug)

                # Yield to event loop
                await asyncio.sleep(0)

        except asyncio.CancelledError:
            logger.info("PostgreSQL event listener cancelled")
            break
        except Exception as e:
            logger.error(f"PostgreSQL listener error: {e}")
            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, max_retry_delay)
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass


async def _handle_pg_notify(payload: str, project_slug: str | None) -> None:
    """Handle a PostgreSQL NOTIFY payload.

    Parses the JSON payload and publishes to Redis.

    Args:
        payload: JSON string from pg_notify
        project_slug: Project slug for event channel isolation
    """
    try:
        data = json.loads(payload)

        table_name = data.get("table")
        event_type = data.get("event_type")
        operation = data.get("operation")
        row_data = data.get("data", {})
        old_data = data.get("old_data")

        if not table_name or not event_type:
            logger.warning(f"Invalid pg_notify payload: {payload}")
            return

        # Get model config to filter fields if needed
        config = _event_models.get(table_name)
        if config and config.fields:
            # Filter to only requested fields
            row_data = {k: v for k, v in row_data.items() if k in config.fields}
            if old_data:
                old_data = {k: v for k, v in old_data.items() if k in config.fields}

        # Build event payload
        # Use field names that match what frontends expect
        event_payload = {
            **row_data,  # Include all filtered row data at top level
            "action": operation.lower(),  # Frontends expect 'action' not 'operation'
        }

        # For counter updates, map 'id' to 'counter_id' for frontend compatibility
        if "id" in event_payload and "counter_id" not in event_payload:
            event_payload["counter_id"] = event_payload.pop("id")

        if old_data:
            event_payload["old"] = old_data

        # Publish via Events Module SDK (or direct Redis for events module itself)
        try:
            await publish_event(
                event_type,
                event_payload,
                project_slug=project_slug,
            )
            logger.debug(
                f"Published DB event: {event_type} from {table_name} ({operation})"
            )
        except Exception as e:
            logger.error(f"Failed to publish event via Events Module: {e}")

    except json.JSONDecodeError as e:
        logger.error(f"Invalid JSON in pg_notify payload: {e}")
    except Exception as e:
        logger.error(f"Error handling pg_notify: {e}")


# SQLAlchemy event listener to auto-setup triggers after metadata.create_all()
@event.listens_for(EventEmittingBase.metadata, "after_create")
def _after_create(target: Any, connection: Any, **kw: Any) -> None:
    """Automatically create triggers after tables are created.

    This is called by SQLAlchemy after metadata.create_all().
    Uses the existing connection to ensure triggers see uncommitted tables.
    """
    if _event_models:
        logger.debug("Setting up pg_notify triggers after table creation")
        engine = connection.engine
        # Use the existing connection to see uncommitted tables in the transaction
        setup_pg_notify_triggers(engine, connection=connection)
