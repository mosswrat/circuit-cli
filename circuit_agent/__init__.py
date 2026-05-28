"""
Circuit Agent v5.0 - AI-Powered Coding Assistant

A Cisco Circuit-powered coding assistant that works like Claude Code:
reads files, writes code, runs commands, searches the web, and helps
with software engineering tasks in your project directory.

New in v5.0:
- Terminal IDE: Purpose-built visual environment for AI coding
- Smart context management for long conversations
- Improved error messages with suggestions

Previous (v4.0):
- Parallel tool execution for faster multi-file operations
- Secret detection and warnings
- Audit logging for all actions
- Cost tracking per session
- Thinking mode for reasoning display
- Headless/CI mode support
"""

# Read version from the installed package metadata so we never drift
# between this string and pyproject.toml. Falls back to a dev marker if
# the package isn't installed (running from a source checkout).
from importlib.metadata import PackageNotFoundError, version as _pkg_version

try:
    __version__ = _pkg_version("circuit-agent")
except PackageNotFoundError:
    __version__ = "0.0.0-dev"

__author__ = "Circuit Agent"

from .agent import CircuitAgent
from .cli import main, run_cli
from .config import (
    MODELS,
    delete_credentials,
    load_circuit_md,
    load_credentials,
    save_credentials,
)
from .memory import ContextCompactor, SessionManager
from .security import AuditLogger, CostTracker, SecretDetector
from .tools import TOOLS, BackupManager, FileTools, GitTools, WebTools

__all__ = [
    "CircuitAgent",
    "FileTools",
    "GitTools",
    "WebTools",
    "BackupManager",
    "SessionManager",
    "ContextCompactor",
    "SecretDetector",
    "AuditLogger",
    "CostTracker",
    "TOOLS",
    "load_credentials",
    "save_credentials",
    "delete_credentials",
    "load_circuit_md",
    "MODELS",
    "run_cli",
    "main",
    "__version__",
]
