"""
State management for Circuit Agent service layer.

Provides immutable data classes for representing agent state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, auto
from typing import Any, Dict, List, Optional


class ConnectionStatus(Enum):
    """Agent connection status."""

    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    ERROR = auto()


class MessageRole(Enum):
    """Chat message role."""

    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ToolStatus(Enum):
    """Tool execution status."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class ChatMessage:
    """
    Immutable chat message.

    Attributes:
        id: Unique message identifier
        role: Who sent the message (user, assistant, system, tool)
        content: Message text content
        timestamp: When the message was created
        tool_calls: List of tool calls if this is an assistant message
        tool_call_id: ID of the tool call this is a response to
        is_streaming: Whether this message is still being streamed
        metadata: Additional message metadata
    """

    id: str
    role: MessageRole
    content: str
    timestamp: datetime = field(default_factory=datetime.now)
    tool_calls: List[ToolCallInfo] = field(default_factory=list)
    tool_call_id: Optional[str] = None
    is_streaming: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def with_content(self, content: str) -> ChatMessage:
        """Create a new message with updated content."""
        return ChatMessage(
            id=self.id,
            role=self.role,
            content=content,
            timestamp=self.timestamp,
            tool_calls=self.tool_calls,
            tool_call_id=self.tool_call_id,
            is_streaming=self.is_streaming,
            metadata=self.metadata,
        )

    def with_streaming(self, is_streaming: bool) -> ChatMessage:
        """Create a new message with updated streaming status."""
        return ChatMessage(
            id=self.id,
            role=self.role,
            content=self.content,
            timestamp=self.timestamp,
            tool_calls=self.tool_calls,
            tool_call_id=self.tool_call_id,
            is_streaming=is_streaming,
            metadata=self.metadata,
        )


@dataclass(frozen=True)
class ToolCallInfo:
    """
    Information about a tool call.

    Attributes:
        id: Unique tool call identifier
        name: Name of the tool being called
        arguments: Tool arguments as a dict
        status: Current status of the tool call
        result: Result of the tool call (if completed)
        error: Error message (if failed)
        started_at: When the tool call started
        completed_at: When the tool call completed
        requires_confirmation: Whether user confirmation is needed
    """

    id: str
    name: str
    arguments: Dict[str, Any] = field(default_factory=dict)
    status: ToolStatus = ToolStatus.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    requires_confirmation: bool = False

    @property
    def detail(self) -> str:
        """Get a short description of the tool call."""
        if self.name == "read_file":
            return self.arguments.get("path", "")
        elif self.name == "write_file":
            return self.arguments.get("path", "")
        elif self.name == "edit_file":
            return self.arguments.get("path", "")
        elif self.name == "run_command":
            cmd = self.arguments.get("command", "")
            return cmd[:50] + "..." if len(cmd) > 50 else cmd
        elif self.name == "list_files":
            return self.arguments.get("pattern", "**/*")
        elif self.name == "search_files":
            return self.arguments.get("pattern", "")
        elif self.name.startswith("git_"):
            return self.name.replace("git_", "")
        else:
            return ""

    def with_status(
        self, status: ToolStatus, result: Optional[str] = None, error: Optional[str] = None
    ) -> ToolCallInfo:
        """Create a new ToolCallInfo with updated status."""
        return ToolCallInfo(
            id=self.id,
            name=self.name,
            arguments=self.arguments,
            status=status,
            result=result if result is not None else self.result,
            error=error if error is not None else self.error,
            started_at=self.started_at,
            completed_at=datetime.now()
            if status in (ToolStatus.SUCCESS, ToolStatus.ERROR)
            else None,
            requires_confirmation=self.requires_confirmation,
        )


