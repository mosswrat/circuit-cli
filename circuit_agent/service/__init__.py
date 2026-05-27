"""
Circuit Agent Service Layer.

Provides a unified backend for all UI implementations (CLI, TUI, GUI).
This layer abstracts the agent logic and provides event-driven communication.
"""

from .agent_service import AgentService
from .events import Event, EventEmitter, EventType
from .state import (
    AgentState,
    ChatMessage,
    ConfirmationRequest,
    ConnectionStatus,
    CostInfo,
    MessageRole,
    TokenUsage,
    ToolCallInfo,
    ToolStatus,
)

__all__ = [
    # Events
    "Event",
    "EventEmitter",
    "EventType",
    # State
    "AgentState",
    "ChatMessage",
    "ToolCallInfo",
    "ConfirmationRequest",
    "ConnectionStatus",
    "MessageRole",
    "ToolStatus",
    "TokenUsage",
    "CostInfo",
    # Service
    "AgentService",
]
