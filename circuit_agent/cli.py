"""
CLI interface for Circuit Agent v4.0.
Main loop, slash commands, headless mode, and user interaction.
"""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Optional

from .agent import CircuitAgent
from .config import (
    CONFIG_FILE,
    MODELS,
    delete_credentials,
    get_circuit_md_locations,
    get_config_summary,
    load_credentials,
)
from prompt_toolkit import PromptSession
from prompt_toolkit.application import Application
from prompt_toolkit.completion import Completer, Completion
from prompt_toolkit.history import FileHistory
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import Float, FloatContainer, HSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.layout.menus import CompletionsMenu
from prompt_toolkit.styles import Style
from prompt_toolkit.widgets import Frame, TextArea


SLASH_COMMANDS = [
    ("/help", "Show help"),
    ("/clear", "Clear conversation history"),
    ("/history", "Show recent conversation"),
    ("/files", "List files in working directory"),
    ("/model", "Change model"),
    ("/tokens", "Show token usage for session"),
    ("/undo", "Restore file from backup"),
    ("/config", "Show current configuration"),
    ("/git", "Quick git status"),
    ("/auto", "Toggle auto-approve mode"),
    ("/stream", "Toggle response streaming"),
    ("/logout", "Delete saved credentials"),
    ("/save", "Save current session"),
    ("/load", "Load a saved session"),
    ("/sessions", "List all saved sessions"),
    ("/compact", "Compress old messages to save tokens"),
    ("/cost", "Show estimated API cost for session"),
    ("/audit", "Show audit log statistics"),
    ("/think", "Toggle thinking mode (on|off)"),
    ("/quit", "Exit"),
]


class SlashCommandCompleter(Completer):
    """Pop up the slash-command menu whenever the line starts with `/`."""

    def get_completions(self, document, complete_event):
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        # Only complete while typing the command itself, not its arguments
        if " " in text:
            return
        word = text.lower()
        for cmd, desc in SLASH_COMMANDS:
            if cmd.startswith(word):
                yield Completion(
                    cmd,
                    start_position=-len(word),
                    display=cmd,
                    display_meta=desc,
                )

from .ui import (
    C,
    StreamingMarkdownRenderer,
    clear_line,
    clear_screen,
    console,
    print_error,
    print_header,
    print_help,
    print_info,
    print_success,
    print_token_usage,
    print_warning,
    print_welcome,
    render_markdown,
    thinking_status,
)


HISTORY_PATH = os.path.expanduser("~/.config/circuit-agent/input-history")


def _build_input_app(agent, working_dir: str, history: FileHistory) -> Application:
    """Claude-Code-style bordered input box.

    Renders inline (not full-screen) so prior output keeps scrolling above.
    Enter submits, Alt+Enter inserts a newline, Ctrl+C / Ctrl+D abort.
    """
    text_area = TextArea(
        multiline=True,
        wrap_lines=True,
        prompt="> ",
        history=history,
        style="class:input",
        focus_on_click=True,
        height=Dimension(min=1, max=8),
        dont_extend_height=True,
        completer=SlashCommandCompleter(),
        complete_while_typing=True,
    )

    kb = KeyBindings()

    @kb.add("c-c")
    def _(event):
        event.app.exit(exception=KeyboardInterrupt)

    @kb.add("c-d")
    def _(event):
        if not text_area.text:
            event.app.exit(exception=EOFError)

    @kb.add("enter")
    def _(event):
        event.app.exit(result=text_area.text)

    @kb.add("escape", "enter")
    def _(event):
        text_area.buffer.insert_text("\n")

    home = os.path.expanduser("~")
    cwd_display = working_dir.replace(home, "~") if working_dir.startswith(home) else working_dir

    def toolbar_fragments():
        auto_on = getattr(agent, "auto_approve", False)
        parts = [
            ("class:dim", "  ! "),
            ("class:hint", "for bash"),
            ("class:dim", "   · "),
            ("class:dim", " / "),
            ("class:hint", "for commands"),
            ("class:dim", "   · "),
            ("class:hint", f"model: "),
            ("class:meta", agent.model),
            ("class:dim", "   · "),
            ("class:hint", f"cwd: {cwd_display}"),
        ]
        if auto_on:
            parts += [("class:dim", "   · "), ("class:warn", "auto-approve ON")]
        return parts

    toolbar = Window(
        FormattedTextControl(toolbar_fragments),
        height=1,
        style="class:bottom-toolbar",
    )

    root = FloatContainer(
        content=HSplit(
            [
                Frame(text_area, style="class:frame"),
                toolbar,
            ]
        ),
        floats=[
            Float(
                xcursor=True,
                ycursor=True,
                content=CompletionsMenu(max_height=12, scroll_offset=1),
            ),
        ],
    )

    style = Style.from_dict(
        {
            "frame.border": "fg:#5f5f5f",
            "input": "",
            "input prompt": "bold fg:#d97757",
            "dim": "fg:#5f5f5f",
            "hint": "fg:#9e9e9e",
            "meta": "fg:#d97757",
            "warn": "fg:ansiyellow bold",
            "bottom-toolbar": "noreverse",
            "completion-menu": "bg:#262626 fg:#e0e0e0",
            "completion-menu.completion": "bg:#262626 fg:#e0e0e0",
            "completion-menu.completion.current": "bg:#d97757 fg:#000000 bold",
            "completion-menu.meta.completion": "bg:#262626 fg:#9e9e9e",
            "completion-menu.meta.completion.current": "bg:#d97757 fg:#1a1a1a",
            "scrollbar.background": "bg:#3a3a3a",
            "scrollbar.button": "bg:#d97757",
        }
    )

    return Application(
        layout=Layout(root),
        key_bindings=kb,
        style=style,
        full_screen=False,
        mouse_support=False,
        erase_when_done=False,
    )


