"""
Smart Error Handling for Circuit Agent v4.0.

Provides helpful error messages with suggestions and context.
"""

import os
import re
from difflib import SequenceMatcher, get_close_matches
from typing import List, Optional, Tuple


class SmartError:
    """Generate helpful error messages with suggestions."""

    def __init__(self, working_dir: str):
        self.working_dir = working_dir

    def file_not_found(self, path: str, operation: str = "read") -> str:
        """
        Generate helpful error for file not found.

        Args:
            path: The path that wasn't found
            operation: What operation was attempted (read, edit, etc.)

        Returns:
            Formatted error message with suggestions
        """
        msg_parts = [f"File not found: {path}"]

        # Find similar files
        similar = self._find_similar_files(path)
        if similar:
            msg_parts.append("\nDid you mean one of these?")
            for s in similar[:5]:
                msg_parts.append(f"  - {s}")

        # Check if parent directory exists
        parent = os.path.dirname(path)
        if parent:
            parent_path = os.path.join(self.working_dir, parent)
            if not os.path.exists(parent_path):
                msg_parts.append(f"\nNote: Directory '{parent}' doesn't exist either.")
                # Suggest similar directories
                similar_dirs = self._find_similar_directories(parent)
                if similar_dirs:
                    msg_parts.append("Similar directories:")
                    for d in similar_dirs[:3]:
                        msg_parts.append(f"  - {d}")

        # Add helpful tips based on operation
        msg_parts.append("\nTips:")
        if operation == "edit":
            msg_parts.append("  - Use read_file first to verify the file exists")
            msg_parts.append("  - Use list_files to see available files")
        else:
            msg_parts.append("  - Use list_files to see available files")
            msg_parts.append("  - Check if the path is relative to the working directory")

        return "\n".join(msg_parts)

    def text_not_found(self, path: str, search_text: str, file_content: str) -> str:
        """
        Generate helpful error for text not found in file.

        Args:
            path: The file path
            search_text: The text that wasn't found
            file_content: The actual file content

        Returns:
            Formatted error message with suggestions
        """
        msg_parts = [f"Could not find the specified text in {path}"]
        msg_parts.append("\nThe text you're trying to replace wasn't found.")

        # Find similar text
        similar_lines = self._find_similar_text(search_text, file_content)
        if similar_lines:
            msg_parts.append("\nSimilar text found in file:")
            for line_num, text, score in similar_lines[:5]:
                # Truncate long lines
                display_text = text[:80] + "..." if len(text) > 80 else text
                msg_parts.append(f"  Line {line_num}: {display_text}")
                if score < 0.8:
                    msg_parts.append(f"           (similarity: {score:.0%})")

        # Analyze potential issues
        issues = self._analyze_text_mismatch(search_text, file_content)
        if issues:
            msg_parts.append("\nPossible issues detected:")
            for issue in issues:
                msg_parts.append(f"  - {issue}")

        # Tips
        msg_parts.append("\nTips:")
        msg_parts.append("  - Ensure exact whitespace/indentation match")
        msg_parts.append("  - Use read_file to see current content")
        msg_parts.append("  - Include more surrounding context for unique matching")
        msg_parts.append("  - Check for tabs vs spaces")

        return "\n".join(msg_parts)

    def multiple_matches(self, path: str, search_text: str, file_content: str, count: int) -> str:
        """
        Generate helpful error for multiple text matches.

        Args:
            path: The file path
            search_text: The text that matched multiple times
            file_content: The actual file content
            count: Number of matches found

        Returns:
            Formatted error message with suggestions
        """
        msg_parts = [f"Found {count} matches in {path}"]
        msg_parts.append("The text you're trying to replace appears multiple times.")

        # Find all match locations
        locations = self._find_all_match_locations(search_text, file_content)
        if locations:
            msg_parts.append("\nMatches found at:")
            for line_num, context in locations[:5]:
                display_context = context[:60] + "..." if len(context) > 60 else context
                msg_parts.append(f"  Line {line_num}: {display_context}")
            if len(locations) > 5:
                msg_parts.append(f"  ... and {len(locations) - 5} more")

        # Tips
        msg_parts.append("\nTips:")
        msg_parts.append("  - Include more surrounding context to make the match unique")
        msg_parts.append("  - Add lines before or after the target text")
        msg_parts.append("  - Include function/class name if editing within one")

        return "\n".join(msg_parts)

    def command_failed(self, command: str, exit_code: int, stdout: str, stderr: str) -> str:
        """
        Generate helpful error for failed command.

        Args:
            command: The command that failed
            exit_code: The exit code
            stdout: Standard output
            stderr: Standard error

        Returns:
            Formatted error message with suggestions
        """
        msg_parts = [f"Command failed with exit code {exit_code}"]
        msg_parts.append(f"Command: {command[:100]}{'...' if len(command) > 100 else ''}")

        # Analyze error
        error_text = stderr or stdout
        suggestions = self._analyze_command_error(command, error_text)

        if stderr:
            msg_parts.append("\nError output:")
            # Truncate very long errors
            if len(stderr) > 500:
                msg_parts.append(stderr[:500] + "\n[truncated]")
            else:
                msg_parts.append(stderr)

        if suggestions:
            msg_parts.append("\nSuggestions:")
            for s in suggestions:
                msg_parts.append(f"  - {s}")

        return "\n".join(msg_parts)

    def git_error(self, operation: str, error_msg: str) -> str:
        """
        Generate helpful error for git operations.

        Args:
            operation: The git operation attempted
            error_msg: The error message from git

        Returns:
            Formatted error message with suggestions
        """
        msg_parts = [f"Git {operation} failed"]

        suggestions = []

        # Common git errors and suggestions
        if "not a git repository" in error_msg.lower():
            suggestions.append("Initialize a git repository with: git init")

        if "nothing to commit" in error_msg.lower():
            suggestions.append("No changes to commit. Use git_status to see current state.")

        if "merge conflict" in error_msg.lower():
            suggestions.append("Resolve merge conflicts in the affected files")
            suggestions.append("Use git_status to see conflicted files")

        if "would be overwritten" in error_msg.lower():
            suggestions.append("Commit or stash your changes first")

        if "authentication failed" in error_msg.lower():
            suggestions.append("Check your git credentials")
            suggestions.append("For GitHub, you may need a personal access token")

        if "does not exist" in error_msg.lower() and "branch" in error_msg.lower():
            suggestions.append("Use git_branch with action='list' to see available branches")

        if "already exists" in error_msg.lower():
            suggestions.append("Choose a different name or delete the existing one first")

        if "detached HEAD" in error_msg.lower():
            suggestions.append(
                "Create a new branch to save your work: git checkout -b <branch-name>"
            )

        msg_parts.append(f"\nError: {error_msg}")

        if suggestions:
            msg_parts.append("\nSuggestions:")
            for s in suggestions:
                msg_parts.append(f"  - {s}")

        return "\n".join(msg_parts)

    def _find_similar_files(self, path: str, max_results: int = 5) -> List[str]:
        """Find files with similar names."""
        target_name = os.path.basename(path)
        os.path.dirname(path) or "."

        all_files = []
        try:
            for root, dirs, files in os.walk(self.working_dir):
                # Skip hidden and common ignored directories
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d not in {"node_modules", "__pycache__", "venv", ".venv", "dist", "build"}
                ]

                for f in files:
                    rel_path = os.path.relpath(os.path.join(root, f), self.working_dir)
                    all_files.append(rel_path)
        except Exception:
            return []

        # Find similar by name
        all_names = [os.path.basename(f) for f in all_files]
        similar_names = get_close_matches(target_name, all_names, n=max_results * 2, cutoff=0.5)

        # Return full paths for similar names
        results = []
        for name in similar_names:
            for f in all_files:
                if os.path.basename(f) == name and f not in results:
                    results.append(f)
                    break
            if len(results) >= max_results:
                break

        return results

    def _find_similar_directories(self, dir_path: str, max_results: int = 3) -> List[str]:
        """Find directories with similar names."""
        target_name = os.path.basename(dir_path.rstrip("/"))

        all_dirs = []
        try:
            for root, dirs, _ in os.walk(self.working_dir):
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".")
                    and d not in {"node_modules", "__pycache__", "venv", ".venv"}
                ]
                for d in dirs:
                    rel_path = os.path.relpath(os.path.join(root, d), self.working_dir)
                    all_dirs.append(rel_path)
        except Exception:
            return []

        all_names = [os.path.basename(d) for d in all_dirs]
        similar_names = get_close_matches(target_name, all_names, n=max_results, cutoff=0.5)

        results = []
        for name in similar_names:
            for d in all_dirs:
                if os.path.basename(d) == name and d not in results:
                    results.append(d)
                    break

        return results

    def _find_similar_text(
        self, search_text: str, content: str, max_results: int = 5
    ) -> List[Tuple[int, str, float]]:
        """Find similar text in file content."""
        search_lines = search_text.strip().split("\n")
        if not search_lines:
            return []

        first_search_line = search_lines[0].strip()
        if not first_search_line:
            return []

        content_lines = content.split("\n")
        results = []

        for i, line in enumerate(content_lines, 1):
            stripped = line.strip()
            if not stripped:
                continue

            # Calculate similarity
            ratio = SequenceMatcher(None, first_search_line, stripped).ratio()
            if ratio >= 0.5:  # At least 50% similar
                results.append((i, line, ratio))

        # Sort by similarity
        results.sort(key=lambda x: x[2], reverse=True)
        return results[:max_results]

    def _find_all_match_locations(self, search_text: str, content: str) -> List[Tuple[int, str]]:
        """Find all locations where text matches."""
        results = []
        lines = content.split("\n")

        # Find the first line of search text
        first_line = search_text.split("\n")[0].strip()

        for i, line in enumerate(lines, 1):
            if first_line in line:
                results.append((i, line.strip()))

        return results

    def _analyze_text_mismatch(self, search_text: str, content: str) -> List[str]:
        """Analyze potential issues with text matching."""
        issues = []

        # Check for whitespace issues
        if "\t" in search_text and "\t" not in content:
            issues.append("Search text contains tabs but file uses spaces")
        elif " " * 4 in search_text and "\t" in content:
            issues.append("Search text uses spaces but file uses tabs")

        # Check for line ending issues
        if "\r\n" in search_text and "\r\n" not in content:
            issues.append("Search text has Windows line endings (CRLF) but file uses Unix (LF)")
        elif "\r\n" in content and "\r\n" not in search_text:
            issues.append("File has Windows line endings (CRLF) but search text uses Unix (LF)")

        # Check for trailing whitespace
        search_lines = search_text.split("\n")
        for i, line in enumerate(search_lines):
            if line != line.rstrip():
                issues.append(f"Search text has trailing whitespace on line {i + 1}")
                break

        # Check if search text might be outdated
        keywords = re.findall(r"\b\w{4,}\b", search_text)
        if keywords:
            found_any = any(kw in content for kw in keywords[:5])
            if not found_any:
                issues.append(
                    "None of the key terms from search text found in file - file may have changed significantly"
                )

        return issues

    def _analyze_command_error(self, command: str, error: str) -> List[str]:
        """Analyze command error and provide suggestions."""
        suggestions = []
        error_lower = error.lower()

        # Python errors
        if "modulenotfounderror" in error_lower or "no module named" in error_lower:
            module = re.search(r"no module named ['\"]?(\w+)", error_lower)
            if module:
                suggestions.append(f"Install missing module: pip install {module.group(1)}")
            suggestions.append("Check if virtual environment is activated")

        if "syntaxerror" in error_lower:
            suggestions.append("Check for syntax errors in the Python file")
            suggestions.append("Look for missing colons, parentheses, or quotes")

        # Node/npm errors
        if "cannot find module" in error_lower:
            suggestions.append("Run 'npm install' to install dependencies")

        if "enoent" in error_lower:
            suggestions.append("File or directory not found - check the path")

        # Permission errors
        if "permission denied" in error_lower:
            suggestions.append("Check file permissions")
            suggestions.append("You may need to use sudo (with caution)")

        # Command not found
        if "command not found" in error_lower or "not recognized" in error_lower:
            cmd = command.split()[0] if command else "command"
            suggestions.append(f"'{cmd}' is not installed or not in PATH")
            suggestions.append("Check if the command name is spelled correctly")

        # Memory errors
        if "out of memory" in error_lower or "killed" in error_lower:
            suggestions.append("Process ran out of memory")
            suggestions.append("Try processing smaller chunks of data")

        # Network errors
        if "connection refused" in error_lower or "network" in error_lower:
            suggestions.append("Check network connectivity")
            suggestions.append("Verify the target host/port is correct")

        return suggestions


def format_error_context(
    error_type: str,
    message: str,
    file_path: Optional[str] = None,
    line_number: Optional[int] = None,
    suggestions: Optional[List[str]] = None,
) -> str:
    """
    Format an error with full context.

    Args:
        error_type: Category of error
        message: Main error message
        file_path: Related file path
        line_number: Line number if applicable
        suggestions: List of suggestions

    Returns:
        Formatted error string
    """
    parts = [f"Error ({error_type}): {message}"]

    if file_path:
        location = f"  File: {file_path}"
        if line_number:
            location += f", Line {line_number}"
        parts.append(location)

    if suggestions:
        parts.append("\nSuggestions:")
        for s in suggestions:
            parts.append(f"  - {s}")

    return "\n".join(parts)
