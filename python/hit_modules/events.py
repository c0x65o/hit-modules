"""Event publishing and subscription helpers for HIT modules.

This module provides Redis-based event pub/sub functionality:
- EventPublisher: For modules to publish events
- EventSubscriber: For modules to subscribe to events (server-side)

Usage:
    from hit_modules.events import publish_event, get_event_publisher

    # Simple one-liner publish
    await publish_event("counter.updated", {"id": "test", "value": 42})

    # Or with publisher instance for batching
    publisher = get_event_publisher()
    await publisher.publish("counter.updated", {"id": "test", "value": 42})

Environment Variables:
    REDIS_URL: Redis connection URL (default: redis://redis-master:6379)
    HIT_EVENTS_PREFIX: Event channel prefix (default: hit:events)
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


def _get_redis_url() -> str:
    """Get Redis URL from environment."""
    return os.getenv("REDIS_URL", "redis://redis-master:6379")


def _get_events_prefix() -> str:
    """Get event channel prefix."""
    return os.getenv("HIT_EVENTS_PREFIX", "hit:events")


@dataclass
class EventMessage:
    """Structured event message."""
    
    channel: str
    event_type: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
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
    """
    
    def __init__(
        self,
        redis_url: str | None = None,
        prefix: str | None = None,
        source_module: str | None = None,
    ):
        self._redis_url = redis_url or _get_redis_url()
        self._prefix = prefix or _get_events_prefix()
        self._source_module = source_module or os.getenv("HIT_MODULE_NAME")
        self._redis: Any = None  # redis.asyncio.Redis
        self._connected = False
    
    async def _ensure_connected(self) -> Any:
        """Ensure Redis connection is established."""
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
        """
        redis = await self._ensure_connected()
        
        # Build full channel name: hit:events:counter.updated
        channel = f"{self._prefix}:{event_type}"
        
        message = EventMessage(
            channel=channel,
            event_type=event_type,
            payload=payload,
            source_module=self._source_module,
            correlation_id=correlation_id,
        )
        
        try:
            subscribers = await redis.publish(channel, message.to_json())
            logger.debug(f"Published event '{event_type}' to {subscribers} subscribers")
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


# Global publisher instance (lazy-loaded)
_global_publisher: EventPublisher | None = None


def get_event_publisher() -> EventPublisher:
    """Get or create the global event publisher instance."""
    global _global_publisher
    if _global_publisher is None:
        _global_publisher = EventPublisher()
    return _global_publisher


async def publish_event(
    event_type: str,
    payload: dict[str, Any],
    *,
    correlation_id: str | None = None,
) -> int:
    """Convenience function to publish an event using the global publisher.
    
    Args:
        event_type: Event type (e.g., "counter.updated")
        payload: Event payload data
        correlation_id: Optional correlation ID for tracing
    
    Returns:
        Number of subscribers that received the message
    
    Example:
        from hit_modules.events import publish_event
        
        await publish_event("counter.updated", {"id": "test", "value": 42})
    """
    publisher = get_event_publisher()
    return await publisher.publish(event_type, payload, correlation_id=correlation_id)


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

