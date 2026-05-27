"""
Tool definitions and executor for Circuit Agent.
"""

import os
import re
import shlex
import subprocess
import time
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .config import DANGEROUS_PATTERNS

# Shell metacharacters that require shell=True
SHELL_METACHARACTERS = set("|;&$`><(){}[]!*?~")


def _needs_shell(command: str) -> bool:
    """Check if command contains shell metacharacters requiring shell=True."""
    for char in command:
        if char in SHELL_METACHARACTERS:
            return True
    return False


def _sanitize_command(command: str) -> str:
    """Sanitize command string to prevent injection attacks."""
    dangerous_subst = [
        r"\$\([^)]+\)",  # $(command)
        r"`[^`]+`",  # `command`
        r"\$\{[^}]+\}",  # ${variable} expansion
    ]
    for pattern in dangerous_subst:
        if re.search(pattern, command):
            raise ValueError("Command substitution not allowed for security reasons")
    return command


# Tool definitions in OpenAI function calling format
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read the contents of a file. Returns file contents with line numbers. Use this before editing any file.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the working directory",
                    },
                    "start_line": {
                        "type": "integer",
                        "description": "Optional: Start reading from this line number (1-indexed)",
                    },
                    "end_line": {
                        "type": "integer",
                        "description": "Optional: Stop reading at this line number (inclusive)",
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "Create a new file or overwrite an existing file with the given content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the working directory",
                    },
                    "content": {
                        "type": "string",
                        "description": "The content to write to the file",
                    },
                },
                "required": ["path", "content"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "Make a targeted edit to a file by replacing specific text. The old_text must match exactly (including whitespace/indentation).",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "File path relative to the working directory",
                    },
                    "old_text": {
                        "type": "string",
                        "description": "The exact text to find and replace (must match exactly including whitespace)",
                    },
                    "new_text": {"type": "string", "description": "The text to replace it with"},
                },
                "required": ["path", "old_text", "new_text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files matching a glob pattern. Use '**/*.py' for recursive search, '*.js' for current dir only.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob pattern (e.g., '**/*.py', 'src/**/*.ts', '*.json')",
                    }
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Search for a regex pattern in files. Returns matching lines with file paths and line numbers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Regex pattern to search for"},
                    "file_pattern": {
                        "type": "string",
                        "description": "Optional glob pattern to filter files (e.g., '**/*.py'). Defaults to all files.",
                        "default": "**/*",
                    },
                    "case_sensitive": {
                        "type": "boolean",
                        "description": "Whether search is case-sensitive. Defaults to false.",
                        "default": False,
                    },
                },
                "required": ["pattern"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "run_command",
            "description": "Execute a shell command in the working directory. Use for running tests, builds, scripts, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "The shell command to execute"},
                    "timeout": {
                        "type": "integer",
                        "description": "Timeout in seconds (default 60, max 300)",
                        "default": 60,
                    },
                },
                "required": ["command"],
            },
        },
    },
    # Git-specific tools
    {
        "type": "function",
        "function": {
            "name": "git_status",
            "description": "Show the working tree status. Returns staged, unstaged, and untracked files.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_diff",
            "description": "Show changes between commits, commit and working tree, etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Optional: Limit diff to specific file or directory",
                    },
                    "staged": {
                        "type": "boolean",
                        "description": "If true, show staged changes (--cached). Default false.",
                        "default": False,
                    },
                    "commit": {
                        "type": "string",
                        "description": "Optional: Compare against specific commit (e.g., 'HEAD~1', 'main')",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_log",
            "description": "Show commit history.",
            "parameters": {
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "description": "Number of commits to show (default 10, max 50)",
                        "default": 10,
                    },
                    "path": {
                        "type": "string",
                        "description": "Optional: Show history for specific file",
                    },
                    "oneline": {
                        "type": "boolean",
                        "description": "If true, show condensed output. Default true.",
                        "default": True,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_commit",
            "description": "Stage files and create a commit. Will stage all modified/new files listed, then commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "Commit message"},
                    "files": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Optional: Specific files to stage. If empty, stages all changes.",
                    },
                },
                "required": ["message"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "git_branch",
            "description": "List, create, or switch branches.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["list", "create", "switch", "delete"],
                        "description": "Action to perform. Default 'list'.",
                        "default": "list",
                    },
                    "name": {
                        "type": "string",
                        "description": "Branch name (required for create/switch/delete)",
                    },
                },
                "required": [],
            },
        },
    },
]


