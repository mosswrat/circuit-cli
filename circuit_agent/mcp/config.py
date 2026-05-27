"""
MCP Server configuration dataclasses.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional


class MCPTransportType(Enum):
    """Transport type for MCP server connection."""

    HTTP = "http"
    STDIO = "stdio"
    SSE = "sse"


@dataclass
class MCPServerConfig:
    """Configuration for an MCP server connection."""

    # Unique identifier for this server
    id: str

    # Human-readable name
    name: str

    # Transport type
    transport: MCPTransportType = MCPTransportType.HTTP

    # Whether this server is enabled
    enabled: bool = True

    # Remote server URL (for HTTP/SSE transport)
    url: Optional[str] = None

    # Authentication token (for remote servers)
    auth_token: Optional[str] = None

    # Docker image (for stdio/docker transport)
    docker_image: Optional[str] = None

    # Command to run (for stdio transport)
    command: Optional[List[str]] = None

    # Environment variables
    env: dict = field(default_factory=dict)

    # Enabled toolsets (empty = all)
    toolsets: List[str] = field(default_factory=list)

    # Request timeout in seconds
    timeout: int = 30

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "id": self.id,
            "name": self.name,
            "transport": self.transport.value,
            "enabled": self.enabled,
            "url": self.url,
            "auth_token": self.auth_token,
            "docker_image": self.docker_image,
            "command": self.command,
            "env": self.env,
            "toolsets": self.toolsets,
            "timeout": self.timeout,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MCPServerConfig":
        """Create from dictionary."""
        transport = data.get("transport", "http")
        if isinstance(transport, str):
            transport = MCPTransportType(transport)

        return cls(
            id=data["id"],
            name=data["name"],
            transport=transport,
            enabled=data.get("enabled", True),
            url=data.get("url"),
            auth_token=data.get("auth_token"),
            docker_image=data.get("docker_image"),
            command=data.get("command"),
            env=data.get("env", {}),
            toolsets=data.get("toolsets", []),
            timeout=data.get("timeout", 30),
        )


@dataclass
class MCPTool:
    """Representation of a tool from an MCP server."""

    # Tool name
    name: str

    # Human-readable description
    description: str

    # JSON Schema for input parameters
    input_schema: dict

    # Server ID this tool belongs to
    server_id: str

    # Original MCP tool definition
    raw: dict = field(default_factory=dict)

    def to_openai_format(self) -> dict:
        """Convert to OpenAI function calling format."""
        from .converter import mcp_to_openai

        return mcp_to_openai(self.raw)