@dataclass(frozen=True)
class ConfirmationRequest:
    """
    Request for user confirmation.

    Attributes:
        id: Unique request identifier
        tool_call: The tool call that needs confirmation
        message: Human-readable confirmation message
        details: Additional details about what will happen
        is_dangerous: Whether this is a potentially dangerous operation
        timeout: Timeout in seconds (0 = no timeout)
    """

    id: str
    tool_call: ToolCallInfo
    message: str
    details: str = ""
    is_dangerous: bool = False
    timeout: float = 60.0

    @property
    def tool_name(self) -> str:
        """Get the tool name."""
        return self.tool_call.name


@dataclass
class TokenUsage:
    """Token usage statistics."""

    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass
class CostInfo:
    """Cost tracking information."""

    total_cost_usd: float = 0.0
    session_cost_usd: float = 0.0
    by_model: Dict[str, float] = field(default_factory=dict)


@dataclass
class AgentState:
    """
    Complete state of the agent.

    This is the single source of truth for the agent's current state.
    UIs should read from this state to render their views.

    Attributes:
        connection_status: Current connection status
        model: Current model name
        working_dir: Current working directory
        auto_approve: Whether auto-approve is enabled
        thinking_mode: Whether thinking mode is enabled
        stream_responses: Whether streaming is enabled
        messages: List of chat messages
        pending_confirmation: Current pending confirmation request
        is_processing: Whether the agent is currently processing
        is_thinking: Whether the agent is in thinking phase
        session_tokens: Token usage for current session
        last_tokens: Token usage for last request
        cost: Cost tracking information
        error: Current error message if any
    """

    # Connection
    connection_status: ConnectionStatus = ConnectionStatus.DISCONNECTED
    error: Optional[str] = None

    # Configuration
    model: str = "gpt-4o"
    working_dir: str = "."
    auto_approve: bool = False
    thinking_mode: bool = False
    stream_responses: bool = True

    # Messages
    messages: List[ChatMessage] = field(default_factory=list)

    # Confirmation
    pending_confirmation: Optional[ConfirmationRequest] = None

    # Processing state
    is_processing: bool = False
    is_thinking: bool = False
    current_tool_calls: List[ToolCallInfo] = field(default_factory=list)

    # Token tracking
    session_tokens: TokenUsage = field(default_factory=TokenUsage)
    last_tokens: TokenUsage = field(default_factory=TokenUsage)

    # Cost tracking
    cost: CostInfo = field(default_factory=CostInfo)

    @property
    def is_connected(self) -> bool:
        """Check if agent is connected."""
        return self.connection_status == ConnectionStatus.CONNECTED

    @property
    def can_send_message(self) -> bool:
        """Check if a message can be sent."""
        return self.is_connected and not self.is_processing and self.pending_confirmation is None

    @property
    def total_tokens(self) -> int:
        """Get total tokens used in session."""
        return self.session_tokens.total

    def add_message(self, message: ChatMessage) -> AgentState:
        """Create a new state with an added message."""
        return AgentState(
            connection_status=self.connection_status,
            error=self.error,
            model=self.model,
            working_dir=self.working_dir,
            auto_approve=self.auto_approve,
            thinking_mode=self.thinking_mode,
            stream_responses=self.stream_responses,
            messages=self.messages + [message],
            pending_confirmation=self.pending_confirmation,
            is_processing=self.is_processing,
            is_thinking=self.is_thinking,
            current_tool_calls=self.current_tool_calls,
            session_tokens=self.session_tokens,
            last_tokens=self.last_tokens,
            cost=self.cost,
        )

    def clear_messages(self) -> AgentState:
        """Create a new state with cleared messages."""
        return AgentState(
            connection_status=self.connection_status,
            error=self.error,
            model=self.model,
            working_dir=self.working_dir,
            auto_approve=self.auto_approve,
            thinking_mode=self.thinking_mode,
            stream_responses=self.stream_responses,
            messages=[],
            pending_confirmation=self.pending_confirmation,
            is_processing=self.is_processing,
            is_thinking=self.is_thinking,
            current_tool_calls=self.current_tool_calls,
            session_tokens=self.session_tokens,
            last_tokens=self.last_tokens,
            cost=self.cost,
        )
