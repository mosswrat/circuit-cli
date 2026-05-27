"""
File operation tools for Circuit Agent.
"""

import os
import re
import shlex
import subprocess
from difflib import get_close_matches
from pathlib import Path
from typing import TYPE_CHECKING, List, Optional

from ..config import DANGEROUS_PATTERNS

# Shell metacharacters that require shell=True
SHELL_METACHARACTERS = set("|;&$`><(){}[]!*?~")


def _needs_shell(command: str) -> bool:
    """Check if command contains shell metacharacters requiring shell=True."""
    # Check for shell metacharacters (excluding quotes which shlex handles)
    for char in command:
        if char in SHELL_METACHARACTERS:
            return True
    return False


def _sanitize_command(command: str) -> str:
    """Sanitize command string to prevent injection attacks."""
    # Block command substitution patterns
    dangerous_subst = [
        r"\$\([^)]+\)",  # $(command)
        r"`[^`]+`",  # `command`
        r"\$\{[^}]+\}",  # ${variable} expansion
    ]
    for pattern in dangerous_subst:
        if re.search(pattern, command):
            raise ValueError("Command substitution not allowed for security reasons")
    return command


if TYPE_CHECKING:
    from ..errors import SmartError


# Tool definitions in OpenAI function calling format
FILE_TOOLS = [
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
    {
        "type": "function",
        "function": {
            "name": "html_to_markdown",
            "description": "Convert an HTML file to Markdown. Extracts text content, removes scripts/styles, and formats as markdown. Use this for large HTML files that are too big to read directly.",
            "parameters": {
                "type": "object",
                "properties": {
                    "input_path": {
                        "type": "string",
                        "description": "Path to the HTML file to convert",
                    },
                    "output_path": {
                        "type": "string",
                        "description": "Path for the output markdown file (e.g., 'output.md')",
                    },
                },
                "required": ["input_path", "output_path"],
            },
        },
    },
]


