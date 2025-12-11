"""Event publishing and subscription helpers for HIT modules.

This module provides event pub/sub functionality via the Events Module:
- EventPublisher: For modules to publish events (via Events Module HTTP API)
- EventSubscriber: For modules to subscribe to events (server-side)

Usage:
    from hit_modules.events import publish_event, get_event_publisher

    # Simple one-liner publish (with project context)
    await publish_event("counter.updated", {"id": "test", "value": 42}, project_slug="hello-world")

    # Or let it auto-detect project from HIT_PROJECT_SLUG env var
    await publish_event("counter.updated", {"id": "test", "value": 42})

    # Or with publisher instance for batching
    publisher = get_event_publisher(project_slug="hello-world")
    await publisher.publish("counter.updated", {"id": "test", "value": 42})

Environment Variables:
    HIT_EVENTS_URL: Events module URL (preferred - uses HTTP API)
    REDIS_URL: Redis connection URL (fallback for events module itself)
    HIT_PROJECT_SLUG: Project slug for event channel isolation
"""

from __future__ import annotations

import asyncio
import json
import os
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Callable, TypeVar

from .logger import get_logger

logger = get_logger(__name__)

# Type for event handlers
T = TypeVar("T")
EventHandler = Callable[[dict[str, Any]], None]
AsyncEventHandler = Callable[[dict[str, Any]], Any]


def _get_events_url() -> str | None:
    """Get Events Module URL from environment (preferred method)."""
    return os.getenv("HIT_EVENTS_URL")


def _get_redis_url() -> str | None:
    """Get Redis URL from environment (fallback for events module itself)."""
    url = os.getenv("REDIS_URL")
    # Don't return default - only use Redis if explicitly configured
    return url if url else None


async def check_events_health() -> dict[str, Any]:
    """Check if the Events Module is reachable and healthy.

    Returns:
        dict with status, ok, and optional error

    Example:
        health = await check_events_health()
        if not health["ok"]:
            raise RuntimeError(f"Events module unhealthy: {health['error']}")
    """
    events_url = _get_events_url()

    if not events_url:
        return {
            "status": "not_configured",
            "ok": False,
            "error": "HIT_EVENTS_URL is not configured",
        }

    try:
        import httpx
    except ImportError:
        return {
            "status": "missing_dependency",
            "ok": False,
            "error": "httpx package required for events health check",
        }

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            response = await client.get(f"{events_url.rstrip('/')}/hit/health")
            if response.status_code == 200:
                return {
                    "status": "healthy",
                    "ok": True,
                    "url": events_url,
                }
            else:
                return {
                    "status": "unhealthy",
                    "ok": False,
                    "error": f"Events module returned {response.status_code}",
                    "url": events_url,
                }
    except httpx.ConnectError as e:
        return {
            "status": "unreachable",
            "ok": False,
            "error": f"Cannot connect to events module: {e}",
            "url": events_url,
        }
    except Exception as e:
        return {
            "status": "error",
            "ok": False,
            "error": str(e),
            "url": events_url,
        }


def _get_project_slug() -> str | None:
    """Get project slug from environment."""
    return os.getenv("HIT_PROJECT_SLUG")


def _get_events_prefix(project_slug: str | None = None) -> str:
    """Get event channel prefix with optional project isolation.

    Format: hit:events:{project_slug} or hit:events (if no project)
    """
    base = "hit:events"
    project = project_slug or _get_project_slug()
    if project:
        return f"{base}:{project}"
    return base


