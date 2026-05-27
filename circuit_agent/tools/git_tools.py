"""
Git operation tools for Circuit Agent.
"""

import subprocess
from typing import TYPE_CHECKING, List, Optional, Tuple

if TYPE_CHECKING:
    from ..errors import SmartError


# Git tool definitions in OpenAI function calling format
GIT_TOOLS = [
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


class GitTools:
    """Git operation tool implementations."""

    def __init__(self, working_dir: str, smart_error: Optional["SmartError"] = None):
        self.working_dir = working_dir
        self.smart_error = smart_error

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

    def git_status(self, args: dict, confirmed: bool = False) -> str:
        """Show git status."""
        success, output = self._run_git(["status", "--short", "--branch"])
        if not success:
            return f"Error: {output}"
        return output if output else "Working tree clean, nothing to commit"

    def git_diff(self, args: dict, confirmed: bool = False) -> str:
        """Show git diff."""
        path = args.get("path")
        staged = args.get("staged", False)
        commit = args.get("commit")

        git_args = ["diff"]

        if staged:
            git_args.append("--cached")
        if commit:
            git_args.append(commit)
        if path:
            git_args.extend(["--", path])

        success, output = self._run_git(git_args, timeout=60)
        if not success:
            return f"Error: {output}"
        return output if output else "(no differences)"

    def git_log(self, args: dict, confirmed: bool = False) -> str:
        """Show git log."""
        count = args.get("count", 10)
        path = args.get("path")
        oneline = args.get("oneline", True)

        count = min(max(count, 1), 50)
        git_args = ["log", f"-{count}"]

        if oneline:
            git_args.append("--oneline")
        else:
            git_args.extend(["--format=%h %ad %s", "--date=short"])

        if path:
            git_args.extend(["--", path])

        success, output = self._run_git(git_args)
        if not success:
            return f"Error: {output}"
        return output if output else "(no commits)"

    def git_commit(self, args: dict, confirmed: bool = False) -> str:
        """Stage and commit files."""
        if not confirmed:
            return "NEEDS_CONFIRMATION:git_commit"

        message = args.get("message", "")
        files = args.get("files", [])

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
            if self.smart_error:
                return self.smart_error.git_error("commit", output)
            return f"Error: {output}"

        return f"Committed: {output}"

    def git_branch(self, args: dict, confirmed: bool = False) -> str:
        """List, create, switch, or delete branches."""
        action = args.get("action", "list")
        name = args.get("name")

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
