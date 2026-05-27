"""
Event system for Circuit Agent service layer.

Provides a simple event emitter for decoupled UI communication.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Callable, Dict, List, Optional, Union


class EventType(Enum):
    """Types of events emitted by the agent service."""

    # Connection events
    CONNECTING = auto()
    CONNECTED = auto()
    DISCONNECTED = auto()
    CONNECTION_ERROR = auto()

    # Chat events
    MESSAGE_STARTED = auto()
    MESSAGE_CHUNK = auto()
    MESSAGE_COMPLETED = auto()
    MESSAGE_ERROR = auto()

    # Tool events
    TOOL_CALL_STARTED = auto()
    TOOL_CALL_COMPLETED = auto()
    TOOL_CALL_ERROR = auto()

    # Confirmation events
    CONFIRMATION_NEEDED = auto()
    CONFIRMATION_RECEIVED = auto()
    CONFIRMATION_TIMEOUT = auto()

    # Status events
    STATUS_CHANGED = auto()
    TOKENS_UPDATED = auto()
    COST_UPDATED = auto()
    MODEL_CHANGED = auto()

    # Session events
    SESSION_SAVED = auto()
    SESSION_LOADED = auto()
    HISTORY_CLEARED = auto()

    # Agent state
    THINKING_STARTED = auto()
    THINKING_COMPLETED = auto()

    # MCP (Model Context Protocol) events
    MCP_SERVER_CONNECTING = auto()
    MCP_SERVER_CONNECTED = auto()
    MCP_SERVER_DISCONNECTED = auto()
    MCP_SERVER_ERROR = auto()
    MCP_TOOL_CALL_STARTED = auto()
    MCP_TOOL_CALL_COMPLETED = auto()
    MCP_TOOLS_UPDATED = auto()


@dataclass
class Event:
    """Base event class with common attributes."""

    type: EventType
    timestamp: datetime = field(default_factory=datetime.now)
    data: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.now()


# Type alias for event handlers
EventHandler = Callable[[Event], None]
AsyncEventHandler = Callable[[Event], Any]  # Can be async


class EventEmitter:
    """
    Thread-safe event emitter with support for sync and async handlers.

    Usage:
        emitter = EventEmitter()

        # Subscribe to events
        def on_message(event):
            print(f"Got message: {event.data}")

        emitter.on(EventType.MESSAGE_CHUNK, on_message)

        # Emit events
        emitter.emit(EventType.MESSAGE_CHUNK, {"content": "Hello"})

        # Unsubscribe
        emitter.off(EventType.MESSAGE_CHUNK, on_message)
    """

    def __init__(self):
        self._handlers: Dict[EventType, List[EventHandler]] = {}
        self._async_handlers: Dict[EventType, List[AsyncEventHandler]] = {}
        self._all_handlers: List[EventHandler] = []
        self._lock = asyncio.Lock()

    def on(
        self,
        event_type: EventType,
        handler: Union[EventHandler, AsyncEventHandler],
        is_async: bool = False,
    ) -> None:
        """
        Subscribe to an event type.

        Args:
            event_type: The type of event to listen for
            handler: Function to call when event is emitted
            is_async: Whether the handler is an async function
        """
        if is_async:
            if event_type not in self._async_handlers:
                self._async_handlers[event_type] = []
            if handler not in self._async_handlers[event_type]:
                self._async_handlers[event_type].append(handler)
        else:
            if event_type not in self._handlers:
                self._handlers[event_type] = []
            if handler not in self._handlers[event_type]:
                self._handlers[event_type].append(handler)

    def off(
        self,
        event_type: EventType,
        handler: Union[EventHandler, AsyncEventHandler],
        is_async: bool = False,
    ) -> None:
        """Unsubscribe from an event type."""
        if is_async:
            if event_type in self._async_handlers:
                try:
                    self._async_handlers[event_type].remove(handler)
                except ValueError:
                    pass
        else:
            if event_type in self._handlers:
                try:
                    self._handlers[event_type].remove(handler)
                except ValueError:
                    pass

    def on_all(self, handler: EventHandler) -> None:
        """Subscribe to all events."""
        if handler not in self._all_handlers:
            self._all_handlers.append(handler)

    def off_all(self, handler: EventHandler) -> None:
        """Unsubscribe from all events."""
        try:
            self._all_handlers.remove(handler)
        except ValueError:
            pass

    def emit(self, event_type: EventType, data: Optional[Dict[str, Any]] = None) -> Event:
        """
        Emit an event synchronously.

        Args:
            event_type: The type of event
            data: Optional data to include with the event

        Returns:
            The emitted Event object
        """
        event = Event(type=event_type, data=data or {})

        # Call sync handlers for this event type
        for handler in self._handlers.get(event_type, []):
            try:
                handler(event)
            except Exception as e:
                # Log but don't propagate handler errors
                print(f"Event handler error: {e}")

        # Call all-event handlers
        for handler in self._all_handlers:
            try:
                handler(event)
            except Exception as e:
                print(f"Event handler error: {e}")

        return event

    async def emit_async(
        self, event_type: EventType, data: Optional[Dict[str, Any]] = None
    ) -> Event:
        """
        Emit an event asynchronously.

        Calls both sync handlers (in order) and async handlers (concurrently).
        """
        event = Event(type=event_type, data=data or {})

        # Call sync handlers first
        for handler in self._handlers.get(event_type, []):
            try:
                handler(event)
            except Exception as e:
                print(f"Sync event handler error: {e}")

        # Call all-event handlers
        for handler in self._all_handlers:
            try:
                handler(event)
            except Exception as e:
                print(f"Event handler error: {e}")

        # Call async handlers concurrently
        async_handlers = self._async_handlers.get(event_type, [])
        if async_handlers:
            tasks = []
            for handler in async_handlers:
                try:
                    result = handler(event)
                    if asyncio.iscoroutine(result):
                        tasks.append(asyncio.create_task(result))
                except Exception as e:
                    print(f"Async event handler error: {e}")

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

        return event

    def clear(self, event_type: Optional[EventType] = None) -> None:
        """
        Clear handlers.

        Args:
            event_type: If provided, clear only handlers for this type.
                       If None, clear all handlers.
        """
        if event_type is None:
            self._handlers.clear()
            self._async_handlers.clear()
            self._all_handlers.clear()
        else:
            self._handlers.pop(event_type, None)
            self._async_handlers.pop(event_type, None)

    def handler_count(self, event_type: Optional[EventType] = None) -> int:
        """Get the number of handlers registered."""
        if event_type is None:
            count = len(self._all_handlers)
            for handlers in self._handlers.values():
                count += len(handlers)
            for handlers in self._async_handlers.values():
                count += len(handlers)
            return count
        else:
            return len(self._handlers.get(event_type, [])) + len(
                self._async_handlers.get(event_type, [])
            )


# Convenience function to create typed events
def create_event(event_type: EventType, **kwargs) -> Event:
    """Create an event with the given type and data."""
    return Event(type=event_type, data=kwargs)