@dataclass
class EventMessage:
    """Structured event message."""

    channel: str
    event_type: str
    payload: dict[str, Any]
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    source_module: str | None = None
    correlation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "channel": self.channel,
            "event_type": self.event_type,
            "payload": self.payload,
            "timestamp": self.timestamp,
            "source_module": self.source_module,
            "correlation_id": self.correlation_id,
        }

    def to_json(self) -> str:
        """Serialize to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, data: str | bytes) -> "EventMessage":
        """Deserialize from JSON string."""
        if isinstance(data, bytes):
            data = data.decode("utf-8")
        parsed = json.loads(data)
        return cls(
            channel=parsed.get("channel", ""),
            event_type=parsed.get("event_type", ""),
            payload=parsed.get("payload", {}),
            timestamp=parsed.get("timestamp", ""),
            source_module=parsed.get("source_module"),
            correlation_id=parsed.get("correlation_id"),
        )


class EventPublisher:
    """Redis-based event publisher for HIT modules.

    Thread-safe and async-compatible. Maintains a connection pool.
    Events are isolated per project using channel prefixes.

    NOTE: This class is primarily used by the Events Module itself.
    Other modules should use the publish_event() function which routes
    events through the Events Module HTTP API when HIT_EVENTS_URL is set.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        project_slug: str | None = None,
        source_module: str | None = None,
    ):
        self._redis_url = redis_url or _get_redis_url()
        self._project_slug = project_slug or _get_project_slug()
        self._prefix = _get_events_prefix(self._project_slug)
        self._source_module = source_module or os.getenv("HIT_MODULE_NAME")
        self._redis: Any = None  # redis.asyncio.Redis
        self._connected = False

    async def _ensure_connected(self) -> Any:
        """Ensure Redis connection is established."""
        if not self._redis_url:
            raise RuntimeError(
                "REDIS_URL is not configured. "
                "Use publish_event() with HIT_EVENTS_URL for module-to-module communication, "
                "or set REDIS_URL for direct Redis access (events module only)."
            )

        if self._redis is None or not self._connected:
            try:
                import redis.asyncio as aioredis
            except ImportError:
                raise ImportError(
                    "redis package required for events. "
                    "Install with: pip install redis[hiredis]"
                )

            self._redis = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            self._connected = True
            logger.debug(f"Connected to Redis at {self._redis_url}")

        return self._redis

    async def publish(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        correlation_id: str | None = None,
    ) -> int:
        """Publish an event to Redis.

        Args:
            event_type: Event type (e.g., "counter.updated", "user.created")
            payload: Event payload data
            correlation_id: Optional correlation ID for tracing

        Returns:
            Number of subscribers that received the message

        Example:
            await publisher.publish("counter.updated", {"id": "test", "value": 42})

        Channel format:
            hit:events:{project_slug}:{event_type}
            e.g., hit:events:hello-world:counter.updated
        """
        redis = await self._ensure_connected()

        # Build full channel name: hit:events:{project}:{event_type}
        channel = f"{self._prefix}:{event_type}"

        message = EventMessage(
            channel=channel,
            event_type=event_type,
            payload=payload,
            source_module=self._source_module,
            correlation_id=correlation_id,
        )

        # Add project to payload for tracking
        message_dict = message.to_dict()
        message_dict["project"] = self._project_slug

        try:
            subscribers = await redis.publish(channel, json.dumps(message_dict))
            logger.debug(
                f"Published event '{event_type}' to {channel} ({subscribers} subscribers)"
            )
            return subscribers
        except Exception as e:
            logger.error(f"Failed to publish event '{event_type}': {e}")
            raise

    async def publish_batch(
        self,
        events: list[tuple[str, dict[str, Any]]],
        *,
        correlation_id: str | None = None,
    ) -> list[int]:
        """Publish multiple events in a pipeline.

        Args:
            events: List of (event_type, payload) tuples
            correlation_id: Shared correlation ID for all events

        Returns:
            List of subscriber counts for each event
        """
        redis = await self._ensure_connected()

        async with redis.pipeline() as pipe:
            for event_type, payload in events:
                channel = f"{self._prefix}:{event_type}"
                message = EventMessage(
                    channel=channel,
                    event_type=event_type,
                    payload=payload,
                    source_module=self._source_module,
                    correlation_id=correlation_id,
                )
                pipe.publish(channel, message.to_json())

            results = await pipe.execute()

        logger.debug(f"Published batch of {len(events)} events")
        return results

    async def close(self) -> None:
        """Close Redis connection."""
        if self._redis is not None:
            await self._redis.close()
            self._redis = None
            self._connected = False
            logger.debug("Closed Redis connection")