class BackupManager:
    """Manages file backups for undo functionality."""

    def __init__(self, working_dir: str):
        self.working_dir = working_dir
        self.backups: Dict[str, List[Dict[str, Any]]] = {}
        self.last_modified: Optional[str] = None

    def backup(self, path: str) -> bool:
        """Backup a file before modification. Returns True if backup was created."""
        full_path = os.path.join(self.working_dir, path)
        if not os.path.exists(full_path):
            # Track new files with empty backup
            if path not in self.backups:
                self.backups[path] = []
            self.backups[path].append(
                {
                    "content": None,  # None means file didn't exist
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

        backup_content = self.backups[path][-1]["content"]
        full_path = os.path.join(self.working_dir, path)

        try:
            if backup_content is None:
                # File didn't exist before, delete it
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
    """Executes tools for the agent."""

    def __init__(self, working_dir: str):
        self.working_dir = os.path.realpath(os.path.abspath(working_dir))
        self.backup_manager = BackupManager(working_dir)

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

    def _run_git(self, args: List[str], timeout: int = 30) -> Tuple[bool, str]:
        """Run a git command and return (success, output)."""
        try:
            result = subprocess.run(
                ["git"] + args,
                cwd=self.working_dir,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            output = result.stdout
            if result.stderr:
                output += ("\n" if output else "") + result.stderr
            return result.returncode == 0, output.strip() or "(no output)"
        except subprocess.TimeoutExpired:
            return False, f"Git command timed out after {timeout}s"
        except FileNotFoundError:
            return False, "Git is not installed or not in PATH"
        except Exception as e:
            return False, f"Git error: {e}"

    def _find_similar_text(self, content: str, search_text: str, n: int = 3) -> List[str]:
        """Find similar lines to the search text for better error messages."""
        content_lines = content.split("\n")
        search_lines = search_text.strip().split("\n")

        if not search_lines:
            return []

        # Look for lines similar to the first line of search text
        first_search_line = search_lines[0].strip()
        if not first_search_line:
            return []

        stripped_lines = [line.strip() for line in content_lines]
        matches = get_close_matches(first_search_line, stripped_lines, n=n, cutoff=0.6)

        # Return with original indentation
        results = []
        for match in matches:
            for i, stripped in enumerate(stripped_lines):
                if stripped == match and content_lines[i] not in results:
                    results.append(f"Line {i + 1}: {content_lines[i][:80]}")
                    break
        return results

    def read_file(self, path: str, start_line: int = None, end_line: int = None) -> str:
        """Read file contents with line numbers."""
        try:
            full_path = self._safe_path(path)
        except ValueError as e:
            return f"Error: {e}"

        if not os.path.exists(full_path):
            # Try to suggest similar files
            parent = os.path.dirname(full_path) or self.working_dir
            if os.path.isdir(parent):
                files = [f for f in os.listdir(parent) if os.path.isfile(os.path.join(parent, f))]
                basename = os.path.basename(path)
                similar = get_close_matches(basename, files, n=3, cutoff=0.6)
                if similar:
                    return f"Error: File not found: {path}\n\nDid you mean: {', '.join(similar)}?"
            return f"Error: File not found: {path}"

        if os.path.isdir(full_path):
            return f"Error: '{path}' is a directory, not a file. Use list_files to see contents."

        try:
            with open(full_path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()

            total_lines = len(lines)

            # Handle line range
            if start_line is not None or end_line is not None:
                start = max(1, start_line or 1) - 1  # Convert to 0-indexed
                end = min(total_lines, end_line or total_lines)
                lines = lines[start:end]
                start_num = start + 1
            else:
                start_num = 1
                # Truncate if too long
                if len(lines) > 500:
                    lines = lines[:500]
                    truncated = total_lines - 500
                else:
                    truncated = 0

            content = "".join(f"{i + start_num:4}| {line}" for i, line in enumerate(lines))

            if start_line is not None or end_line is not None:
                header = f"[Lines {start_num}-{start_num + len(lines) - 1} of {total_lines}]\n"
            elif truncated > 0:
                header = ""
                content += f"\n... ({truncated} more lines truncated)"
            else:
                header = ""

            return header + (content if content else "(empty file)")
        except Exception as e:
            return f"Error reading file: {e}"

    def write_file(self, path: str, content: str, confirmed: bool = False) -> str:
        """Write content to a file."""
        if not confirmed:
            return "NEEDS_CONFIRMATION:write_file"

        try:
            full_path = self._safe_path(path)
        except ValueError as e:
            return f"Error: {e}"

        try:
            self.backup_manager.backup(path)

            parent_dir = os.path.dirname(full_path)
            if parent_dir:
                os.makedirs(parent_dir, exist_ok=True)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(content)

            lines = content.count("\n") + 1
            return f"Successfully wrote {lines} lines to {path}"
        except Exception as e:
            return f"Error writing file: {e}"

    def edit_file(self, path: str, old_text: str, new_text: str, confirmed: bool = False) -> str:
        """Replace text in a file with improved error messages."""
        if not confirmed:
            return "NEEDS_CONFIRMATION:edit_file"

        try:
            full_path = self._safe_path(path)
        except ValueError as e:
            return f"Error: {e}"

        if not os.path.exists(full_path):
            return f"Error: File not found: {path}\nTip: Use read_file first to verify the file exists and see its contents."

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            if old_text not in content:
                # Provide helpful suggestions
                similar = self._find_similar_text(content, old_text)
                error_msg = f"Error: Could not find the specified text in {path}"
                error_msg += "\n\nThe text you're trying to replace wasn't found."
                error_msg += "\nTip: Make sure the text matches exactly, including whitespace and indentation."

                if similar:
                    error_msg += "\n\nSimilar lines found:\n  " + "\n  ".join(similar)

                return error_msg

            count = content.count(old_text)
            if count > 1:
                return f"Error: Found {count} matches in {path}.\nTip: Include more surrounding context to make the match unique."

            self.backup_manager.backup(path)
            new_content = content.replace(old_text, new_text, 1)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return f"Successfully edited {path}"
        except Exception as e:
            return f"Error editing file: {e}"

    def list_files(self, pattern: str) -> str:
        """List files matching a glob pattern."""
        try:
            matches = list(Path(self.working_dir).glob(pattern))

            filtered = []
            skip_dirs = {
                "node_modules",
                "__pycache__",
                ".git",
                ".venv",
                "venv",
                ".tox",
                "dist",
                "build",
                ".next",
                ".cache",
            }

            for m in matches:
                rel_path = str(m.relative_to(self.working_dir))
                parts = rel_path.split(os.sep)

                if any(p.startswith(".") or p in skip_dirs for p in parts):
                    continue

                if m.is_file():
                    filtered.append(rel_path)

            filtered.sort()

            if not filtered:
                return f"No files found matching pattern: {pattern}"

            if len(filtered) > 100:
                result = "\n".join(filtered[:100])
                result += f"\n... ({len(filtered) - 100} more files)"
            else:
                result = "\n".join(filtered)

            return f"Found {len(filtered)} files:\n{result}"
        except Exception as e:
            return f"Error listing files: {e}"

    def search_files(
        self, pattern: str, file_pattern: str = "**/*", case_sensitive: bool = False
    ) -> str:
        """Search for a regex pattern in files."""
        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            regex = re.compile(pattern, flags)
        except re.error as e:
            return f"Invalid regex pattern: {e}\nTip: Escape special characters like . * + ? with backslash."

        results = []
        files_searched = 0
        skip_dirs = {"node_modules", "__pycache__", ".git", ".venv", "venv", ".next", ".cache"}

        try:
            for file_path in Path(self.working_dir).glob(file_pattern):
                if not file_path.is_file():
                    continue

                rel_path = str(file_path.relative_to(self.working_dir))
                parts = rel_path.split(os.sep)

                if any(p.startswith(".") or p in skip_dirs for p in parts):
                    continue

                try:
                    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                        files_searched += 1
                        for i, line in enumerate(f, 1):
                            if regex.search(line):
                                display_line = line.strip()[:100]
                                results.append(f"{rel_path}:{i}: {display_line}")

                                if len(results) >= 50:
                                    break
                except (IOError, UnicodeDecodeError):
                    continue

                if len(results) >= 50:
                    break

            if not results:
                return f"No matches found for '{pattern}' in {files_searched} files"

            output = "\n".join(results)
            if len(results) >= 50:
                output += "\n... (results truncated at 50 matches)"

            return f"Found {len(results)} matches:\n{output}"
        except Exception as e:
            return f"Error searching files: {e}"

    def run_command(self, command: str, timeout: int = 60, confirmed: bool = False) -> str:
        """Execute a shell command."""
        if self._is_dangerous_command(command):
            if not confirmed:
                return "NEEDS_CONFIRMATION:dangerous_command"

        timeout = min(max(timeout, 5), 300)

        try:
            # Sanitize command to block injection attacks
            command = _sanitize_command(command)

            # Use shell=False when possible for security
            if _needs_shell(command):
                # Command needs shell features - use shell but command is sanitized
                result = subprocess.run(
                    command,
                    shell=True,
                    cwd=self.working_dir,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
            else:
                # Safe to use list form without shell
                result = subprocess.run(
                    shlex.split(command),
                    shell=False,
                    cwd=self.working_dir,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )

            output = ""
            if result.stdout:
                output += result.stdout
            if result.stderr:
                if output:
                    output += "\n"
                output += f"[stderr]\n{result.stderr}"

            if not output:
                output = "(no output)"

            if len(output) > 5000:
                output = output[:5000] + "\n... (output truncated)"

            status = (
                "succeeded" if result.returncode == 0 else f"failed (exit code {result.returncode})"
            )
            return f"Command {status}:\n{output}"

        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds\nTip: Use timeout parameter for long-running commands."
        except Exception as e:
            return f"Error running command: {e}"

    # Git-specific tools
    def git_status(self) -> str:
        """Show git status."""
        success, output = self._run_git(["status", "--short", "--branch"])
        if not success:
            return f"Error: {output}"
        return output if output else "Working tree clean, nothing to commit"

    def git_diff(self, path: str = None, staged: bool = False, commit: str = None) -> str:
        """Show git diff."""
        args = ["diff"]

        if staged:
            args.append("--cached")
        if commit:
            args.append(commit)
        if path:
            args.extend(["--", path])

        success, output = self._run_git(args, timeout=60)
        if not success:
            return f"Error: {output}"
        return output if output else "(no differences)"

    def git_log(self, count: int = 10, path: str = None, oneline: bool = True) -> str:
        """Show git log."""
        count = min(max(count, 1), 50)
        args = ["log", f"-{count}"]

        if oneline:
            args.append("--oneline")
        else:
            args.extend(["--format=%h %ad %s", "--date=short"])

        if path:
            args.extend(["--", path])

        success, output = self._run_git(args)
        if not success:
            return f"Error: {output}"
        return output if output else "(no commits)"

    def git_commit(self, message: str, files: List[str] = None, confirmed: bool = False) -> str:
        """Stage and commit files."""
        if not confirmed:
            return "NEEDS_CONFIRMATION:git_commit"

        # Stage files
        if files:
            for f in files:
                success, output = self._run_git(["add", f])
                if not success:
                    return f"Error staging {f}: {output}"
        else:
            success, output = self._run_git(["add", "-A"])
            if not success:
                return f"Error staging files: {output}"

        # Check if there's anything to commit
        success, status = self._run_git(["status", "--porcelain"])
        if success and not status:
            return "Nothing to commit, working tree clean"

        # Commit
        success, output = self._run_git(["commit", "-m", message])
        if not success:
            return f"Error: {output}"

        return f"Committed: {output}"

    def git_branch(self, action: str = "list", name: str = None) -> str:
        """List, create, switch, or delete branches."""
        if action == "list":
            success, output = self._run_git(["branch", "-a"])
            return output if success else f"Error: {output}"

        elif action == "create":
            if not name:
                return "Error: Branch name required for create action"
            success, output = self._run_git(["branch", name])
            return f"Created branch: {name}" if success else f"Error: {output}"

        elif action == "switch":
            if not name:
                return "Error: Branch name required for switch action"
            success, output = self._run_git(["checkout", name])
            return f"Switched to: {name}" if success else f"Error: {output}"

        elif action == "delete":
            if not name:
                return "Error: Branch name required for delete action"
            success, output = self._run_git(["branch", "-d", name])
            return f"Deleted branch: {name}" if success else f"Error: {output}"

        else:
            return f"Unknown action: {action}. Use list, create, switch, or delete."