async def _prompt_user(agent, working_dir: str, history: FileHistory) -> str:
    """Run one round of the bordered input box and return the submitted text."""
    app = _build_input_app(agent, working_dir, history)
    return await app.run_async()


async def run_cli(working_dir: Optional[str] = None):
    """Main CLI entry point for interactive mode."""
    # Determine working directory if not provided
    if working_dir is None:
        working_dir = os.getcwd()

    if not os.path.isdir(working_dir):
        print(f"{C.RED}Error: '{working_dir}' is not a valid directory{C.RESET}")
        sys.exit(1)

    working_dir = os.path.abspath(working_dir)

    clear_screen()
    print_header(working_dir)

    # Load credentials
    client_id, client_secret, app_key = load_credentials()
    is_first_run = False

    if client_id and client_secret and app_key:
        print_success("Using saved credentials")
    else:
        print(f"""{C.BOLD}Enter your Cisco Circuit credentials:{C.RESET}

  Get these from: {C.CYAN}https://developer.cisco.com/site/ai-ml/{C.RESET}
  → Manage Circuit API Keys → View
""")
        if not client_id:
            client_id = input(f"  {C.CYAN}Client ID:{C.RESET} ").strip()
        if not client_secret:
            # PromptSession.prompt_async — sync pt_prompt() tries to start its
            # own asyncio loop and conflicts with the one already running here.
            secret_session = PromptSession()
            client_secret = (
                await secret_session.prompt_async("  Client Secret: ", is_password=True)
            ).strip()
        if not app_key:
            # Same asyncio-loop reasoning as Client Secret above; mask so the
            # key doesn't end up in shell scrollback.
            app_key_session = PromptSession()
            app_key = (
                await app_key_session.prompt_async("  App Key: ", is_password=True)
            ).strip()
        is_first_run = True

    if not all([client_id, client_secret, app_key]):
        print_error("All credentials are required")
        sys.exit(1)

    # Test connection
    print_info("Testing connection...")
    agent = CircuitAgent(client_id, client_secret, app_key, working_dir)

    try:
        await agent.get_token()
        print_success("Authentication successful!")

        # Persist creds to ~/.circuit-agent/.env (the canonical store) and
        # auto-spawn the proxy in the background. Idempotent on both fronts.
        env_file = write_env_file(client_id, client_secret, app_key)
        if is_first_run:
            print_success(f"Credentials saved to {env_file}")
        _ensure_proxy_running()

    except Exception as e:
        print_error(f"Authentication failed: {e}")
        sys.exit(1)

    # Select model
    print(f"\n{C.BOLD}Select a model:{C.RESET}\n")
    for k, (_, desc) in MODELS.items():
        print(f"  {C.CYAN}{k}){C.RESET} {desc}")

    choice = input(f"\n  Choice [{C.GREEN}2{C.RESET}]: ").strip() or "2"
    if choice in MODELS:
        agent.model = MODELS[choice][0]
    print_success(f"Using {agent.model}")

    # Show welcome message
    print_welcome()

    # Main chat loop
    await chat_loop(agent, working_dir)