class EventSubscriber:
    """Redis-based event subscriber for server-side event handling.

    Useful for modules that need to react to events from other modules.
    For browser/client subscriptions, use the events gateway WebSocket.
    """

    def __init__(
        self,
        redis_url: str | None = None,
        prefix: str | None = None,
    ):
        self._redis_url = redis_url or _get_redis_url()
        self._prefix = prefix or _get_events_prefix()
        self._redis: Any = None
        self._pubsub: Any = None
        self._handlers: dict[str, list[AsyncEventHandler]] = {}
        self._running = False

    async def _ensure_connected(self) -> Any:
        """Ensure Redis pubsub is established."""
        if self._redis is None:
            try:
                import redis.asyncio as aioredis
            except ImportError:
                raise ImportError(
                    "redis package required for events. "
                    "Install with: pip install redis[hiredis]"
                )

            self._redis = aioredis.from_url(
                self._redis_url,
                encoding="utf-8",
                decode_responses=True,
            )
            self._pubsub = self._redis.pubsub()
            logger.debug(f"Subscriber connected to Redis at {self._redis_url}")

        return self._pubsub

    async def subscribe(
        self,
        event_pattern: str,
        handler: AsyncEventHandler,
    ) -> None:
        """Subscribe to events matching a pattern.

        Args:
            event_pattern: Event pattern (e.g., "counter.*", "user.created")
            handler: Async function to handle events

        Example:
            async def on_counter_update(event):
                print(f"Counter updated: {event['payload']}")

            await subscriber.subscribe("counter.*", on_counter_update)
        """
        pubsub = await self._ensure_connected()

        # Store handler
        full_pattern = f"{self._prefix}:{event_pattern}"
        if full_pattern not in self._handlers:
            self._handlers[full_pattern] = []
            # Subscribe to Redis pattern
            await pubsub.psubscribe(full_pattern)
            logger.debug(f"Subscribed to pattern: {full_pattern}")

        self._handlers[full_pattern].append(handler)

    async def unsubscribe(self, event_pattern: str) -> None:
        """Unsubscribe from an event pattern."""
        full_pattern = f"{self._prefix}:{event_pattern}"
        if full_pattern in self._handlers:
            del self._handlers[full_pattern]
            if self._pubsub:
                await self._pubsub.punsubscribe(full_pattern)
            logger.debug(f"Unsubscribed from pattern: {full_pattern}")

    async def listen(self) -> AsyncIterator[EventMessage]:
        """Async generator that yields incoming events.

        Example:
            async for event in subscriber.listen():
                print(f"Received: {event.event_type}")
        """
        pubsub = await self._ensure_connected()
        self._running = True

        try:
            async for message in pubsub.listen():
                if not self._running:
                    break

                if message["type"] in ("pmessage", "message"):
                    try:
                        event = EventMessage.from_json(message["data"])
                        yield event
                    except (json.JSONDecodeError, KeyError) as e:
                        logger.warning(f"Invalid event message: {e}")
        finally:
            self._running = False

    async def run(self) -> None:
        """Run the subscriber, dispatching events to handlers.

        Call this as a background task:
            asyncio.create_task(subscriber.run())
        """
        async for event in self.listen():
            # Find matching handlers
            for pattern, handlers in self._handlers.items():
                # Simple pattern matching (could be improved)
                if event.channel.startswith(pattern.replace("*", "")):
                    for handler in handlers:
                        try:
                            result = handler(event.to_dict())
                            if asyncio.iscoroutine(result):
                                await result
                        except Exception as e:
                            logger.error(f"Event handler error: {e}")

    async def close(self) -> None:
        """Close subscriber and Redis connection."""
        self._running = False
        if self._pubsub:
            await self._pubsub.close()
            self._pubsub = None
        if self._redis:
            await self._redis.close()
            self._redis = None
        logger.debug("Closed subscriber Redis connection")