class FileTools:
    """File operation tool implementations."""

    def __init__(
        self, working_dir: str, backup_manager=None, smart_error: Optional["SmartError"] = None
    ):
        self.working_dir = os.path.realpath(os.path.abspath(working_dir))
        self.backup_manager = backup_manager
        self.smart_error = smart_error

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

    def _find_similar_text(self, content: str, search_text: str, n: int = 3) -> List[str]:
        """Find similar lines to the search text for better error messages."""
        content_lines = content.split("\n")
        search_lines = search_text.strip().split("\n")

        if not search_lines:
            return []

        first_search_line = search_lines[0].strip()
        if not first_search_line:
            return []

        stripped_lines = [line.strip() for line in content_lines]
        matches = get_close_matches(first_search_line, stripped_lines, n=n, cutoff=0.6)

        results = []
        for match in matches:
            for i, stripped in enumerate(stripped_lines):
                if stripped == match and content_lines[i] not in results:
                    results.append(f"Line {i + 1}: {content_lines[i][:80]}")
                    break
        return results

    def read_file(self, args: dict, confirmed: bool = False) -> str:
        """Read file contents with line numbers."""
        path = args.get("path", "")
        start_line = args.get("start_line")
        end_line = args.get("end_line")

        try:
            full_path = self._safe_path(path)
        except ValueError as e:
            return f"Error: {e}"

        if not os.path.exists(full_path):
            # Use SmartError for helpful suggestions
            if self.smart_error:
                return self.smart_error.file_not_found(path, "read")
            # Fallback to basic error handling
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

            if start_line is not None or end_line is not None:
                start = max(1, start_line or 1) - 1
                end = min(total_lines, end_line or total_lines)
                lines = lines[start:end]
                start_num = start + 1
            else:
                start_num = 1
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

    def write_file(self, args: dict, confirmed: bool = False) -> str:
        """Write content to a file."""
        if not confirmed:
            return "NEEDS_CONFIRMATION:write_file"

        path = args.get("path", "")
        content = args.get("content", "")

        try:
            full_path = self._safe_path(path)
        except ValueError as e:
            return f"Error: {e}"

        try:
            if self.backup_manager:
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

    def edit_file(self, args: dict, confirmed: bool = False) -> str:
        """Replace text in a file with improved error messages."""
        if not confirmed:
            return "NEEDS_CONFIRMATION:edit_file"

        path = args.get("path", "")
        old_text = args.get("old_text", "")
        new_text = args.get("new_text", "")

        try:
            full_path = self._safe_path(path)
        except ValueError as e:
            return f"Error: {e}"

        if not os.path.exists(full_path):
            if self.smart_error:
                return self.smart_error.file_not_found(path, "edit")
            return f"Error: File not found: {path}\nTip: Use read_file first to verify the file exists and see its contents."

        try:
            with open(full_path, "r", encoding="utf-8") as f:
                content = f.read()

            if old_text not in content:
                # Use SmartError for detailed suggestions
                if self.smart_error:
                    return self.smart_error.text_not_found(path, old_text, content)
                # Fallback
                similar = self._find_similar_text(content, old_text)
                error_msg = f"Error: Could not find the specified text in {path}"
                error_msg += "\n\nThe text you're trying to replace wasn't found."
                error_msg += "\nTip: Make sure the text matches exactly, including whitespace and indentation."

                if similar:
                    error_msg += "\n\nSimilar lines found:\n  " + "\n  ".join(similar)

                return error_msg

            count = content.count(old_text)
            if count > 1:
                # Use SmartError for multiple matches
                if self.smart_error:
                    return self.smart_error.multiple_matches(path, old_text, content, count)
                return f"Error: Found {count} matches in {path}.\nTip: Include more surrounding context to make the match unique."

            if self.backup_manager:
                self.backup_manager.backup(path)
            new_content = content.replace(old_text, new_text, 1)

            with open(full_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            return f"Successfully edited {path}"
        except Exception as e:
            return f"Error editing file: {e}"

    def list_files(self, args: dict, confirmed: bool = False) -> str:
        """List files matching a glob pattern."""
        pattern = args.get("pattern", "**/*")

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

    def search_files(self, args: dict, confirmed: bool = False) -> str:
        """Search for a regex pattern in files."""
        pattern = args.get("pattern", "")
        file_pattern = args.get("file_pattern", "**/*")
        case_sensitive = args.get("case_sensitive", False)

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

    def run_command(self, args: dict, confirmed: bool = False) -> str:
        """Execute a shell command."""
        command = args.get("command", "")
        timeout = args.get("timeout", 60)

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

            if result.returncode == 0:
                return f"Command succeeded:\n{output}"
            else:
                # Use SmartError for failed commands
                if self.smart_error:
                    return self.smart_error.command_failed(
                        command, result.returncode, result.stdout, result.stderr
                    )
                return f"Command failed (exit code {result.returncode}):\n{output}"

        except subprocess.TimeoutExpired:
            return f"Error: Command timed out after {timeout} seconds\nTip: Use timeout parameter for long-running commands."
        except Exception as e:
            return f"Error running command: {e}"

    def html_to_markdown(self, args: dict, confirmed: bool = False) -> str:
        """Convert an HTML file to Markdown."""
        input_path = args.get("input_path", "")
        output_path = args.get("output_path", "")

        try:
            full_input = self._safe_path(input_path)
            full_output = self._safe_path(output_path)
        except ValueError as e:
            return f"Error: {e}"

        if not os.path.exists(full_input):
            return f"Error: File not found: {input_path}"

        try:
            with open(full_input, "r", encoding="utf-8", errors="replace") as f:
                html = f.read()

            # Remove script and style tags with content
            html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r"<head[^>]*>.*?</head>", "", html, flags=re.DOTALL | re.IGNORECASE)

            # Convert headers
            html = re.sub(
                r"<h1[^>]*>(.*?)</h1>", r"\n# \1\n", html, flags=re.DOTALL | re.IGNORECASE
            )
            html = re.sub(
                r"<h2[^>]*>(.*?)</h2>", r"\n## \1\n", html, flags=re.DOTALL | re.IGNORECASE
            )
            html = re.sub(
                r"<h3[^>]*>(.*?)</h3>", r"\n### \1\n", html, flags=re.DOTALL | re.IGNORECASE
            )
            html = re.sub(
                r"<h4[^>]*>(.*?)</h4>", r"\n#### \1\n", html, flags=re.DOTALL | re.IGNORECASE
            )

            # Convert paragraphs and breaks
            html = re.sub(r"<p[^>]*>", "\n\n", html, flags=re.IGNORECASE)
            html = re.sub(r"</p>", "", html, flags=re.IGNORECASE)
            html = re.sub(r"<br[^>]*/?\s*>", "\n", html, flags=re.IGNORECASE)
            html = re.sub(r"<div[^>]*>", "\n", html, flags=re.IGNORECASE)
            html = re.sub(r"</div>", "\n", html, flags=re.IGNORECASE)

            # Convert lists
            html = re.sub(r"<li[^>]*>", "\n- ", html, flags=re.IGNORECASE)
            html = re.sub(r"</li>", "", html, flags=re.IGNORECASE)
            html = re.sub(r"<[ou]l[^>]*>", "\n", html, flags=re.IGNORECASE)
            html = re.sub(r"</[ou]l>", "\n", html, flags=re.IGNORECASE)

            # Convert code blocks
            html = re.sub(
                r"<pre[^>]*>(.*?)</pre>", r"\n```\n\1\n```\n", html, flags=re.DOTALL | re.IGNORECASE
            )
            html = re.sub(
                r"<code[^>]*>(.*?)</code>", r"`\1`", html, flags=re.DOTALL | re.IGNORECASE
            )

            # Convert formatting
            html = re.sub(
                r"<strong[^>]*>(.*?)</strong>", r"**\1**", html, flags=re.DOTALL | re.IGNORECASE
            )
            html = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", html, flags=re.DOTALL | re.IGNORECASE)
            html = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", html, flags=re.DOTALL | re.IGNORECASE)

            # Convert links
            html = re.sub(
                r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
                r"[\2](\1)",
                html,
                flags=re.DOTALL | re.IGNORECASE,
            )

            # Remove remaining HTML tags
            html = re.sub(r"<[^>]+>", " ", html)

            # Clean up HTML entities
            html = html.replace("&nbsp;", " ")
            html = html.replace("&amp;", "&")
            html = html.replace("&lt;", "<")
            html = html.replace("&gt;", ">")
            html = html.replace("&quot;", '"')
            html = html.replace("&#39;", "'")

            # Clean up whitespace
            html = re.sub(r"[ \t]+", " ", html)
            html = re.sub(r"\n[ \t]+", "\n", html)
            html = re.sub(r"[ \t]+\n", "\n", html)
            html = re.sub(r"\n{3,}", "\n\n", html)
            markdown = html.strip()

            # Write output
            with open(full_output, "w", encoding="utf-8") as f:
                f.write(markdown)

            lines = markdown.count("\n") + 1
            return f"Successfully converted {input_path} to {output_path} ({lines} lines)"

        except Exception as e:
            return f"Error converting HTML to markdown: {e}"