async def chat_loop(agent: CircuitAgent, working_dir: str):
    """Main chat loop — Claude-Code-style input + streaming output."""
    os.makedirs(os.path.dirname(HISTORY_PATH), exist_ok=True)
    history = FileHistory(HISTORY_PATH)

    while True:
        try:
            user_input = (await _prompt_user(agent, working_dir, history)).strip()

            if not user_input:
                continue

            if user_input.startswith("/"):
                cmd_result = handle_command(user_input, agent, working_dir)
                if cmd_result == "quit":
                    break
                elif cmd_result == "continue":
                    continue

            console.print()  # blank line between user input and response

            first_chunk = [True]
            md_renderer = StreamingMarkdownRenderer()
            status_ctx = thinking_status("Thinking…")
            status_ctx.__enter__()
            status_stopped = [False]

            def on_content(chunk: str, _first=first_chunk, _md=md_renderer):
                if _first[0]:
                    if not status_stopped[0]:
                        status_ctx.__exit__(None, None, None)
                        status_stopped[0] = True
                    _first[0] = False
                _md.feed(chunk)

            try:
                response = await agent.chat(user_input, on_content=on_content)
            finally:
                if not status_stopped[0]:
                    status_ctx.__exit__(None, None, None)
                    status_stopped[0] = True

            if not first_chunk[0]:
                md_renderer.flush()
            elif response:
                render_markdown(response)

            stats = agent.get_token_stats()
            if stats["last_total"] > 0:
                print_token_usage(
                    stats["last_prompt"],
                    stats["last_completion"],
                    stats["session_prompt"],
                    stats["session_completion"],
                )

        except KeyboardInterrupt:
            console.print()
            console.print("[grey50]Goodbye![/grey50]")
            break
        except EOFError:
            console.print()
            console.print("[grey50]Goodbye![/grey50]")
            break
        except Exception as e:
            print_error(str(e))


