"""
MCP Client Manager for Circuit IDE.

Manages connections to multiple MCP servers and provides a unified
interface for tool discovery and execution.
"""

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .config import MCPServerConfig, MCPTransportType
from .converter import filter_tools_by_toolset, mcp_to_openai
from .transport import MCPRPCError, MCPTransportError, SyncHTTPTransport

logger = logging.getLogger(__name__)


@dataclass
class MCPServerConnection:
    """Active connection to an MCP server."""

    config: MCPServerConfig
    transport: SyncHTTPTransport
    tools: List[Dict[str, Any]] = field(default_factory=list)
    server_info: Optional[Dict[str, Any]] = None

    @property
    def is_connected(self) -> bool:
        return self.transport.is_connected

    @property
    def tool_count(self) -> int:
        return len(self.tools)


class MCPClientManager:
    """
    Manages connections to multiple MCP servers.

    Provides:
    - Connection lifecycle management
    - Tool discovery and aggregation
    - Tool execution routing
    - Event callbacks for UI updates
    """

    def __init__(self):
        self._connections: Dict[str, MCPServerConnection] = {}
        self._tool_to_server: Dict[str, str] = {}  # tool_name -> server_id

        # Callbacks
        self._on_connected: Optional[Callable[[str, int], None]] = None
        self._on_disconnected: Optional[Callable[[str], None]] = None
        self._on_error: Optional[Callable[[str, str], None]] = None

    def set_callbacks(
        self,
        on_connected: Optional[Callable[[str, int], None]] = None,
        on_disconnected: Optional[Callable[[str], None]] = None,
        on_error: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        """Set event callbacks for connection status changes."""
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected
        self._on_error = on_error

    def connect(self, config: MCPServerConfig) -> bool:
        """
        Connect to an MCP server.

        Args:
            config: Server configuration

        Returns:
            True if connection successful
        """
        if not config.enabled:
            logger.debug(f"Server {config.id} is disabled, skipping")
            return False

        if config.id in self._connections:
            logger.warning(f"Already connected to {config.id}")
            return True

        try:
            # Create transport based on type
            if config.transport == MCPTransportType.HTTP:
                if not config.url:
                    raise ValueError("URL required for HTTP transport")

                transport = SyncHTTPTransport(
                    url=config.url,
                    auth_token=config.auth_token,
                    timeout=config.timeout,
                )
            else:
                raise ValueError(f"Unsupported transport: {config.transport}")

            # Connect and initialize
            server_info = transport.connect()

            # Discover tools
            tools_result = transport.send("tools/list", {})
            raw_tools = tools_result.get("tools", [])

            # Filter by enabled toolsets
            filtered_tools = filter_tools_by_toolset(raw_tools, config.toolsets)

            # Create connection
            connection = MCPServerConnection(
                config=config,
                transport=transport,
                tools=filtered_tools,
                server_info=server_info,
            )

            self._connections[config.id] = connection

            # Map tools to this server
            for tool in filtered_tools:
                tool_name = tool.get("name", "")
                if tool_name:
                    # Prefix with server ID to avoid collisions
                    prefixed_name = f"mcp_{config.id}_{tool_name}"
                    self._tool_to_server[prefixed_name] = config.id
                    # Also map unprefixed for convenience
                    self._tool_to_server[tool_name] = config.id

            logger.info(
                f"Connected to MCP server {config.name} ({config.id}): "
                f"{len(filtered_tools)} tools available"
            )

            if self._on_connected:
                self._on_connected(config.id, len(filtered_tools))

            return True

        except (MCPTransportError, MCPRPCError) as e:
            error_msg = str(e)
            logger.error(f"Failed to connect to {config.id}: {error_msg}")
            if self._on_error:
                self._on_error(config.id, error_msg)
            return False
        except Exception as e:
            error_msg = str(e)
            logger.exception(f"Unexpected error connecting to {config.id}")
            if self._on_error:
                self._on_error(config.id, error_msg)
            return False

    def disconnect(self, server_id: str) -> None:
        """Disconnect from an MCP server."""
        if server_id not in self._connections:
            return

        connection = self._connections[server_id]

        try:
            connection.transport.close()
        except Exception as e:
            logger.warning(f"Error closing transport: {e}")

        # Remove tool mappings
        tools_to_remove = [name for name, sid in self._tool_to_server.items() if sid == server_id]
        for name in tools_to_remove:
            del self._tool_to_server[name]

        del self._connections[server_id]

        logger.info(f"Disconnected from MCP server {server_id}")

        if self._on_disconnected:
            self._on_disconnected(server_id)

    def disconnect_all(self) -> None:
        """Disconnect from all MCP servers."""
        server_ids = list(self._connections.keys())
        for server_id in server_ids:
            self.disconnect(server_id)

    def is_connected(self, server_id: str) -> bool:
        """Check if a server is connected."""
        return server_id in self._connections and self._connections[server_id].is_connected

    def get_connection(self, server_id: str) -> Optional[MCPServerConnection]:
        """Get connection info for a server."""
        return self._connections.get(server_id)

    def list_tools(self, format: str = "openai") -> List[Dict[str, Any]]:
        """
        Get all available tools from connected servers.

        Args:
            format: "openai" for OpenAI function format, "mcp" for raw MCP format

        Returns:
            List of tool definitions
        """
        all_tools = []

        for server_id, connection in self._connections.items():
            for tool in connection.tools:
                if format == "openai":
                    openai_tool = mcp_to_openai(tool)
                    # Add server prefix to name for uniqueness
                    openai_tool["function"]["name"] = f"mcp_{server_id}_{tool['name']}"
                    all_tools.append(openai_tool)
                else:
                    # Add server ID to raw tool
                    tool_copy = tool.copy()
                    tool_copy["_server_id"] = server_id
                    all_tools.append(tool_copy)

        return all_tools

    def has_tool(self, tool_name: str) -> bool:
        """Check if a tool is available."""
        return tool_name in self._tool_to_server

    def get_tool_server(self, tool_name: str) -> Optional[str]:
        """Get the server ID that provides a tool."""
        return self._tool_to_server.get(tool_name)

    def execute_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """
        Execute a tool on the appropriate MCP server.

        Args:
            tool_name: Name of the tool (may include mcp_serverid_ prefix)
            arguments: Tool arguments

        Returns:
            Tool execution result

        Raises:
            ValueError: If tool not found
            MCPTransportError: On transport failure
            MCPRPCError: On RPC error
        """
        # Handle prefixed tool names (mcp_github_repos_list -> repos_list on github)
        original_name = tool_name
        server_id = None

        if tool_name.startswith("mcp_"):
            parts = tool_name.split("_", 2)  # ["mcp", "serverid", "actual_name"]
            if len(parts) >= 3:
                server_id = parts[1]
                tool_name = parts[2]

        # Find the server
        if server_id is None:
            server_id = self._tool_to_server.get(tool_name)

        if server_id is None:
            raise ValueError(f"Tool not found: {original_name}")

        if server_id not in self._connections:
            raise ValueError(f"Server not connected: {server_id}")

        connection = self._connections[server_id]

        # Execute the tool
        logger.info(f"Executing MCP tool {tool_name} on {server_id}")

        result = connection.transport.send(
            "tools/call",
            {
                "name": tool_name,
                "arguments": arguments,
            },
        )

        return result

    def get_status(self) -> Dict[str, Any]:
        """Get overall MCP status."""
        return {
            "connected_servers": len(self._connections),
            "total_tools": len(self._tool_to_server),
            "servers": {
                sid: {
                    "name": conn.config.name,
                    "connected": conn.is_connected,
                    "tool_count": conn.tool_count,
                }
                for sid, conn in self._connections.items()
            },
        }
