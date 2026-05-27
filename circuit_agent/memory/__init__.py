"""
Memory module for Circuit Agent v3.0.

Provides session persistence and context compaction.
"""

from .compaction import ContextCompactor
from .session import SessionManager

__all__ = [
    "SessionManager",
    "ContextCompactor",
]