# Global publisher instances (lazy-loaded, per project)
_global_publishers: dict[str, EventPublisher] = {}


def get_event_publisher(project_slug: str | None = None) -> EventPublisher:
    """Get or create an event publisher instance.

    Args:
        project_slug: Project slug for channel isolation (defaults to HIT_PROJECT_SLUG env)

    Returns:
        EventPublisher instance for the project
    """
    project = project_slug or _get_project_slug() or "default"

    if project not in _global_publishers:
        _global_publishers[project] = EventPublisher(project_slug=project)

    return _global_publishers[project]


async def publish_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    project_slug: str | None = None,
    correlation_id: str | None = None,
) -> int:
    """Publish an event via the Events Module HTTP API.

    Modules publish events through the Events Module HTTP API (HIT_EVENTS_URL).
    Only the Events Module itself talks directly to Redis.

    Args:
        event_type: Event type (e.g., "counter.updated")
        payload: Event payload data
        project_slug: Project slug for channel isolation (defaults to HIT_PROJECT_SLUG env)
        correlation_id: Optional correlation ID for tracing

    Returns:
        Number of subscribers that received the message

    Raises:
        RuntimeError: If HIT_EVENTS_URL is not configured

    Example:
        from hit_modules.events import publish_event

        # With explicit project
        await publish_event("counter.updated", {"id": "test", "value": 42}, project_slug="hello-world")

        # Or rely on HIT_PROJECT_SLUG env var
        await publish_event("counter.updated", {"id": "test", "value": 42})
    """
    project = project_slug or _get_project_slug() or "default"
    events_url = _get_events_url()

    if not events_url:
        raise RuntimeError(
            f"Cannot publish event '{event_type}': HIT_EVENTS_URL is not configured. "
            "Modules must publish events via the Events Module HTTP API."
        )

    return await _publish_via_http(
        events_url, event_type, payload, project, correlation_id
    )


async def _publish_via_http(
    events_url: str,
    event_type: str,
    payload: dict[str, Any],
    project_slug: str,
    correlation_id: str | None = None,
) -> int:
    """Publish event via Events Module HTTP API.

    This is the recommended pattern - modules call the Events Module SDK,
    which handles Redis internally (like how ping-pong SDK calls ping-pong service).
    """
    try:
        import httpx
    except ImportError:
        raise ImportError(
            "httpx package required for events HTTP publishing. "
            "Install with: pip install httpx"
        )

    url = f"{events_url.rstrip('/')}/publish"
    params = {"event_type": event_type}
    headers = {"X-HIT-Project-Slug": project_slug}
    
    # Include service token for inter-module authentication
    import os
    service_token = os.environ.get("HIT_SERVICE_TOKEN")
    if service_token:
        headers["X-HIT-Service-Token"] = service_token
    else:
        logger.warning(
            "HIT_SERVICE_TOKEN not found. Events module may reject the request. "
            "Set HIT_SERVICE_TOKEN in environment for inter-module authentication."
        )

    if correlation_id:
        payload = {**payload, "correlation_id": correlation_id}

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                url, params=params, headers=headers, json=payload
            )
            response.raise_for_status()
            result = response.json()
            subscribers = result.get("subscribers", 1)
            logger.debug(
                f"Published event '{event_type}' via Events Module ({subscribers} subscribers)"
            )
            return subscribers
    except httpx.HTTPStatusError as e:
        logger.error(
            f"Events Module returned error: {e.response.status_code} - {e.response.text}"
        )
        raise
    except Exception as e:
        logger.error(f"Failed to publish event via Events Module: {e}")
        raise


@asynccontextmanager
async def event_publisher_context(
    redis_url: str | None = None,
) -> AsyncIterator[EventPublisher]:
    """Context manager for a dedicated event publisher.

    Example:
        async with event_publisher_context() as publisher:
            await publisher.publish("event", {"data": "value"})
    """
    publisher = EventPublisher(redis_url=redis_url)
    try:
        yield publisher
    finally:
        await publisher.close()
