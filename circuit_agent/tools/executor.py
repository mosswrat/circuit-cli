"""
Tool executor with parallel execution support.
"""

import asyncio
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional, Tuple

from ..config import DANGEROUS_PATTERNS


class BackupManager:
    """Manages file backups for undo functionality."""

    def __init__(self, working_dir: str):
        self.working_dir = os.path.realpath(os.path.abspath(working_dir))
        self.backups: Dict[str, List[Dict[str, Any]]] = {}
        self.last_modified: Optional[str] = None

    def _safe_path(self, path: str) -> str:
        """Validate and return safe absolute path within working directory.

        Raises ValueError if path would escape the working directory.
        """
        # Normalize and resolve the path
        full_path = os.path.normpath(os.path.join(self.working_dir, path))
        real_path = os.path.realpath(full_path)

        # Ensure the resolved path is within working directory
        if not real_path.startswith(self.working_dir + os.sep) and real_path != self.working_dir:
            raise ValueError(f"Path traversal detected: {path} resolves outside working directory")

        return real_path

    def backup(self, path: str) -> bool:
        """Backup a file before modification. Returns True if backup was created."""
        try:
            full_path = self._safe_path(path)
        except ValueError:
            return False

        if not os.path.exists(full_path):
            if path not in self.backups:
                self.backups[path] = []
            self.backups[path].append(
                {
                    "content": None,
                    "timestamp": time.time(),
                }
            )
            self.last_modified = path
            return True

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            if path not in self.backups:
                self.backups[path] = []

            if len(self.backups[path]) >= 10:
                self.backups[path].pop(0)

            self.backups[path].append(
                {
                    "content": content,
                    "timestamp": time.time(),
                }
            )
            self.last_modified = path
            return True
        except Exception:
            return False

    def get_backup(self, path: str) -> Optional[str]:
        """Get the most recent backup content for a file."""
        if path in self.backups and self.backups[path]:
            return self.backups[path][-1]["content"]
        return None

    def get_last_modified(self) -> Optional[str]:
        """Get the path of the last modified file."""
        return self.last_modified

    def list_backups(self) -> Dict[str, int]:
        """List all files with backups and their count."""
        return {path: len(backups) for path, backups in self.backups.items()}

    def restore(self, path: str) -> Tuple[bool, str]:
        """Restore a file from backup. Returns (success, message)."""
        if path not in self.backups or not self.backups[path]:
            return False, f"No backup found for {path}"

        # Validate path to prevent traversal attacks
        try:
            full_path = self._safe_path(path)
        except ValueError as e:
            return False, f"Security error: {e}"

        backup_content = self.backups[path][-1]["content"]

        try:
            if backup_content is None:
                if os.path.exists(full_path):
                    os.remove(full_path)
                    self.backups[path].pop()
                    return True, f"Deleted {path} (file was newly created)"
            else:
                with open(full_path, "w", encoding="utf-8") as f:
                    f.write(backup_content)
                self.backups[path].pop()
                return True, f"Restored {path} from backup"
        except Exception as e:
            return False, f"Failed to restore: {e}"


class ToolExecutor:
    """Executes tools with parallel execution support."""

    def __init__(self, working_dir: str):
        self.working_dir = os.path.realpath(os.path.abspath(working_dir))
        self.backup_manager = BackupManager(working_dir)
        self._thread_pool = ThreadPoolExecutor(max_workers=8)

        # Tool implementations will be registered here
        self._tools: Dict[str, Callable] = {}

    def register_tool(self, name: str, func: Callable):
        """Register a tool implementation."""
        self._tools[name] = func

    def _safe_path(self, path: str) -> str:
        """Ensure path is within working directory (prevents path traversal attacks)."""
        full_path = os.path.normpath(os.path.join(self.working_dir, path))
        real_path = os.path.realpath(full_path)
        if not (real_path == self.working_dir or real_path.startswith(self.working_dir + os.sep)):
            raise ValueError(f"Path '{path}' is outside working directory")
        return full_path

    def _is_dangerous_command(self, command: str) -> bool:
        """Check if command matches dangerous patterns."""
        for pattern in DANGEROUS_PATTERNS:
            if re.search(pattern, command, re.IGNORECASE):
                return True
        return False

    async def execute(
        self, tool_name: str, arguments: dict, confirmed: bool = False
    ) -> Tuple[Any, bool]:
        """
        Execute a single tool.
        Returns (result, needs_confirmation).
        """
        if tool_name not in self._tools:
            return f"Unknown tool: {tool_name}", False

        tool_func = self._tools[tool_name]

        # Run in thread pool to avoid blocking
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            self._thread_pool, lambda: tool_func(arguments, confirmed)
        )
        return result

    async def execute_parallel(self, calls: List[Tuple[str, dict, bool]]) -> List[Tuple[Any, bool]]:
        """
        Execute multiple tools in parallel.

        Args:
            calls: List of (tool_name, arguments, confirmed) tuples

        Returns:
            List of (result, needs_confirmation) tuples in same order
        """
        tasks = [self.execute(name, args, confirmed) for name, args, confirmed in calls]
        return await asyncio.gather(*tasks)

    async def execute_batch_reads(self, paths: List[str]) -> Dict[str, str]:
        """
        Read multiple files in parallel.

        Args:
            paths: List of file paths to read

        Returns:
            Dict mapping path to content (or error message)
        """
        calls = [("read_file", {"path": p}, False) for p in paths]
        results = await self.execute_parallel(calls)
        return {path: result[0] for path, result in zip(paths, results, strict=False)}

    async def execute_batch_searches(self, searches: List[Tuple[str, str]]) -> Dict[str, str]:
        """
        Run multiple searches in parallel.

        Args:
            searches: List of (pattern, file_pattern) tuples

        Returns:
            Dict mapping pattern to results
        """
        calls = [("search_files", {"pattern": p, "file_pattern": fp}, False) for p, fp in searches]
        results = await self.execute_parallel(calls)
        return {pattern: result[0] for (pattern, _), result in zip(searches, results, strict=False)}

    def shutdown(self):
        """Shutdown the thread pool."""
        self._thread_pool.shutdown(wait=False)