def handle_command(user_input: str, agent: CircuitAgent, working_dir: str) -> str:
    """
    Handle a slash command.
    Returns: "quit" to exit, "continue" to skip to next iteration, None to fall through
    """
    parts = user_input.lower().split()
    cmd = parts[0]
    args = parts[1:] if len(parts) > 1 else []

    # Quit commands
    if cmd in ["/quit", "/exit", "/q"]:
        print(f"\n{C.CYAN}Goodbye!{C.RESET}\n")
        return "quit"

    # Clear history
    elif cmd in ["/clear", "/c"]:
        agent.clear_history()
        print_success("Conversation cleared")
        return "continue"

    # Show history
    elif cmd == "/history":
        if not agent.history:
            print_info("No conversation history")
        else:
            print(f"\n{C.BOLD}Recent conversation:{C.RESET}")
            for msg in agent.history[-10:]:
                role = msg.get("role", "unknown").upper()
                content = msg.get("content", "")
                if role == "USER":
                    print(
                        f"  {C.BLUE}[USER]{C.RESET} {content[:80]}{'...' if len(content) > 80 else ''}"
                    )
                elif role == "ASSISTANT":
                    preview = content[:80].replace("\n", " ")
                    print(
                        f"  {C.MAGENTA}[AGENT]{C.RESET} {preview}{'...' if len(content) > 80 else ''}"
                    )
        return "continue"

    # List files
    elif cmd == "/files":
        files = list(Path(working_dir).glob("*"))
        print(f"\n{C.DIM}Files in {working_dir}:{C.RESET}")
        for f in sorted(files)[:20]:
            icon = "📁" if f.is_dir() else "📄"
            print(f"  {icon} {f.name}")
        if len(files) > 20:
            print_info(f"... and {len(files) - 20} more")
        return "continue"

    # Change model
    elif cmd == "/model":
        print(f"\n{C.BOLD}Select a model:{C.RESET}")
        for k, (_, desc) in MODELS.items():
            marker = " ←" if MODELS[k][0] == agent.model else ""
            print(f"  {C.CYAN}{k}){C.RESET} {desc}{C.GREEN}{marker}{C.RESET}")
        choice = input("\n  Choice: ").strip()
        if choice in MODELS:
            agent.model = MODELS[choice][0]
            print_success(f"Switched to {agent.model}")
        return "continue"

    # Token usage
    elif cmd == "/tokens":
        stats = agent.get_token_stats()
        print(f"\n{C.BOLD}Token Usage:{C.RESET}")
        print(
            f"  Last request:  {stats['last_prompt']:,} in / {stats['last_completion']:,} out ({stats['last_total']:,} total)"
        )
        print(
            f"  Session total: {stats['session_prompt']:,} in / {stats['session_completion']:,} out ({stats['session_total']:,} total)"
        )
        return "continue"

    # Undo
    elif cmd in ["/undo", "/u"]:
        backup_manager = agent.backup_manager

        # If path specified, undo that file
        if args:
            path = args[0]
            success, message = backup_manager.restore(path)
            if success:
                print_success(message)
            else:
                print_error(message)
        else:
            # Undo last modified file
            last_modified = backup_manager.get_last_modified()
            if last_modified:
                print_info(f"Last modified: {last_modified}")
                confirm = input(f"  {C.CYAN}Restore this file? [y/N]:{C.RESET} ").strip().lower()
                if confirm in ("y", "yes"):
                    success, message = backup_manager.restore(last_modified)
                    if success:
                        print_success(message)
                    else:
                        print_error(message)
                else:
                    print_info("Cancelled")
            else:
                print_info("No files to undo")

            # Show available backups
            backups = backup_manager.list_backups()
            if backups:
                print(f"\n{C.DIM}Files with backups:{C.RESET}")
                for path, count in backups.items():
                    print(f"  {path} ({count} backup{'s' if count > 1 else ''})")
        return "continue"

    # Configuration
    elif cmd == "/config":
        summary = get_config_summary()
        circuit_md = get_circuit_md_locations(working_dir)

        print(f"\n{C.BOLD}Configuration:{C.RESET}")
        print(f"  Config dir:  {summary['config_dir']}")
        print(f"  Credentials: {'Saved' if summary['credentials_saved'] else 'Not saved'}")
        if summary["client_id_preview"]:
            print(f"  Client ID:   {summary['client_id_preview']}")

        print(f"\n{C.BOLD}CIRCUIT.md:{C.RESET}")
        print(f"  Project: {circuit_md['project_path']}")
        print(f"           {'✓ Found' if circuit_md['project'] else '✗ Not found'}")
        print(f"  Global:  {circuit_md['global_path']}")
        print(f"           {'✓ Found' if circuit_md['global'] else '✗ Not found'}")

        print(f"\n{C.BOLD}Current session:{C.RESET}")
        print(f"  Model:       {agent.model}")
        print(f"  Streaming:   {'Enabled' if agent.stream_responses else 'Disabled'}")
        print(f"  Auto-approve: {C.YELLOW + 'ON' + C.RESET if agent.auto_approve else 'Off'}")
        print(f"  History:     {len(agent.history)} messages")
        return "continue"

    # Logout
    elif cmd == "/logout":
        if delete_credentials():
            print_success("Saved credentials deleted")
            print_info("You'll need to re-enter credentials next time")
        else:
            print_warning("No saved credentials found")
        return "continue"

    # Help
    elif cmd in ["/help", "/h"]:
        print_help()
        return "continue"

    # Streaming toggle
    elif cmd == "/stream":
        agent.stream_responses = not agent.stream_responses
        status = "enabled" if agent.stream_responses else "disabled"
        print_success(f"Streaming {status}")
        return "continue"

    # Auto-approve toggle
    elif cmd == "/auto":
        agent.auto_approve = not agent.auto_approve
        if agent.auto_approve:
            print_warning(
                "Auto-approve ENABLED - all actions will be executed without confirmation"
            )
        else:
            print_success("Auto-approve disabled - confirmations required")
        return "continue"

    # Git status shortcut
    elif cmd == "/git":
        result = agent.git_tools.git_status({}, False)
        print(f"\n{result}")
        return "continue"

    # Session save
    elif cmd == "/save":
        if not args:
            # Generate name from timestamp
            from datetime import datetime

            name = datetime.now().strftime("%Y%m%d-%H%M%S")
        else:
            name = args[0]

        success, message = agent.save_session(name)
        if success:
            print_success(message)
        else:
            print_error(message)
        return "continue"

    # Session load
    elif cmd == "/load":
        if not args:
            # Show available sessions
            sessions = agent.list_sessions()
            if not sessions:
                print_info("No saved sessions found")
            else:
                print(f"\n{C.BOLD}Saved sessions:{C.RESET}")
                for i, s in enumerate(sessions[:10], 1):
                    print(f"  {i}. {s['name']} ({s['message_count']} msgs, {s['model']})")
                print("\n  Use: /load <name>")
        else:
            name = args[0]
            success, message = agent.load_session(name)
            if success:
                print_success(message)
            else:
                print_error(message)
        return "continue"

    # List sessions
    elif cmd == "/sessions":
        sessions = agent.list_sessions()
        if not sessions:
            print_info("No saved sessions found")
        else:
            print(f"\n{C.BOLD}Saved sessions:{C.RESET}")
            for s in sessions[:15]:
                created = s["created_at"][:10] if len(s["created_at"]) > 10 else s["created_at"]
                print(f"  {C.CYAN}{s['name']}{C.RESET}")
                print(f"    {created} | {s['message_count']} msgs | {s['model']}")
            if len(sessions) > 15:
                print(f"\n  ... and {len(sessions) - 15} more")
        return "continue"

    # Context compaction
    elif cmd == "/compact":
        stats = agent.get_compaction_stats()
        print(f"\n{C.BOLD}Context stats:{C.RESET}")
        print(f"  Messages: {stats['message_count']}")
        print(f"  Est. tokens: ~{stats['estimated_tokens']:,}")

        if stats["needs_compaction"]:
            print(f"\n  Would compact: {stats['would_compact']} msgs → summary")
            print(f"  Would keep: {stats['would_keep']} recent msgs")

            confirm = input(f"\n  {C.CYAN}Compact now? [y/N]:{C.RESET} ").strip().lower()
            if confirm in ("y", "yes"):
                success, message = agent.compact_history()
                if success:
                    print_success(message)
                else:
                    print_info(message)
        else:
            print_info("No compaction needed yet")
        return "continue"

    # v4.0: Cost tracking
    elif cmd == "/cost":
        print(f"\n{C.BOLD}Session Cost:{C.RESET}")
        print(f"  {agent.get_cost_summary()}")
        return "continue"

    # v4.0: Audit log
    elif cmd == "/audit":
        stats = agent.get_audit_stats()
        if not stats.get("enabled"):
            print_info("Audit logging is disabled")
        else:
            print(f"\n{C.BOLD}Audit Log:{C.RESET}")
            print(f"  Session: {stats.get('session_id', 'unknown')}")
            print(f"  Entries: {stats.get('entries', 0)}")
            print(f"  Log file: {stats.get('log_file', 'N/A')}")

            action_counts = stats.get("action_counts", {})
            if action_counts:
                print(f"\n  {C.BOLD}Actions:{C.RESET}")
                for action, count in sorted(action_counts.items()):
                    print(f"    {action}: {count}")

            if args and args[0] == "recent":
                print(f"\n  {C.BOLD}Recent entries:{C.RESET}")
                entries = agent.get_recent_audit_entries(5)
                for entry in entries:
                    ts = entry.get("timestamp", "")[:19]
                    action = entry.get("action", "unknown")
                    print(f"    [{ts}] {action}")
        return "continue"

    # v4.0: Thinking mode toggle
    elif cmd == "/think":
        if args and args[0] in ("on", "off"):
            enabled = args[0] == "on"
            agent.set_thinking_mode(enabled)
            if enabled:
                print_success("Thinking mode enabled - agent will show reasoning")
            else:
                print_success("Thinking mode disabled")
        else:
            current = "on" if agent.thinking_mode else "off"
            print(f"\n{C.BOLD}Thinking Mode:{C.RESET} {current}")
            print("  Use: /think on  or  /think off")
        return "continue"

    # Unknown command
    else:
        print_warning("Unknown command. Type /help for help.")
        return "continue"


