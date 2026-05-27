"""
Transport layer for MCP server communication.

Supports HTTP/SSE transport for remote MCP servers using JSON-RPC 2.0.
"""

import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx

logger = logging.getLogger(__name__)


class MCPTransportError(Exception):
    """Error in MCP transport layer."""

    pass


class MCPRPCError(Exception):
    """JSON-RPC error from MCP server."""

    def __init__(self, code: int, message: str, data: Any = None):
        self.code = code
        self.message = message
        self.data = data
        super().__init__(f"MCP RPC Error {code}: {message}")


@dataclass
class MCPRequest:
    """JSON-RPC 2.0 request."""

    method: str
    params: Optional[Dict[str, Any]] = None
    id: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        request = {
            "jsonrpc": "2.0",
            "method": self.method,
        }
        if self.params is not None:
            request["params"] = self.params
        if self.id is not None:
            request["id"] = self.id
        return request


@dataclass
class MCPResponse:
    """JSON-RPC 2.0 response."""

    id: Optional[int]
    result: Optional[Any] = None
    error: Optional[Dict[str, Any]] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MCPResponse":
        return cls(
            id=data.get("id"),
            result=data.get("result"),
            error=data.get("error"),
        )

    def raise_for_error(self):
        """Raise exception if response contains an error."""
        if self.error:
            raise MCPRPCError(
                code=self.error.get("code", -1),
                message=self.error.get("message", "Unknown error"),
                data=self.error.get("data"),
            )


class HTTPSSETransport:
    """
    HTTP/SSE transport for remote MCP servers.

    Implements the JSON-RPC 2.0 protocol over HTTP POST requests.
    Handles authentication and session management.
    """

    def __init__(
        self,
        url: str,
        auth_token: Optional[str] = None,
        timeout: int = 30,
    ):
        self.url = url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None
        self._request_id = 0
        self._session_id: Optional[str] = None
        self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _next_id(self) -> int:
        """Get next request ID."""
        self._request_id += 1
        return self._request_id

    async def connect(self) -> Dict[str, Any]:
        """
        Connect to the MCP server and initialize the session.

        Returns server capabilities.
        """
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)

        # Send initialize request
        result = await self.send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {"listChanged": True},
                    "sampling": {},
                },
                "clientInfo": {
                    "name": "Circuit IDE",
                    "version": "1.0.0",
                },
            },
        )

        self._connected = True
        logger.info(f"Connected to MCP server: {self.url}")

        # Send initialized notification
        await self.notify("notifications/initialized", {})

        return result

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        Send a JSON-RPC request and wait for response.

        Args:
            method: RPC method name
            params: Method parameters

        Returns:
            Result from the RPC response

        Raises:
            MCPTransportError: On transport failure
            MCPRPCError: On RPC error response
        """
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)

        request = MCPRequest(
            method=method,
            params=params,
            id=self._next_id(),
        )

        try:
            response = self._client.post(
                self.url,
                json=request.to_dict(),
                headers=self._get_headers(),
            )
            response.raise_for_status()

            # Check for session ID in response headers
            if "Mcp-Session-Id" in response.headers:
                self._session_id = response.headers["Mcp-Session-Id"]

            data = response.json()
            rpc_response = MCPResponse.from_dict(data)
            rpc_response.raise_for_error()

            return rpc_response.result

        except httpx.HTTPStatusError as e:
            raise MCPTransportError(f"HTTP error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise MCPTransportError(f"Request failed: {e}") from e
        except json.JSONDecodeError as e:
            raise MCPTransportError(f"Invalid JSON response: {e}") from e

    async def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """
        Send a JSON-RPC notification (no response expected).
        """
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)

        request = MCPRequest(method=method, params=params)

        try:
            response = self._client.post(
                self.url,
                json=request.to_dict(),
                headers=self._get_headers(),
            )
            response.raise_for_status()
        except httpx.RequestError as e:
            logger.warning(f"Notification failed: {e}")

    async def close(self) -> None:
        """Close the transport connection."""
        if self._client:
            self._client.close()
            self._client = None
        self._connected = False
        self._session_id = None
        logger.info("MCP transport closed")


class SyncHTTPTransport:
    """
    Synchronous HTTP transport for MCP servers.

    Used when async is not needed or available.
    """

    def __init__(
        self,
        url: str,
        auth_token: Optional[str] = None,
        timeout: int = 30,
    ):
        self.url = url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout
        self._client: Optional[httpx.Client] = None
        self._request_id = 0
        self._session_id: Optional[str] = None
        self._connected = False
        self._server_info: Optional[Dict[str, Any]] = None

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def server_info(self) -> Optional[Dict[str, Any]]:
        return self._server_info

    def _get_headers(self) -> Dict[str, str]:
        """Get request headers."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _next_id(self) -> int:
        """Get next request ID."""
        self._request_id += 1
        return self._request_id

    def connect(self) -> Dict[str, Any]:
        """
        Connect to the MCP server and initialize the session.

        Returns server capabilities.
        """
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)

        # Send initialize request
        result = self.send(
            "initialize",
            {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "roots": {"listChanged": True},
                    "sampling": {},
                },
                "clientInfo": {
                    "name": "Circuit IDE",
                    "version": "1.0.0",
                },
            },
        )

        self._server_info = result
        self._connected = True
        logger.info(f"Connected to MCP server: {self.url}")

        # Send initialized notification
        self.notify("notifications/initialized", {})

        return result

    def send(self, method: str, params: Optional[Dict[str, Any]] = None) -> Any:
        """
        Send a JSON-RPC request and wait for response.
        """
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)

        request = MCPRequest(
            method=method,
            params=params,
            id=self._next_id(),
        )

        try:
            response = self._client.post(
                self.url,
                json=request.to_dict(),
                headers=self._get_headers(),
            )
            response.raise_for_status()

            # Check for session ID in response headers
            if "Mcp-Session-Id" in response.headers:
                self._session_id = response.headers["Mcp-Session-Id"]

            data = response.json()
            rpc_response = MCPResponse.from_dict(data)
            rpc_response.raise_for_error()

            return rpc_response.result

        except httpx.HTTPStatusError as e:
            raise MCPTransportError(f"HTTP error: {e.response.status_code}") from e
        except httpx.RequestError as e:
            raise MCPTransportError(f"Request failed: {e}") from e
        except json.JSONDecodeError as e:
            raise MCPTransportError(f"Invalid JSON response: {e}") from e

    def notify(self, method: str, params: Optional[Dict[str, Any]] = None) -> None:
        """
        Send a JSON-RPC notification (no response expected).
        """
        if self._client is None:
            self._client = httpx.Client(timeout=self.timeout)

        request = MCPRequest(method=method, params=params)

        try:
            response = self._client.post(
                self.url,
                json=request.to_dict(),
                headers=self._get_headers(),
            )
            response.raise_for_status()
        except httpx.RequestError as e:
            logger.warning(f"Notification failed: {e}")

    def close(self) -> None:
        """Close the transport connection."""
        if self._client:
            self._client.close()
            self._client = None
        self._connected = False
        self._session_id = None
        self._server_info = None
        logger.info("MCP transport closed")
