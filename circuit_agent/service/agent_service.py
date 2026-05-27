"""
Unified Agent Service for Circuit Agent.

Provides a single entry point for all UI implementations to interact
with the agent. Handles connection management, message processing,
tool execution, and state management.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

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


class AgentService:
    """
    Unified service layer for Circuit Agent.

    This class provides a clean interface for all UIs to interact with
    the agent. It handles:
    - Agent lifecycle (connect, disconnect)
    - Message sending and streaming
    - Tool execution and confirmation
    - State management
    - Event emission for UI updates

    Usage:
        service = AgentService(working_dir="/path/to/project")

        # Subscribe to events
        service.on(EventType.MESSAGE_CHUNK, lambda e: print(e.data["content"]))

        # Connect to the API
        await service.connect(client_id, client_secret, app_key)

        # Send a message
        await service.send_message("Help me refactor this code")

        # Handle confirmations
        service.on(EventType.CONFIRMATION_NEEDED, handle_confirmation)
        service.approve_confirmation(confirmation_id)
    """

    def __init__(
        self,
        working_dir: str = ".",
        model: str = "gpt-4o",
        auto_approve: bool = False,
        thinking_mode: bool = False,
        stream_responses: bool = True,
    ):
        """
        Initialize the agent service.

        Args:
            working_dir: Working directory for file operations
            model: Default model to use
            auto_approve: Whether to auto-approve tool calls
            thinking_mode: Whether to show agent thinking
            stream_responses: Whether to stream responses
        """
        self._working_dir = str(Path(working_dir).resolve())
        self._agent = None
        self._events = EventEmitter()

        # Initialize state
        self._state = AgentState(
            connection_status=ConnectionStatus.DISCONNECTED,
            model=model,
            working_dir=self._working_dir,
            auto_approve=auto_approve,
            thinking_mode=thinking_mode,
            stream_responses=stream_responses,
        )

        # Confirmation handling
        self._pending_confirmations: Dict[str, asyncio.Event] = {}
        self._confirmation_results: Dict[str, bool] = {}

    # =========================================================================
    # Properties
    # =========================================================================

    @property
    def state(self) -> AgentState:
        """Get the current agent state."""
        return self._state

    @property
    def is_connected(self) -> bool:
        """Check if agent is connected."""
        return self._state.is_connected

    @property
    def events(self) -> EventEmitter:
        """Get the event emitter for subscribing to events."""
        return self._events

    # =========================================================================
    # Event Subscription Shortcuts
    # =========================================================================

    def on(self, event_type: EventType, handler: Callable[[Event], None]) -> None:
        """Subscribe to an event type."""
        self._events.on(event_type, handler)

    def off(self, event_type: EventType, handler: Callable[[Event], None]) -> None:
        """Unsubscribe from an event type."""
        self._events.off(event_type, handler)

    # =========================================================================
    # Connection Management
    # =========================================================================

    async def connect(
        self,
        client_id: str,
        client_secret: str,
        app_key: str,
    ) -> bool:
        """
        Connect to the Circuit API.

        Args:
            client_id: OAuth client ID
            client_secret: OAuth client secret
            app_key: Circuit app key

        Returns:
            True if connection successful, False otherwise
        """
        self._update_state(connection_status=ConnectionStatus.CONNECTING)
        await self._events.emit_async(EventType.CONNECTING)

        try:
            # Import here to avoid circular imports
            from circuit_agent import CircuitAgent

            # CircuitAgent only takes 4 constructor params
            self._agent = CircuitAgent(
                client_id=client_id,
                client_secret=client_secret,
                app_key=app_key,
                working_dir=self._working_dir,
            )

            # Set additional properties after instantiation
            self._agent.model = self._state.model
            self._agent.auto_approve = self._state.auto_approve
            self._agent.thinking_mode = self._state.thinking_mode
            self._agent.stream_responses = self._state.stream_responses

            # Test connection by getting token
            await self._agent.get_token()

            self._update_state(
                connection_status=ConnectionStatus.CONNECTED,
                error=None,
            )
            await self._events.emit_async(EventType.CONNECTED)
            return True

        except Exception as e:
            error_msg = str(e)
            self._update_state(
                connection_status=ConnectionStatus.ERROR,
                error=error_msg,
            )
            await self._events.emit_async(EventType.CONNECTION_ERROR, {"error": error_msg})
            return False

    async def connect_with_saved_credentials(self) -> bool:
        """
        Connect using saved credentials.

        Returns:
            True if connection successful, False if no credentials or error
        """
        from circuit_agent.config import load_credentials

        client_id, client_secret, app_key = load_credentials()

        if not all([client_id, client_secret, app_key]):
            self._update_state(
                connection_status=ConnectionStatus.ERROR,
                error="No saved credentials found",
            )
            return False

        return await self.connect(client_id, client_secret, app_key)

    def disconnect(self) -> None:
        """Disconnect from the API."""
        self._agent = None
        self._update_state(connection_status=ConnectionStatus.DISCONNECTED)
        self._events.emit(EventType.DISCONNECTED)

    # =========================================================================
    # Message Handling
    # =========================================================================

    async def send_message(self, content: str) -> Optional[str]:
        """
        Send a message to the agent.

        Args:
            content: The message content

        Returns:
            The agent's response, or None if error
        """
        if not self._agent:
            await self._events.emit_async(EventType.MESSAGE_ERROR, {"error": "Not connected"})
            return None

        if self._state.is_processing:
            await self._events.emit_async(
                EventType.MESSAGE_ERROR, {"error": "Already processing a message"}
            )
            return None

        # Create user message
        user_msg_id = str(uuid.uuid4())
        user_message = ChatMessage(
            id=user_msg_id,
            role=MessageRole.USER,
            content=content,
        )
        self._add_message(user_message)

        # Start processing
        self._update_state(is_processing=True, is_thinking=True)
        await self._events.emit_async(
            EventType.MESSAGE_STARTED,
            {
                "message_id": user_msg_id,
                "content": content,
            },
        )

        # Create assistant message placeholder
        assistant_msg_id = str(uuid.uuid4())
        assistant_content = ""

        try:
            # Set up streaming callback
            def on_content(chunk: str):
                nonlocal assistant_content
                assistant_content += chunk
                self._events.emit(
                    EventType.MESSAGE_CHUNK,
                    {
                        "message_id": assistant_msg_id,
                        "chunk": chunk,
                        "content": assistant_content,
                    },
                )

            # Intercept confirmation requests
            original_confirm = self._agent._confirm_action

            async def intercept_confirm(tool_name: str, arguments: dict) -> bool:
                # Check auto-approve first
                if self._state.auto_approve:
                    return True

                # Create confirmation request
                confirm_id = str(uuid.uuid4())
                tool_call = ToolCallInfo(
                    id=confirm_id,
                    name=tool_name,
                    arguments=arguments,
                    requires_confirmation=True,
                )

                message = f"Allow {tool_name}?"
                details = tool_call.detail

                request = ConfirmationRequest(
                    id=confirm_id,
                    tool_call=tool_call,
                    message=message,
                    details=details,
                    is_dangerous=tool_name in ("run_command", "write_file", "git_commit"),
                )

                self._update_state(pending_confirmation=request)

                # Create event for confirmation
                event = asyncio.Event()
                self._pending_confirmations[confirm_id] = event

                await self._events.emit_async(EventType.CONFIRMATION_NEEDED, {"request": request})

                # Wait for confirmation
                try:
                    await asyncio.wait_for(event.wait(), timeout=request.timeout)
                    result = self._confirmation_results.get(confirm_id, False)
                except asyncio.TimeoutError:
                    await self._events.emit_async(
                        EventType.CONFIRMATION_TIMEOUT, {"request": request}
                    )
                    result = False

                # Clean up
                self._pending_confirmations.pop(confirm_id, None)
                self._confirmation_results.pop(confirm_id, None)
                self._update_state(pending_confirmation=None)

                await self._events.emit_async(
                    EventType.CONFIRMATION_RECEIVED,
                    {
                        "request": request,
                        "approved": result,
                    },
                )

                return result

            # Monkey-patch confirmation method
            self._agent._confirm_action = intercept_confirm

            # Send message to agent
            self._update_state(is_thinking=True)
            await self._events.emit_async(EventType.THINKING_STARTED)

            response = await self._agent.chat(content, on_content=on_content)

            await self._events.emit_async(EventType.THINKING_COMPLETED)
            self._update_state(is_thinking=False)

            # Restore original method
            self._agent._confirm_action = original_confirm

            # Create assistant message
            assistant_message = ChatMessage(
                id=assistant_msg_id,
                role=MessageRole.ASSISTANT,
                content=response,
            )
            self._add_message(assistant_message)

            # Update token counts
            stats = self._agent.get_token_stats()
            self._update_state(
                session_tokens=TokenUsage(
                    prompt_tokens=stats["session_prompt"],
                    completion_tokens=stats["session_completion"],
                ),
                last_tokens=TokenUsage(
                    prompt_tokens=stats["last_prompt"],
                    completion_tokens=stats["last_completion"],
                ),
            )
            await self._events.emit_async(EventType.TOKENS_UPDATED, stats)

            # Update cost
            cost_stats = self._agent.get_cost_stats()
            self._update_state(
                cost=CostInfo(
                    total_cost_usd=cost_stats.get("estimated_cost_usd", 0),
                    session_cost_usd=cost_stats.get("estimated_cost_usd", 0),
                    by_model=cost_stats.get("by_model", {}),
                )
            )
            await self._events.emit_async(EventType.COST_UPDATED, cost_stats)

            await self._events.emit_async(
                EventType.MESSAGE_COMPLETED,
                {
                    "message_id": assistant_msg_id,
                    "content": response,
                },
            )

            return response

        except Exception as e:
            error_msg = str(e)
            await self._events.emit_async(
                EventType.MESSAGE_ERROR,
                {
                    "error": error_msg,
                },
            )
            self._update_state(error=error_msg)
            return None

        finally:
            self._update_state(is_processing=False, is_thinking=False)

    # =========================================================================
    # Confirmation Handling
    # =========================================================================

    def approve_confirmation(self, confirmation_id: str) -> None:
        """Approve a pending confirmation request."""
        self._confirmation_results[confirmation_id] = True
        if confirmation_id in self._pending_confirmations:
            self._pending_confirmations[confirmation_id].set()

    def reject_confirmation(self, confirmation_id: str) -> None:
        """Reject a pending confirmation request."""
        self._confirmation_results[confirmation_id] = False
        if confirmation_id in self._pending_confirmations:
            self._pending_confirmations[confirmation_id].set()

    # =========================================================================
    # Configuration
    # =========================================================================

    def set_model(self, model: str) -> None:
        """Change the current model."""
        self._update_state(model=model)
        if self._agent:
            self._agent.model = model
        self._events.emit(EventType.MODEL_CHANGED, {"model": model})

    def set_auto_approve(self, enabled: bool) -> None:
        """Enable or disable auto-approve mode."""
        self._update_state(auto_approve=enabled)
        if self._agent:
            self._agent.set_auto_approve(enabled)
        self._events.emit(EventType.STATUS_CHANGED, {"auto_approve": enabled})

    def set_thinking_mode(self, enabled: bool) -> None:
        """Enable or disable thinking mode."""
        self._update_state(thinking_mode=enabled)
        if self._agent:
            self._agent.set_thinking_mode(enabled)
        self._events.emit(EventType.STATUS_CHANGED, {"thinking_mode": enabled})

    # =========================================================================
    # History Management
    # =========================================================================

    def clear_history(self) -> None:
        """Clear the message history."""
        self._update_state(messages=[])
        if self._agent:
            self._agent.clear_history()
        self._events.emit(EventType.HISTORY_CLEARED)

    async def save_session(self, name: str) -> Tuple[bool, str]:
        """
        Save the current session.

        Returns:
            Tuple of (success, message)
        """
        if not self._agent:
            return False, "Not connected"

        success, msg = self._agent.save_session(name)
        if success:
            await self._events.emit_async(EventType.SESSION_SAVED, {"name": name})
        return success, msg

    async def load_session(self, name: str) -> Tuple[bool, str]:
        """
        Load a saved session.

        Returns:
            Tuple of (success, message)
        """
        if not self._agent:
            return False, "Not connected"

        success, msg = self._agent.load_session(name)
        if success:
            # Sync state with agent
            self._update_state(
                messages=[],  # Will be rebuilt from agent history
                model=self._agent.model,
                auto_approve=self._agent.auto_approve,
                thinking_mode=self._agent.thinking_mode,
            )
            await self._events.emit_async(EventType.SESSION_LOADED, {"name": name})
        return success, msg

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all saved sessions."""
        if not self._agent:
            return []
        return self._agent.list_sessions()

    # =========================================================================
    # Tool Execution (for direct tool calls)
    # =========================================================================

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        skip_confirmation: bool = False,
    ) -> Tuple[str, bool]:
        """
        Execute a tool directly.

        Args:
            tool_name: Name of the tool to execute
            arguments: Tool arguments
            skip_confirmation: Whether to skip confirmation

        Returns:
            Tuple of (result, success)
        """
        if not self._agent:
            return "Not connected", False

        tool_id = str(uuid.uuid4())
        tool_call = ToolCallInfo(
            id=tool_id,
            name=tool_name,
            arguments=arguments,
            status=ToolStatus.RUNNING,
            started_at=datetime.now(),
        )

        await self._events.emit_async(EventType.TOOL_CALL_STARTED, {"tool_call": tool_call})

        try:
            confirmed = skip_confirmation or self._state.auto_approve
            result, needs_confirm = self._agent._execute_tool(
                tool_name, arguments, confirmed=confirmed
            )

            if needs_confirm and not confirmed:
                # Would need confirmation
                return "Confirmation required", False

            updated_call = tool_call.with_status(ToolStatus.SUCCESS, result=str(result))
            await self._events.emit_async(
                EventType.TOOL_CALL_COMPLETED,
                {
                    "tool_call": updated_call,
                    "result": result,
                },
            )
            return str(result), True

        except Exception as e:
            error_msg = str(e)
            updated_call = tool_call.with_status(ToolStatus.ERROR, error=error_msg)
            await self._events.emit_async(
                EventType.TOOL_CALL_ERROR,
                {
                    "tool_call": updated_call,
                    "error": error_msg,
                },
            )
            return error_msg, False

    # =========================================================================
    # State Management
    # =========================================================================

    def _update_state(self, **kwargs) -> None:
        """Update the agent state."""
        # Create new state with updated values
        self._state = AgentState(
            connection_status=kwargs.get("connection_status", self._state.connection_status),
            error=kwargs.get("error", self._state.error),
            model=kwargs.get("model", self._state.model),
            working_dir=kwargs.get("working_dir", self._state.working_dir),
            auto_approve=kwargs.get("auto_approve", self._state.auto_approve),
            thinking_mode=kwargs.get("thinking_mode", self._state.thinking_mode),
            stream_responses=kwargs.get("stream_responses", self._state.stream_responses),
            messages=kwargs.get("messages", self._state.messages),
            pending_confirmation=kwargs.get(
                "pending_confirmation", self._state.pending_confirmation
            ),
            is_processing=kwargs.get("is_processing", self._state.is_processing),
            is_thinking=kwargs.get("is_thinking", self._state.is_thinking),
            current_tool_calls=kwargs.get("current_tool_calls", self._state.current_tool_calls),
            session_tokens=kwargs.get("session_tokens", self._state.session_tokens),
            last_tokens=kwargs.get("last_tokens", self._state.last_tokens),
            cost=kwargs.get("cost", self._state.cost),
        )

    def _add_message(self, message: ChatMessage) -> None:
        """Add a message to the state."""
        self._update_state(messages=self._state.messages + [message])

    # =========================================================================
    # Utilities
    # =========================================================================

    def get_token_stats(self) -> Dict[str, int]:
        """Get token usage statistics."""
        return {
            "session_prompt": self._state.session_tokens.prompt_tokens,
            "session_completion": self._state.session_tokens.completion_tokens,
            "session_total": self._state.session_tokens.total,
            "last_prompt": self._state.last_tokens.prompt_tokens,
            "last_completion": self._state.last_tokens.completion_tokens,
            "last_total": self._state.last_tokens.total,
        }

    def get_cost_stats(self) -> Dict[str, Any]:
        """Get cost statistics."""
        return {
            "total_cost_usd": self._state.cost.total_cost_usd,
            "session_cost_usd": self._state.cost.session_cost_usd,
            "by_model": self._state.cost.by_model,
        }

    # =========================================================================
    # MCP (Model Context Protocol) Methods
    # =========================================================================

    async def init_mcp(self, configs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Initialize MCP servers.

        Args:
            configs: List of MCP server configurations

        Returns:
            Status dict with connected servers and tool counts
        """
        if not self._agent:
            return {"error": "Not connected", "connected": [], "failed": [], "total_tools": 0}

        await self._events.emit_async(EventType.MCP_SERVER_CONNECTING)

        try:
            results = self._agent.init_mcp(configs)

            for server_id in results.get("connected", []):
                await self._events.emit_async(
                    EventType.MCP_SERVER_CONNECTED,
                    {
                        "server_id": server_id,
                    },
                )

            for failure in results.get("failed", []):
                await self._events.emit_async(
                    EventType.MCP_SERVER_ERROR,
                    {
                        "server_id": failure,
                    },
                )

            await self._events.emit_async(
                EventType.MCP_TOOLS_UPDATED,
                {
                    "total_tools": results.get("total_tools", 0),
                },
            )

            return results

        except Exception as e:
            error_msg = str(e)
            await self._events.emit_async(
                EventType.MCP_SERVER_ERROR,
                {
                    "error": error_msg,
                },
            )
            return {"error": error_msg, "connected": [], "failed": [], "total_tools": 0}

    async def init_github_mcp(self, pat: str, toolsets: List[str] = None) -> Dict[str, Any]:
        """
        Initialize GitHub MCP server specifically.

        Args:
            pat: GitHub Personal Access Token
            toolsets: List of enabled toolsets (repos, issues, pull_requests, etc.)

        Returns:
            Status dict
        """
        from circuit_agent.mcp.servers.github import GitHubMCPServer

        if not self._agent:
            return {"error": "Not connected", "success": False, "tool_count": 0}

        await self._events.emit_async(
            EventType.MCP_SERVER_CONNECTING,
            {
                "server_id": "github",
            },
        )

        try:
            config = GitHubMCPServer.get_remote_config(
                pat=pat,
                toolsets=toolsets or [],
                enabled=True,
            )

            success = self._agent.mcp_manager.connect(config)

            if success:
                self._agent._mcp_tools_cache = self._agent.mcp_manager.list_tools()
                tool_count = len(self._agent._mcp_tools_cache)

                await self._events.emit_async(
                    EventType.MCP_SERVER_CONNECTED,
                    {
                        "server_id": "github",
                        "tool_count": tool_count,
                    },
                )

                await self._events.emit_async(
                    EventType.MCP_TOOLS_UPDATED,
                    {
                        "total_tools": tool_count,
                    },
                )

                return {"success": True, "tool_count": tool_count}
            else:
                await self._events.emit_async(
                    EventType.MCP_SERVER_ERROR,
                    {
                        "server_id": "github",
                        "error": "Connection failed",
                    },
                )
                return {"success": False, "error": "Connection failed", "tool_count": 0}

        except Exception as e:
            error_msg = str(e)
            await self._events.emit_async(
                EventType.MCP_SERVER_ERROR,
                {
                    "server_id": "github",
                    "error": error_msg,
                },
            )
            return {"success": False, "error": error_msg, "tool_count": 0}

    def disconnect_mcp(self, server_id: str = None) -> None:
        """
        Disconnect from MCP server(s).

        Args:
            server_id: Specific server to disconnect, or None for all
        """
        if not self._agent:
            return

        if server_id:
            self._agent.mcp_manager.disconnect(server_id)
            self._events.emit(
                EventType.MCP_SERVER_DISCONNECTED,
                {
                    "server_id": server_id,
                },
            )
        else:
            self._agent.mcp_manager.disconnect_all()
            self._events.emit(
                EventType.MCP_SERVER_DISCONNECTED,
                {
                    "server_id": "all",
                },
            )

        self._agent._mcp_tools_cache = self._agent.mcp_manager.list_tools()
        self._events.emit(
            EventType.MCP_TOOLS_UPDATED,
            {
                "total_tools": len(self._agent._mcp_tools_cache),
            },
        )

    def get_mcp_status(self) -> Dict[str, Any]:
        """Get MCP connection status."""
        if not self._agent:
            return {"connected_servers": 0, "total_tools": 0, "servers": {}}

        return self._agent.get_mcp_status()