async def run_headless(
    prompt: str,
    working_dir: str,
    auto_approve: bool = False,
    output_format: str = "text",
    model: str = "gpt-5-nano",
    max_iterations: int = 25,
) -> int:
    """
    Run agent in headless/CI mode with a single prompt.

    Args:
        prompt: The prompt to execute
        working_dir: Working directory
        auto_approve: Skip all confirmations
        output_format: Output format (text, json, markdown)
        model: Model to use
        max_iterations: Maximum tool call iterations

    Returns:
        Exit code (0 for success, 1 for error)
    """
    # Load credentials
    client_id, client_secret, app_key = load_credentials()

    if not all([client_id, client_secret, app_key]):
        if output_format == "json":
            print(json.dumps({"error": "Missing credentials", "success": False}))
        else:
            print(
                "Error: Missing credentials. Run in interactive mode first to set up.",
                file=sys.stderr,
            )
        return 1

    # Create agent
    agent = CircuitAgent(client_id, client_secret, app_key, working_dir)
    agent.model = model
    agent.auto_approve = auto_approve

    # Collect output
    output_parts = []

    def on_content(chunk: str):
        if output_format == "text":
            print(chunk, end="", flush=True)
        else:
            output_parts.append(chunk)

    try:
        # Authenticate
        await agent.get_token()

        # Run the prompt
        response = await agent.chat(
            prompt, on_content=on_content if output_format == "text" else None
        )

        if output_format == "json":
            result = {
                "success": True,
                "response": response,
                "tokens": agent.get_token_stats(),
                "cost": agent.get_cost_stats(),
            }
            print(json.dumps(result, indent=2))
        elif output_format == "markdown":
            print(f"# Agent Response\n\n{response}\n")
            print(
                f"---\n*Tokens: {agent.get_token_stats()['session_total']:,} | Cost: ${agent.get_cost_stats()['estimated_cost_usd']:.4f}*"
            )
        else:
            # Text format - already printed via on_content
            if not output_parts:
                print()  # Newline after streamed content
            stats = agent.get_token_stats()
            print(
                f"\n[Tokens: {stats['session_total']:,} | Cost: ${agent.get_cost_stats()['estimated_cost_usd']:.4f}]",
                file=sys.stderr,
            )

        return 0

    except Exception as e:
        if output_format == "json":
            print(json.dumps({"error": str(e), "success": False}))
        else:
            print(f"Error: {e}", file=sys.stderr)
        return 1


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        prog="circuit-agent",
        description="Circuit Agent v4.0 - AI-Powered Coding Assistant",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Interactive mode
  circuit-agent

  # Headless mode with single prompt
  circuit-agent -p "Fix all TypeScript errors"

  # Auto-approve all actions (for CI/CD)
  circuit-agent -p "Run tests and fix failures" --auto-approve

  # JSON output for scripting
  circuit-agent -p "List all TODO comments" --output json

  # Specify working directory
  circuit-agent /path/to/project -p "Analyze the codebase"
