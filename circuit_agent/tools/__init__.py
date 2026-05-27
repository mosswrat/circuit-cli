"""
Tools package for Circuit Agent v3.0.

Provides modular tool implementations with parallel execution support.
"""

from .executor import BackupManager, ToolExecutor
from .file_tools import FILE_TOOLS, FileTools
from .git_tools import GIT_TOOLS, GitTools
from .web_tools import WEB_TOOLS, WebTools

# Combined tool definitions for the API
TOOLS = FILE_TOOLS + GIT_TOOLS + WEB_TOOLS

__all__ = [
    "ToolExecutor",
    "BackupManager",
    "FileTools",
    "GitTools",
    "WebTools",
    "TOOLS",
    "FILE_TOOLS",
    "GIT_TOOLS",
    "WEB_TOOLS",
]
