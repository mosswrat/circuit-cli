"""
Circuit Agent v4.0 - Core agent class with streaming, parallel tools, and web access.
"""

import asyncio
import base64
import json
import os
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

import httpx

from .config import (
    API_VERSION,
    CHAT_BASE_URL,
    TOKEN_URL,
    detect_project_type,
    load_circuit_md,
    load_github_mcp_config,
    load_github_pat,
    ssl_config,
)
from .context import SmartContextManager
from .errors import SmartError

# MCP support
from .mcp import MCPClientManager
from .mcp.servers.github import GitHubMCPServer
from .mcp.transport import MCPTransportError
from .memory import ContextCompactor, SessionManager
from .security import AuditLogger, CostTracker, SecretDetector
from .streaming import StreamingResponse, non_streaming_chat_completion, stream_chat_completion
from .tools import TOOLS, BackupManager, FileTools, GitTools, WebTools
from .tools.github_tools import GITHUB_TOOLS, GitHubTools
from .ui import C, print_success, print_tool_call, print_tool_result, show_diff


class CircuitAgent:
    """AI coding assistant powered by Cisco Circuit."""

    def __init__(self, client_id: str, client_secret: str, app_key: str, working_dir: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.app_key = app_key
        self.working_dir = os.path.abspath(working_dir)
        self._token: Optional[str] = None
        self._expiry: float = 0
        self.model = "gpt-5-nano"
        self.history: List[Dict[str, Any]] = []

        # v4.0: Smart error handling (initialize before tools)
        self.smart_error = SmartError(working_dir)

        # Initialize modular tool classes
        self.backup_manager = BackupManager(working_dir)
        self.file_tools = FileTools(working_dir, self.backup_manager, self.smart_error)
        self.git_tools = GitTools(working_dir, self.smart_error)
        self.web_tools = WebTools()
        self.github_tools = GitHubTools()

        # Session and compaction
        self.session_manager = SessionManager()
        self.compactor = ContextCompactor()

        # Token tracking
        self.session_prompt_tokens = 0
        self.session_completion_tokens = 0
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0

        # v4.0: Security features
        self.secret_detector = SecretDetector(enabled=True)
        self.audit_logger = AuditLogger(enabled=True)
        self.cost_tracker = CostTracker()

        # v4.0: Smart context management
        self.context_manager = SmartContextManager(max_tokens=120000)

        # v5.0: MCP (Model Context Protocol) support
        self.mcp_manager = MCPClientManager()
        self._mcp_tools_cache: List[Dict[str, Any]] = []

        # Settings
        self.stream_responses = True
        self.auto_approve = False  # Skip confirmations when True
        self.thinking_mode = False  # v4.0: Show reasoning before actions
        self.max_retries = 3
        self.retry_delay = 1.0  # Base delay for exponential backoff

        # Initialize system prompt
        self._init_system_prompt()

        # Initialize MCP servers
        self._init_mcp_servers()

    def _init_system_prompt(self):
        """Set up the system prompt for the coding agent."""
        project_info = detect_project_type(self.working_dir)

        # Load CIRCUIT.md if present
        circuit_md = load_circuit_md(self.working_dir)
        circuit_section = ""
        if circuit_md:
            circuit_section = f"\n## Project Instructions (from CIRCUIT.md)\n\n{circuit_md}\n"

        self.system_prompt = f"""You are Circuit Agent v4.0, an expert AI coding assistant working in: {self.working_dir}

{project_info}
{circuit_section}
You help with software engineering tasks by reading code, making edits, running commands, looking up documentation, and explaining concepts.

## Available Tools

### File Operations
- **read_file**: Read file contents with line numbers. Supports line ranges. Always read before editing.
- **write_file**: Create new files or overwrite existing ones.
- **edit_file**: Make targeted text replacements. Requires exact match including whitespace.
- **list_files**: Find files using glob patterns (e.g., '**/*.py', 'src/*.ts')
- **search_files**: Search file contents with regex patterns.
- **run_command**: Execute shell commands (tests, builds, scripts, etc.)

### Git Operations
- **git_status**: Show working tree status (staged, unstaged, untracked files)
- **git_diff**: Show changes (working tree, staged, or against commits)
- **git_log**: Show commit history
- **git_commit**: Stage files and create commits
- **git_branch**: List, create, switch, or delete branches

### Web Operations
- **web_fetch**: Fetch content from URLs (documentation, APIs, etc.). Returns markdown.
- **web_search**: Search the web for information. Returns results with titles, URLs, snippets.

## Guidelines

1. **Explore First**: Use list_files and search_files to understand the codebase before making changes.
2. **Read Before Edit**: Always read_file before using edit_file to ensure accurate text matching.
3. **Explain Changes**: Briefly explain what you're doing and why before making edits.
4. **Small Edits**: Make targeted, minimal changes. Don't rewrite entire files unnecessarily.
5. **Verify Results**: After making changes, run tests or builds to verify correctness.
6. **Use Git Wisely**: Use git_status and git_diff to review changes before committing.
7. **Handle Errors**: If a tool fails, read the error, diagnose the issue, and try a different approach.
8. **Look Up Docs**: Use web_search and web_fetch to look up documentation, error messages, and solutions.

## Output Behavior

**Always write to files instead of terminal for:**
- Plans and roadmaps → write to `PLAN.md` (or update existing)
- Documentation → write to `README.md`, `DOCS.md`, etc.
- Summaries and reports → write to appropriate `.md` files
- Code analysis results → write to a file if lengthy

**When user asks to "make a plan" or "write a plan":**
1. Check if `PLAN.md` exists - if so, read it first
2. Write the new/updated plan to `PLAN.md` using write_file
3. Briefly confirm what was written

**Keep terminal output short:**
- Use files for anything longer than ~20 lines
- Terminal should have brief confirmations and summaries
- Reference the file you wrote to so user knows where to look

## Handling Large Files

**When a file is too large to read at once:**
1. Use read_file with start_line and end_line to read in chunks
2. For HTML files: use run_command with Python to extract text content
3. Process the file in sections and combine results

**For HTML to Markdown conversion:**
Use this Python command to extract content:
```
python3 -c "
import re
with open('FILE.html') as f: html=f.read()
html=re.sub(r'<script.*?</script>','',html,flags=re.DOTALL)
html=re.sub(r'<style.*?</style>','',html,flags=re.DOTALL)
html=re.sub(r'<[^>]+>',' ',html)
print(html[:50000])
" > output.txt
```
Then read the output and convert to proper markdown.

## Response Style

- Be concise and direct
- Show relevant code snippets when explaining
- Break complex tasks into clear steps
- When errors occur, explain what went wrong and how to fix it
- Use markdown formatting for readability

## Thinking Mode

When thinking mode is enabled, show your reasoning process before taking actions:

<thinking>
- What I understand about the request
- My approach to solving this
- Tools I'll use and why
- Potential issues to watch for
</thinking>

Then proceed with the actual response and actions."""

    def _get_thinking_prompt(self) -> str:
        """Get the thinking mode instruction if enabled."""
        if not self.thinking_mode:
            return ""

        return """

IMPORTANT: Thinking mode is ON. Before taking any action, briefly explain your reasoning:

<thinking>
1. What the user is asking for
2. What I need to do to accomplish this
3. Which tools I'll use and in what order
4. Any potential issues or edge cases
</thinking>

Then proceed with your response."""

    def _init_mcp_servers(self):
        """Initialize configured MCP servers."""
        try:
            # Check for GitHub MCP configuration
            github_config = load_github_mcp_config()
            github_pat = load_github_pat()

            if github_config.get("enabled") and github_pat:
                # Create GitHub MCP server config
                toolsets = github_config.get("toolsets", [])
                use_remote = github_config.get("use_remote", True)

                if use_remote:
                    config = GitHubMCPServer.get_remote_config(
                        pat=github_pat, toolsets=toolsets, enabled=True
                    )
                else:
                    config = GitHubMCPServer.get_docker_config(
                        pat=github_pat, toolsets=toolsets, enabled=True
                    )

                # Connect to the server
                if self.mcp_manager.connect(config):
                    self._mcp_tools_cache = self.mcp_manager.list_tools()
                    print(
                        f"{C.DIM}  [MCP: GitHub connected, {len(self._mcp_tools_cache)} tools available]{C.RESET}"
                    )
        except Exception as e:
            print(f"{C.DIM}  [MCP init warning: {e}]{C.RESET}")

    def init_mcp(self, configs: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Initialize MCP servers from configuration list.

        Args:
            configs: List of MCP server configurations

        Returns:
            Status dict with connected servers and tool counts
        """
        from .mcp.config import MCPServerConfig

        results = {"connected": [], "failed": [], "total_tools": 0}

        for config_dict in configs:
            try:
                config = MCPServerConfig.from_dict(config_dict)
                if self.mcp_manager.connect(config):
                    results["connected"].append(config.id)
                else:
                    results["failed"].append(config.id)
            except Exception as e:
                results["failed"].append(f"{config_dict.get('id', 'unknown')}: {e}")

        # Update tools cache
        self._mcp_tools_cache = self.mcp_manager.list_tools()
        results["total_tools"] = len(self._mcp_tools_cache)

        return results

    def get_all_tools(self) -> List[Dict[str, Any]]:
        """Get all available tools including GitHub and MCP tools."""
        all_tools = list(TOOLS)  # Copy built-in tools

        # Add GitHub tools if configured
        github_config = load_github_mcp_config()
        if github_config.get("enabled") and load_github_pat():
            all_tools.extend(GITHUB_TOOLS)

        # Add MCP tools
        if self._mcp_tools_cache:
            all_tools.extend(self._mcp_tools_cache)

        return all_tools

    def get_mcp_status(self) -> Dict[str, Any]:
        """Get MCP connection status."""
        return self.mcp_manager.get_status()

    def disconnect_mcp(self, server_id: str = None):
        """Disconnect from MCP server(s)."""
        if server_id:
            self.mcp_manager.disconnect(server_id)
        else:
            self.mcp_manager.disconnect_all()
        self._mcp_tools_cache = self.mcp_manager.list_tools()

    async def get_token(self) -> str:
        """Get OAuth access token, refreshing if needed."""
        if self._token and time.time() < (self._expiry - 300):
            return self._token

        creds = f"{self.client_id}:{self.client_secret}"
        auth = base64.b64encode(creds.encode()).decode()

        async with httpx.AsyncClient(verify=ssl_config.get_verify_param(), timeout=30.0) as client:
            for attempt in range(self.max_retries):
                try:
                    r = await client.post(
                        TOKEN_URL,
                        headers={
                            "Authorization": f"Basic {auth}",
                            "Content-Type": "application/x-www-form-urlencoded",
                        },
                        data="grant_type=client_credentials",
                    )
                    if r.status_code == 200:
                        data = r.json()
                        self._token = data["access_token"]
                        self._expiry = time.time() + data.get("expires_in", 3600)
                        return self._token
                    elif r.status_code >= 500:
                        # Server error, retry
                        if attempt < self.max_retries - 1:
                            await asyncio.sleep(self.retry_delay * (2**attempt))
                            continue
                    raise Exception(f"Auth failed: {r.status_code} - {r.text}")
                except httpx.RequestError as e:
                    if attempt < self.max_retries - 1:
                        await asyncio.sleep(self.retry_delay * (2**attempt))
                        continue
                    raise Exception(f"Auth request failed: {e}") from e

        raise Exception("Authentication failed after retries")

    def _execute_tool(self, name: str, arguments: dict) -> Tuple[Any, bool]:
        """
        Execute a tool and return (result, needs_confirmation).
        If needs_confirmation is True, the result contains the arguments for confirmation.
        """
        # Auto-approve mode skips all confirmations
        confirmed = self.auto_approve

        # File tools
        if name == "read_file":
            return self.file_tools.read_file(arguments, confirmed), False

        elif name == "write_file":
            result = self.file_tools.write_file(arguments, confirmed)
            if result == "NEEDS_CONFIRMATION:write_file":
                return arguments, True
            return result, False

        elif name == "edit_file":
            result = self.file_tools.edit_file(arguments, confirmed)
            if result == "NEEDS_CONFIRMATION:edit_file":
                return arguments, True
            return result, False

        elif name == "list_files":
            return self.file_tools.list_files(arguments, confirmed), False

        elif name == "search_files":
            return self.file_tools.search_files(arguments, confirmed), False

        elif name == "run_command":
            result = self.file_tools.run_command(arguments, confirmed)
            if result == "NEEDS_CONFIRMATION:dangerous_command":
                return arguments, True
            return result, False

        elif name == "html_to_markdown":
            return self.file_tools.html_to_markdown(arguments, confirmed), False

        # Git tools
        elif name == "git_status":
            return self.git_tools.git_status(arguments, confirmed), False

        elif name == "git_diff":
            return self.git_tools.git_diff(arguments, confirmed), False

        elif name == "git_log":
            return self.git_tools.git_log(arguments, confirmed), False

        elif name == "git_commit":
            result = self.git_tools.git_commit(arguments, confirmed)
            if result == "NEEDS_CONFIRMATION:git_commit":
                return arguments, True
            return result, False

        elif name == "git_branch":
            return self.git_tools.git_branch(arguments, confirmed), False

        # Web tools (NEW in v3.0)
        elif name == "web_fetch":
            return self.web_tools.web_fetch(arguments, confirmed), False

        elif name == "web_search":
            return self.web_tools.web_search(arguments, confirmed), False

        # GitHub tools
        elif name == "github_whoami":
            return self.github_tools.get_authenticated_user(arguments, confirmed), False

        elif name == "github_list_repos":
            return self.github_tools.list_repos(arguments, confirmed), False

        elif name == "github_get_repo":
            return self.github_tools.get_repo(arguments, confirmed), False

        elif name == "github_create_repo":
            result = self.github_tools.create_repo(arguments, confirmed)
            if result == "NEEDS_CONFIRMATION:github_create_repo":
                return arguments, True
            return result, False

        elif name == "github_list_issues":
            return self.github_tools.list_issues(arguments, confirmed), False

        elif name == "github_get_issue":
            return self.github_tools.get_issue(arguments, confirmed), False

        elif name == "github_create_issue":
            result = self.github_tools.create_issue(arguments, confirmed)
            if result == "NEEDS_CONFIRMATION:github_create_issue":
                return arguments, True
            return result, False

        elif name == "github_close_issue":
            result = self.github_tools.close_issue(arguments, confirmed)
            if result == "NEEDS_CONFIRMATION:github_close_issue":
                return arguments, True
            return result, False

        elif name == "github_list_prs":
            return self.github_tools.list_pull_requests(arguments, confirmed), False

        elif name == "github_get_pr":
            return self.github_tools.get_pull_request(arguments, confirmed), False

        elif name == "github_list_workflows":
            return self.github_tools.list_workflow_runs(arguments, confirmed), False

        elif name == "github_search_repos":
            return self.github_tools.search_repos(arguments, confirmed), False

        elif name == "github_search_issues":
            return self.github_tools.search_issues(arguments, confirmed), False

        # MCP tools (prefixed with mcp_)
        elif name.startswith("mcp_") or self.mcp_manager.has_tool(name):
            try:
                result = self.mcp_manager.execute_tool(name, arguments)
                # Format the MCP result
                if isinstance(result, dict):
                    content = result.get("content", [])
                    if content:
                        # Extract text from content blocks
                        text_parts = []
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                text_parts.append(item.get("text", ""))
                            elif isinstance(item, str):
                                text_parts.append(item)
                        return "\n".join(text_parts) if text_parts else json.dumps(result), False
                    return json.dumps(result), False
                return str(result), False
            except MCPTransportError as e:
                return f"MCP error: {e}", False
            except Exception as e:
                return f"MCP tool error: {e}", False

        else:
            return f"Unknown tool: {name}", False

    def _confirm_action(self, tool_name: str, arguments: dict) -> bool:
        """Ask user for confirmation before executing a tool."""
        print()

        if tool_name == "write_file":
            path = arguments.get("path", "")
            content = arguments.get("content", "")
            lines = content.count("\n") + 1
            print(f"{C.YELLOW}Write to: {C.BOLD}{path}{C.RESET} {C.DIM}({lines} lines){C.RESET}")

            # v4.0: Check for secrets
            secrets = self.secret_detector.scan(content)
            if secrets:
                critical = [s for s in secrets if s["severity"] == "critical"]
                if critical:
                    print(f"\n{C.RED}{C.BOLD}WARNING: Potential secrets detected!{C.RESET}")
                else:
                    print(f"\n{C.YELLOW}Warning: Potential sensitive data detected{C.RESET}")
                print(self.secret_detector.format_findings(secrets))
                print()

            print(f"{C.DIM}Preview:{C.RESET}")
            preview_lines = content.split("\n")[:15]
            for i, line in enumerate(preview_lines, 1):
                print(f"{C.DIM}{i:3}| {line[:100]}{C.RESET}")
            if len(content.split("\n")) > 15:
                print(f"{C.DIM}... ({len(content.split(chr(10))) - 15} more lines){C.RESET}")

        elif tool_name == "edit_file":
            path = arguments.get("path", "")
            old_text = arguments.get("old_text", "")
            new_text = arguments.get("new_text", "")
            print(f"{C.YELLOW}Edit: {C.BOLD}{path}{C.RESET}")

            # v4.0: Check for secrets in new content
            secrets = self.secret_detector.scan(new_text)
            if secrets:
                critical = [s for s in secrets if s["severity"] == "critical"]
                if critical:
                    print(f"\n{C.RED}{C.BOLD}WARNING: Potential secrets in new content!{C.RESET}")
                else:
                    print(f"\n{C.YELLOW}Warning: Potential sensitive data in new content{C.RESET}")
                print(self.secret_detector.format_findings(secrets))

            print()
            show_diff(old_text, new_text, path)

        elif tool_name == "run_command":
            cmd = arguments.get("command", "")
            print(f"{C.RED}{C.BOLD}Dangerous command:{C.RESET}")
            print(f"{C.YELLOW}  {cmd}{C.RESET}")

        elif tool_name == "git_commit":
            message = arguments.get("message", "")
            files = arguments.get("files")
            print(f"{C.YELLOW}Git commit:{C.RESET}")
            print(f"  Message: {message}")
            if files:
                print(f"  Files: {', '.join(files)}")
            else:
                print("  Files: (all changes)")

        response = input(f"\n{C.CYAN}Allow? [y/N/a(all)]:{C.RESET} ").strip().lower()

        if response == "a":
            self.auto_approve = True
            print_success("Auto-approve enabled for this session")
            return True

        return response in ("y", "yes")

    def _get_tool_detail(self, tool_name: str, arguments: dict) -> str:
        """Get a short detail string for tool call display."""
        if tool_name in ("read_file", "write_file", "edit_file"):
            return arguments.get("path", "")
        elif tool_name == "list_files":
            return arguments.get("pattern", "")
        elif tool_name == "search_files":
            return arguments.get("pattern", "")
        elif tool_name == "run_command":
            cmd = arguments.get("command", "")
            return cmd[:60] + ("..." if len(cmd) > 60 else "")
        elif tool_name == "git_status":
            return ""
        elif tool_name == "git_diff":
            return arguments.get("path", "") or ("staged" if arguments.get("staged") else "")
        elif tool_name == "git_log":
            return f"-{arguments.get('count', 10)}"
        elif tool_name == "git_commit":
            msg = arguments.get("message", "")
            return msg[:40] + ("..." if len(msg) > 40 else "")
        elif tool_name == "git_branch":
            return arguments.get("action", "list")
        # Web tools
        elif tool_name == "web_fetch":
            url = arguments.get("url", "")
            return url[:50] + ("..." if len(url) > 50 else "")
        elif tool_name == "web_search":
            query = arguments.get("query", "")
            return query[:40] + ("..." if len(query) > 40 else "")
        # MCP tools
        elif tool_name.startswith("mcp_"):
            # Extract the actual tool name after mcp_serverid_
            parts = tool_name.split("_", 2)
            if len(parts) >= 3:
                action = parts[2]
            else:
                action = tool_name
            # Get first significant argument
            for key in ["owner", "repo", "query", "title", "name", "path"]:
                if key in arguments:
                    val = str(arguments[key])
                    return f"{action}: {val[:30]}{'...' if len(val) > 30 else ''}"
            return action
        return ""

    # Read-only tools that can run in parallel safely
    READ_ONLY_TOOLS = {
        "read_file",
        "list_files",
        "search_files",
        "git_status",
        "git_diff",
        "git_log",
        "git_branch",
        "web_fetch",
        "web_search",
    }

    def _is_read_only_tool(self, tool_name: str) -> bool:
        """Check if a tool is read-only and safe for parallel execution."""
        if tool_name in self.READ_ONLY_TOOLS:
            return True
        # Most MCP read operations (list, get, search) are safe for parallel execution
        if tool_name.startswith("mcp_"):
            # Whitelist of known read-only MCP operations
            read_patterns = ("_list", "_get", "_search", "_read", "_view", "_fetch")
            for pattern in read_patterns:
                if pattern in tool_name:
                    return True
        return False

    async def _process_tool_calls_parallel(
        self, tool_calls: List[dict], messages: list
    ) -> List[str]:
        """
        Process multiple tool calls, running read-only tools in parallel.
        Returns list of tool names processed.
        """
        if not tool_calls:
            return []

        # Separate read-only (parallelizable) and write tools
        read_only_calls = []
        write_calls = []

        for tc in tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "")
            if self._is_read_only_tool(tool_name):
                read_only_calls.append(tc)
            else:
                write_calls.append(tc)

        tool_names = []

        # Process read-only calls in parallel
        if read_only_calls:
            tasks = [self._process_tool_call_async(tc) for tc in read_only_calls]
            results = await asyncio.gather(*tasks)

            # Add results to messages in order
            for tc, (tool_name, result) in zip(read_only_calls, results, strict=False):
                messages.append({"role": "tool", "tool_call_id": tc["id"], "content": str(result)})
                tool_names.append(tool_name)

        # Process write calls sequentially (they need confirmations)
        for tc in write_calls:
            tool_name = await self._process_tool_call(tc, messages)
            tool_names.append(tool_name)

        return tool_names

    async def _process_tool_call_async(self, tool_call: dict) -> Tuple[str, Any]:
        """Process a single tool call asynchronously. Returns (tool_name, result)."""
        func = tool_call["function"]
        tool_name = func["name"]

        try:
            arguments = json.loads(func["arguments"])
        except json.JSONDecodeError:
            arguments = {}

        # Show tool usage
        detail = self._get_tool_detail(tool_name, arguments)
        print_tool_call(tool_name, detail)

        # Execute the tool (read-only tools don't need confirmation)
        loop = asyncio.get_event_loop()
        result, _ = await loop.run_in_executor(
            None, lambda: self._execute_tool(tool_name, arguments)
        )

        print_tool_result(str(result))
        return tool_name, result

    async def _make_api_call(
        self,
        client: httpx.AsyncClient,
        url: str,
        headers: Dict[str, str],
        payload: Dict[str, Any],
        on_content: Optional[Callable[[str], None]] = None,
        use_streaming: bool = True,
    ) -> StreamingResponse:
        """Make an API call with retry logic."""
        last_error = None

        for attempt in range(self.max_retries):
            try:
                if use_streaming and self.stream_responses:
                    return await stream_chat_completion(
                        client, url, headers, payload, on_content=on_content
                    )
                else:
                    return await non_streaming_chat_completion(client, url, headers, payload)

            except httpx.RequestError as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    delay = self.retry_delay * (2**attempt)
                    print(f"{C.DIM}  Connection error, retrying in {delay:.1f}s...{C.RESET}")
                    await asyncio.sleep(delay)
                    continue

            except Exception as e:
                error_msg = str(e)
                # Check for retryable errors
                if "500" in error_msg or "502" in error_msg or "503" in error_msg:
                    last_error = e
                    if attempt < self.max_retries - 1:
                        delay = self.retry_delay * (2**attempt)
                        print(f"{C.DIM}  Server error, retrying in {delay:.1f}s...{C.RESET}")
                        await asyncio.sleep(delay)
                        continue
                raise

        raise Exception(f"API call failed after {self.max_retries} attempts: {last_error}")

    async def chat(
        self, user_message: str, on_content: Optional[Callable[[str], None]] = None
    ) -> str:
        """
        Send a message and handle the full tool-calling loop.

        Args:
            user_message: The user's input message
            on_content: Optional callback for streaming content chunks

        Returns:
            The final assistant response
        """
        token = await self.get_token()
        url = f"{CHAT_BASE_URL}/{self.model}/chat/completions?api-version={API_VERSION}"
        headers = {"Content-Type": "application/json", "api-key": token}

        # v4.0: Log user input
        self.audit_logger.log_user_input(user_message)

        # Build messages with system prompt (include thinking mode if enabled)
        system_content = self.system_prompt + self._get_thinking_prompt()
        messages: List[Dict[str, Any]] = [{"role": "system", "content": system_content}]
        messages.extend(self.history)
        messages.append({"role": "user", "content": user_message})

        # v4.0: Optimize context if needed
        if len(messages) > 30:
            messages, opt_stats = self.context_manager.optimize_context(messages)
            if opt_stats.get("tokens_saved", 0) > 1000:
                print(
                    f"{C.DIM}  [Context optimized: {opt_stats['tokens_saved']:,} tokens saved]{C.RESET}"
                )

        iteration = 0
        max_iterations = 25
        user_msg_added = False
        accumulated_content = ""

        # Reset last token counts
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0

        async with httpx.AsyncClient(verify=ssl_config.get_verify_param(), timeout=180.0) as client:
            while iteration < max_iterations:
                iteration += 1

                # Get all tools including MCP tools
                all_tools = self.get_all_tools()

                payload: Dict[str, Any] = {
                    "messages": messages,
                    "user": json.dumps({"appkey": self.app_key}),
                    "temperature": 0.7,
                    "max_tokens": 4096,
                    "tools": all_tools,
                    "tool_choice": "auto",
                }

                # Stream ALL responses, not just the first one
                response = await self._make_api_call(
                    client, url, headers, payload, on_content=on_content, use_streaming=True
                )

                # Track tokens
                self.last_prompt_tokens += response.prompt_tokens
                self.last_completion_tokens += response.completion_tokens
                self.session_prompt_tokens += response.prompt_tokens
                self.session_completion_tokens += response.completion_tokens

                # v4.0: Track costs and audit
                self.cost_tracker.track(
                    self.model, response.prompt_tokens, response.completion_tokens
                )
                self.audit_logger.log_api_call(
                    self.model, response.prompt_tokens, response.completion_tokens
                )

                # Add user message to history on first successful response
                if not user_msg_added:
                    self.history.append({"role": "user", "content": user_message})
                    user_msg_added = True

                # Accumulate content
                if response.content:
                    accumulated_content += response.content

                # If no tool calls, we're done
                if not response.has_tool_calls():
                    if accumulated_content:
                        self.history.append({"role": "assistant", "content": accumulated_content})
                    return accumulated_content

                # Process tool calls (with parallel execution for read-only tools)
                tool_calls_dict = response.get_tool_calls_dict()
                messages.append(
                    {
                        "role": "assistant",
                        "content": response.content or None,
                        "tool_calls": tool_calls_dict,
                    }
                )

                # Use parallel processing for multiple tool calls
                tool_names = await self._process_tool_calls_parallel(tool_calls_dict, messages)

                # Add tool summary to history (not the full tool_calls object)
                self.history.append(
                    {"role": "assistant", "content": f"[Used tools: {', '.join(tool_names)}]"}
                )

                # Print newline after tools before next response
                if on_content:
                    on_content("\n")

        return accumulated_content or "Maximum iterations reached. Please try a simpler request."

    async def _process_tool_call(self, tool_call: dict, messages: list) -> str:
        """Process a single tool call and add result to messages. Returns tool name."""
        func = tool_call["function"]
        tool_name = func["name"]

        try:
            arguments = json.loads(func["arguments"])
        except json.JSONDecodeError:
            arguments = {}

        # Show tool usage
        detail = self._get_tool_detail(tool_name, arguments)
        print_tool_call(tool_name, detail)

        # Execute the tool
        result, needs_confirmation = self._execute_tool(tool_name, arguments)

        if needs_confirmation:
            if self._confirm_action(tool_name, result):
                # Execute with confirmation using modular tools
                if tool_name == "write_file":
                    result = self.file_tools.write_file(result, confirmed=True)
                elif tool_name == "edit_file":
                    result = self.file_tools.edit_file(result, confirmed=True)
                elif tool_name == "run_command":
                    result = self.file_tools.run_command(result, confirmed=True)
                elif tool_name == "git_commit":
                    result = self.git_tools.git_commit(result, confirmed=True)
            else:
                result = "Action cancelled by user"

        print_tool_result(str(result))

        # v4.0: Log tool call
        self.audit_logger.log_tool_call(tool_name, arguments, str(result), success=True)

        # Add tool result to messages
        messages.append({"role": "tool", "tool_call_id": tool_call["id"], "content": str(result)})

        return tool_name

    def clear_history(self):
        """Clear conversation history."""
        self.history = []

    def get_token_stats(self) -> Dict[str, int]:
        """Get token usage statistics."""
        return {
            "last_prompt": self.last_prompt_tokens,
            "last_completion": self.last_completion_tokens,
            "last_total": self.last_prompt_tokens + self.last_completion_tokens,
            "session_prompt": self.session_prompt_tokens,
            "session_completion": self.session_completion_tokens,
            "session_total": self.session_prompt_tokens + self.session_completion_tokens,
        }

    def set_auto_approve(self, enabled: bool):
        """Enable or disable auto-approve mode."""
        self.auto_approve = enabled

    # Session management methods
    def save_session(self, name: str) -> Tuple[bool, str]:
        """Save current session."""
        return self.session_manager.save(
            name=name,
            history=self.history,
            model=self.model,
            working_dir=self.working_dir,
            auto_approve=self.auto_approve,
        )

    def load_session(self, name: str) -> Tuple[bool, str]:
        """Load a saved session."""
        success, data = self.session_manager.load(name)
        if not success:
            return False, data

        self.history = data.get("history", [])
        self.model = data.get("model", self.model)
        self.auto_approve = data.get("auto_approve", False)

        return True, f"Loaded session: {name} ({len(self.history)} messages)"

    def list_sessions(self) -> List[Dict[str, Any]]:
        """List all saved sessions."""
        return self.session_manager.list_sessions()

    def delete_session(self, name: str) -> Tuple[bool, str]:
        """Delete a saved session."""
        return self.session_manager.delete(name)

    # Context compaction methods
    def get_compaction_stats(self) -> Dict[str, Any]:
        """Get stats about current history and potential compaction."""
        return self.compactor.get_compaction_stats(self.history)

    def compact_history(self, use_llm: bool = False) -> Tuple[bool, str]:
        """Compact conversation history to reduce tokens."""
        if not self.compactor.needs_compaction(self.history):
            return False, "History doesn't need compaction yet"

        if use_llm:
            # TODO: Implement LLM-based summarization
            pass

        self.history, stats = self.compactor.compact(self.history)
        return True, stats

    # v4.0: Cost tracking methods
    def get_cost_stats(self) -> Dict[str, Any]:
        """Get detailed cost statistics."""
        return self.cost_tracker.get_stats()

    def get_cost_summary(self) -> str:
        """Get formatted cost summary."""
        return self.cost_tracker.format_stats()

    # v4.0: Audit methods
    def get_audit_stats(self) -> Dict[str, Any]:
        """Get audit log statistics."""
        return self.audit_logger.get_session_stats()

    def get_recent_audit_entries(self, count: int = 10) -> List[Dict]:
        """Get recent audit log entries."""
        return self.audit_logger.get_recent_entries(count)

    # v4.0: Security methods
    def scan_for_secrets(self, content: str) -> List[Dict]:
        """Scan content for potential secrets."""
        return self.secret_detector.scan(content)

    def set_thinking_mode(self, enabled: bool):
        """Enable or disable thinking mode."""
        self.thinking_mode = enabled