""",
    )

    parser.add_argument(
        "directory", nargs="?", default=None, help="Working directory (default: current directory)"
    )

    parser.add_argument("-p", "--prompt", help="Single prompt to execute (enables headless mode)")

    parser.add_argument("--prompt-file", type=str, help="Read prompt from file")

    parser.add_argument(
        "--auto-approve",
        "-y",
        action="store_true",
        help="Auto-approve all actions (skip confirmations)",
    )

    parser.add_argument(
        "--output",
        "-o",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format (default: text)",
    )

    parser.add_argument(
        "--model",
        "-m",
        choices=["gemini-3.1-flash-lite", "gpt-5-nano"],
        default="gemini-3.1-flash-lite",
        help="Model to use (default: gemini-3.1-flash-lite)",
    )

    parser.add_argument("--version", "-v", action="store_true", help="Show version and exit")
    parser.add_argument(
        "--upgrade",
        "--update",
        action="store_true",
        help="Pull the latest circuit-agent from GitHub into this venv, then exit",
    )

    return parser.parse_args()


REPO_GIT_URL = "git+https://github.com/mosswrat/circuit-cli.git"


def _upgrade_in_place() -> int:
    """Run `pip install --upgrade --force-reinstall --no-cache-dir` for our
    package URL inside the venv that's running this Python. Returns the
    pip exit code. The --no-cache-dir flag is the load-bearing one — pip
    aggressively reuses cached wheels of git+ URLs even when the underlying
    commit has changed, so without it `--upgrade` often becomes a no-op."""
    import subprocess

    venv_bin = Path(sys.executable).parent
    pip_exe = venv_bin / ("pip.exe" if sys.platform == "win32" else "pip")
    if not pip_exe.exists():
        print(f"Error: cannot find pip at {pip_exe}", file=sys.stderr)
        return 1

    cmd = [
        str(pip_exe),
        "install",
        "--upgrade",
        "--force-reinstall",
        "--no-cache-dir",
        REPO_GIT_URL,
    ]
    print(f"Upgrading circuit-agent from {REPO_GIT_URL}")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print(f"\nUpgrade failed (pip exit {result.returncode}).", file=sys.stderr)
        return result.returncode

    # Show the new version by invoking the freshly-installed entry point.
    # We can't `from . import __version__` again — the in-memory module is
    # stale. Subprocess gets a fresh import.
    agent_exe = venv_bin / ("circuit-agent.exe" if sys.platform == "win32" else "circuit-agent")
    try:
        out = subprocess.check_output([str(agent_exe), "--version"], text=True).strip()
        print(f"\n==> {out}")
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return 0


def _env_file_path() -> Path:
    config_dir = Path(os.environ.get("CIRCUIT_AGENT_HOME") or (Path.home() / ".circuit-agent"))
    return config_dir / ".env"


def _load_env_file_silently() -> None:
    """If ~/.circuit-agent/.env exists, bridge it into os.environ.

    Called at agent startup so returning users don't re-prompt. Silent
    on miss — no prints, no errors. The in-TUI flow handles first-run
    credential collection. Existing env vars are not overwritten.
    """
    env_file = _env_file_path()
    if not env_file.exists():
        return
    try:
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip()
            if key and val and key not in os.environ:
                os.environ[key] = val
    except OSError as exc:
        # File exists but is unreadable (perms, I/O error, etc.) — surface
        # it so the user knows why their saved creds aren't loading. The
        # missing-file case is handled by the early-return above.
        print(f"Warning: could not read {env_file}: {exc}", file=sys.stderr)


def write_env_file(client_id: str, client_secret: str, app_key: str, model: str = "gpt-5-nano") -> Path:
    """Persist Cisco credentials to ~/.circuit-agent/.env so circuit-proxy
    (and any other tool pointing at it) can find them. Called from the
    in-TUI credential flow after agent.get_token() validates the values."""
    env_file = _env_file_path()
    config_dir = env_file.parent
    config_dir.mkdir(parents=True, exist_ok=True)

    if env_file.exists():
        # Tell the user before we clobber their old creds — no backup is kept
        # (we don't want stale secrets lying around) but silent replacement
        # is surprising. The in-TUI flow lands here every successful auth.
        print(f"note: overwriting existing {env_file}", file=sys.stderr)

    # chmod is a no-op on Windows; skip the call instead of swallowing the
    # NotImplementedError. On POSIX, an actual failure is security-relevant
    # (creds left world-readable) and should be visible.
    if sys.platform != "win32":
        try:
            os.chmod(config_dir, 0o700)
        except OSError as exc:
            print(f"Warning: could not set 0o700 on {config_dir}: {exc}", file=sys.stderr)
    body = (
        "# Cisco CIRCUIT API credentials — keep this file private\n"
        f"CIRCUIT_CLIENT_ID={client_id}\n"
        f"CIRCUIT_CLIENT_SECRET={client_secret}\n"
        f"CIRCUIT_APP_KEY={app_key}\n"
        f"CIRCUIT_MODEL={model}\n"
    )
    env_file.write_text(body)
    if sys.platform != "win32":
        try:
            os.chmod(env_file, 0o600)
        except OSError as exc:
            print(f"Warning: could not set 0o600 on {env_file}: {exc}", file=sys.stderr)
    return env_file


SPAWN_MARKER_TTL_SECS = 30


def _ensure_proxy_running() -> None:
    """Make sure circuit-proxy is reachable on the configured host:port.

    Probes /health first so re-running the agent doesn't double-spawn.
    If the proxy is down, fires off a detached Popen and returns immediately
    — the agent itself talks to Cisco directly, so we don't need to confirm
    the proxy is alive before the user can use the TUI. The proxy logs to
    ~/.circuit-agent/proxy.log if it fails to come up.

    Concurrency: uses Path.touch(exist_ok=False) as an atomic marker to
    prevent two simultaneous agent launches from both spawning a proxy
    (the second would bind-fail on the port and die noisily). Marker is
    auto-cleaned after Popen; stale markers older than 30s are reclaimed.

    Skipped entirely if CIRCUIT_AGENT_AUTO_PROXY=0.
    """
    if os.environ.get("CIRCUIT_AGENT_AUTO_PROXY", "1").lower() in ("0", "false", "no"):
        return

    import socket
    import subprocess
    import time
    import urllib.error
    import urllib.request

    host = os.environ.get("CIRCUIT_PROXY_HOST", "127.0.0.1")
    port = int(os.environ.get("CIRCUIT_PROXY_PORT", "8787"))
    health_url = f"http://{host}:{port}/health"

    def _is_alive() -> bool:
        try:
            with urllib.request.urlopen(health_url, timeout=1.0) as resp:
                return resp.status == 200
        except (urllib.error.URLError, socket.timeout, ConnectionError, OSError):
            return False

    if _is_alive():
        return

    log_dir = Path(os.environ.get("CIRCUIT_AGENT_HOME") or (Path.home() / ".circuit-agent"))
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "proxy.log"
    spawn_marker = log_dir / "proxy.spawning"

    # Atomic claim — only one agent at a time wins the right to spawn.
    try:
        spawn_marker.touch(exist_ok=False)
    except FileExistsError:
        try:
            age = time.time() - spawn_marker.stat().st_mtime
        except OSError:
            age = 0
        if age < SPAWN_MARKER_TTL_SECS:
            return  # another agent is mid-spawn; trust it to finish
        # Marker is stale — previous spawner crashed before cleanup. Reclaim.
        spawn_marker.unlink(missing_ok=True)
        try:
            spawn_marker.touch(exist_ok=False)
        except FileExistsError:
            return  # raced with yet another agent reclaiming; let them win

    print(f"Starting circuit-proxy in background (logs: {log_file})...", file=sys.stderr)

    log_fd = open(log_file, "ab")
    popen_kwargs = {
        "stdout": log_fd,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
    }
    if sys.platform == "win32":
        # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP — survives parent exit, no console window
        popen_kwargs["creationflags"] = 0x00000008 | 0x00000200
    else:
        popen_kwargs["start_new_session"] = True  # detach from agent's process group

    try:
        subprocess.Popen([sys.executable, "-m", "circuit_agent.proxy"], **popen_kwargs)
    except Exception as exc:
        log_fd.close()
        spawn_marker.unlink(missing_ok=True)
        print(f"Warning: failed to spawn circuit-proxy: {exc}", file=sys.stderr)
        print("Run 'circuit-proxy' manually in another terminal.", file=sys.stderr)
        return
    log_fd.close()  # parent's copy; child has its own
    spawn_marker.unlink(missing_ok=True)
    # Fire-and-forget: the agent talks to Cisco directly and doesn't need
    # the proxy alive to function. If the proxy fails to bind, the user
    # finds out from proxy.log or the next tool that tries to use it.


def main():
    """Entry point for the CLI."""
    args = parse_args()

    # Handle version flag
    if args.version:
        from . import __version__

        print(f"Circuit Agent v{__version__}")
        return

    if args.upgrade:
        sys.exit(_upgrade_in_place())

    _load_env_file_silently()

    # Determine working directory
    working_dir = args.directory or os.getcwd()
    if not os.path.isdir(working_dir):
        print(f"Error: '{working_dir}' is not a valid directory", file=sys.stderr)
        sys.exit(1)

    working_dir = os.path.abspath(working_dir)

    # Determine prompt (from -p or --prompt-file)
    prompt = args.prompt
    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).read_text().strip()
        except Exception as e:
            print(f"Error reading prompt file: {e}", file=sys.stderr)
            sys.exit(1)

    # Run in appropriate mode
    try:
        if prompt:
            # Headless mode
            exit_code = asyncio.run(
                run_headless(
                    prompt=prompt,
                    working_dir=working_dir,
                    auto_approve=args.auto_approve,
                    output_format=args.output,
                    model=args.model,
                )
            )
            sys.exit(exit_code)
        else:
            # Interactive mode
            asyncio.run(run_cli(working_dir))
    except KeyboardInterrupt:
        print(f"\n{C.CYAN}Goodbye!{C.RESET}\n")


if __name__ == "__main__":
    main()
