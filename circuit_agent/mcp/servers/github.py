"""
GitHub MCP Server configuration.

Provides configuration for connecting to GitHub's MCP server,
which offers 100+ tools for GitHub operations.

See: https://github.com/github/github-mcp-server
"""

from dataclasses import dataclass
from typing import List, Optional

from ..config import MCPServerConfig, MCPTransportType


@dataclass
class GitHubToolset:
    """Definition of a GitHub MCP toolset."""

    id: str
    name: str
    description: str


class GitHubMCPServer:
    """
    GitHub MCP Server configuration and management.

    GitHub provides two ways to connect:
    1. Remote: https://api.githubcopilot.com/mcp/ (recommended)
    2. Docker: ghcr.io/github/github-mcp-server

    The remote option is simpler and doesn't require Docker.
    """

    # Server identifiers
    SERVER_ID = "github"
    SERVER_NAME = "GitHub"

    # Remote server URL
    REMOTE_URL = "https://api.githubcopilot.com/mcp/"

    # Docker image
    DOCKER_IMAGE = "ghcr.io/github/github-mcp-server"

    # Available toolsets
    TOOLSETS = [
        GitHubToolset(
            id="repos", name="Repositories", description="Create, manage, and search repositories"
        ),
        GitHubToolset(
            id="issues", name="Issues", description="Create, update, search, and manage issues"
        ),
        GitHubToolset(
            id="pull_requests",
            name="Pull Requests",
            description="Create, review, merge, and manage PRs",
        ),
        GitHubToolset(
            id="actions", name="Actions", description="Manage GitHub Actions workflows and runs"
        ),
        GitHubToolset(
            id="code_security",
            name="Code Security",
            description="Code scanning, secret scanning, Dependabot",
        ),
        GitHubToolset(
            id="users", name="Users", description="User profile and organization management"
        ),
        GitHubToolset(
            id="discussions", name="Discussions", description="GitHub Discussions management"
        ),
    ]

    # Default enabled toolsets
    DEFAULT_TOOLSETS = ["repos", "issues", "pull_requests", "actions"]

    @classmethod
    def get_toolset_ids(cls) -> List[str]:
        """Get list of all toolset IDs."""
        return [t.id for t in cls.TOOLSETS]

    @classmethod
    def get_remote_config(
        cls,
        pat: str,
        toolsets: Optional[List[str]] = None,
        enabled: bool = True,
    ) -> MCPServerConfig:
        """
        Get configuration for GitHub remote MCP server.

        Args:
            pat: GitHub Personal Access Token
            toolsets: List of enabled toolsets (None = all)
            enabled: Whether the server is enabled

        Returns:
            MCPServerConfig for the GitHub remote server
        """
        return MCPServerConfig(
            id=cls.SERVER_ID,
            name=cls.SERVER_NAME,
            transport=MCPTransportType.HTTP,
            enabled=enabled,
            url=cls.REMOTE_URL,
            auth_token=pat,
            toolsets=toolsets or [],
            timeout=30,
        )

    @classmethod
    def get_docker_config(
        cls,
        pat: str,
        toolsets: Optional[List[str]] = None,
        enabled: bool = True,
    ) -> MCPServerConfig:
        """
        Get configuration for GitHub Docker MCP server.

        Note: Docker transport is not yet implemented.
        Use remote server for now.

        Args:
            pat: GitHub Personal Access Token
            toolsets: List of enabled toolsets (None = all)
            enabled: Whether the server is enabled

        Returns:
            MCPServerConfig for the GitHub Docker server
        """
        return MCPServerConfig(
            id=cls.SERVER_ID,
            name=cls.SERVER_NAME,
            transport=MCPTransportType.STDIO,
            enabled=enabled,
            docker_image=cls.DOCKER_IMAGE,
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": pat},
            toolsets=toolsets or [],
            timeout=30,
        )

    @classmethod
    def get_required_pat_scopes(cls, toolsets: Optional[List[str]] = None) -> List[str]:
        """
        Get required GitHub PAT scopes for the specified toolsets.

        Args:
            toolsets: List of toolsets to check (None = all)

        Returns:
            List of required OAuth scopes
        """
        if toolsets is None:
            toolsets = cls.get_toolset_ids()

        scopes = set()

        # Base scope for all operations
        scopes.add("repo")

        if "issues" in toolsets:
            scopes.add("write:discussion")

        if "pull_requests" in toolsets:
            scopes.add("repo")

        if "actions" in toolsets:
            scopes.add("workflow")

        if "code_security" in toolsets:
            scopes.add("security_events")

        if "users" in toolsets:
            scopes.add("read:user")
            scopes.add("read:org")

        if "discussions" in toolsets:
            scopes.add("write:discussion")

        return sorted(scopes)

    @classmethod
    def validate_pat(cls, pat: str) -> bool:
        """
        Basic validation of a GitHub PAT format.

        Args:
            pat: Token to validate

        Returns:
            True if format looks valid
        """
        if not pat:
            return False

        # Classic tokens start with ghp_
        # Fine-grained tokens start with github_pat_
        valid_prefixes = ("ghp_", "github_pat_", "gho_", "ghs_", "ghr_")

        return any(pat.startswith(prefix) for prefix in valid_prefixes)
