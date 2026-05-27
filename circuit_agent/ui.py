"""
UI utilities for Circuit Agent — Claude-Code-styled terminal rendering.

Keeps backwards-compatible function names (print_header, print_tool_call,
show_diff, etc.) so cli.py and agent.py keep working unchanged.
"""

import difflib
import os
import re
from pathlib import Path
from typing import Optional

from rich.console import Console, Group
from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.syntax import Syntax
from rich.text import Text

# ---------------------------------------------------------------------------
# Theme — chosen to echo Claude Code: warm orange accent, dim gray chrome,
# bold cyan headings, clean rounded panels.
# ---------------------------------------------------------------------------

ACCENT = "#d97757"        # warm orange used for the ✻ sigil and tool dot
HEADING = "bold cyan"
DIM = "grey50"
USER_LABEL = "bold #87afff"
AGENT_LABEL = "bold #d97757"

console = Console(highlight=False, soft_wrap=False)


# ---------------------------------------------------------------------------
# Backwards-compat color shim — some callers still use ANSI escapes directly.
# ---------------------------------------------------------------------------


class Colors:
    """ANSI codes kept for code paths that still hand-render."""

    GREEN = "\033[92m"
    RED = "\033[91m"
    YELLOW = "\033[93m"
    CYAN = "\033[96m"
    MAGENTA = "\033[95m"
    BLUE = "\033[94m"
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"


C = Colors


# ---------------------------------------------------------------------------
# Terminal control
# ---------------------------------------------------------------------------


def clear_screen():
    os.system("cls" if os.name == "nt" else "clear")


def clear_line():
    print("\r" + " " * 80 + "\r", end="", flush=True)


# ---------------------------------------------------------------------------
# Panels — header + welcome banner
# ---------------------------------------------------------------------------


def _shorten_path(path: str, max_len: int = 60) -> str:
    home = str(Path.home())
    if path.startswith(home):
        path = "~" + path[len(home):]
    if len(path) <= max_len:
        return path
    return "…" + path[-(max_len - 1):]


def print_header(working_dir: str):
    """Top-of-session welcome panel — Claude-Code-style rounded box."""
    body = Text()
    body.append("✻ ", style=ACCENT)
    body.append("Welcome to ", style="bold")
    body.append("Circuit Code\n", style="bold cyan")
    body.append("\n")
    body.append("  /help ", style="bold")
    body.append("for help, ", style=DIM)
    body.append("/quit ", style="bold")
    body.append("to exit\n\n", style=DIM)
    body.append(f"  cwd: ", style=DIM)
    body.append(_shorten_path(working_dir), style="white")

    console.print()
    console.print(
        Panel(
            body,
            border_style=DIM,
            padding=(0, 1),
            expand=False,
        )
    )


def print_welcome():
    """Compact tips banner shown after auth + model pick."""
    body = Text()
    body.append("Tips for getting started:\n\n", style="bold")
    body.append(" 1. ", style=DIM)
    body.append("Ask the agent to explore the codebase first\n")
    body.append(" 2. ", style=DIM)
    body.append("Be specific — narrow tasks > broad ones\n")
    body.append(" 3. ", style=DIM)
    body.append("Type ", style="default")
    body.append("/auto", style="bold")
    body.append(" to skip confirmations (careful!)\n", style="default")
    body.append(" 4. ", style=DIM)
    body.append("Drop a ", style="default")
    body.append("CIRCUIT.md", style="bold")
    body.append(" in this folder for project rules\n", style="default")

    console.print()
    console.print(
        Panel(
            body,
            border_style=DIM,
            padding=(0, 1),
            expand=False,
        )
    )
    console.print()


def print_help():
    """Rich-styled /help output."""

    def section(title: str, rows: list[tuple[str, str]]):
        console.print()
        console.print(f"[bold]{title}[/bold]")
        for cmd, desc in rows:
            console.print(f"  [cyan]{cmd:<14}[/cyan] [grey70]{desc}[/grey70]")

    section(
        "Commands",
        [
            ("/help, /h", "Show this help"),
            ("/files", "List files in working directory"),
            ("/clear, /c", "Clear conversation history"),
            ("/history", "Show recent conversation"),
            ("/model", "Change model"),
            ("/tokens", "Show token usage for session"),
            ("/undo [file]", "Restore file from backup"),
            ("/config", "Show current configuration"),
            ("/git", "Quick git status"),
            ("/auto", "Toggle auto-approve mode"),
            ("/stream", "Toggle response streaming"),
            ("/logout", "Delete saved credentials"),
            ("/quit, /q", "Exit"),
        ],
    )
    section(
        "Sessions",
        [
            ("/save [name]", "Save current session"),
            ("/load [name]", "Load a saved session"),
            ("/sessions", "List all saved sessions"),
            ("/compact", "Compress old messages to save tokens"),
        ],
    )
    section(
        "Telemetry",
        [
            ("/cost", "Estimated API cost for session"),
            ("/audit", "Audit log statistics"),
            ("/think [on|off]", "Toggle thinking mode"),
        ],
    )
    section(
        "Confirmations",
        [
            ("y", "Yes, allow this action"),
            ("n", "No, cancel this action"),
            ("a", "Allow this and all future actions"),
        ],
    )
    console.print()


# ---------------------------------------------------------------------------
# Inline status / log lines
# ---------------------------------------------------------------------------


def print_token_usage(
    prompt_tokens: int, completion_tokens: int, session_prompt: int, session_completion: int
):
    total = prompt_tokens + completion_tokens
    session_total = session_prompt + session_completion
    console.print(
        f"[{DIM}]tokens: {prompt_tokens:,} in / {completion_tokens:,} out "
        f"({total:,}) · session {session_total:,}[/{DIM}]"
    )


def print_error(message: str):
    console.print(f"  [bold red]✗[/bold red] [red]{message}[/red]")


def print_success(message: str):
    console.print(f"  [bold green]✓[/bold green] [green]{message}[/green]")


def print_warning(message: str):
    console.print(f"  [bold yellow]![/bold yellow] [yellow]{message}[/yellow]")


def print_info(message: str):
    console.print(f"  [{DIM}]{message}[/{DIM}]")


# ---------------------------------------------------------------------------
# Tool-call rendering — Claude Code's ⏺ / ⎿ pattern
# ---------------------------------------------------------------------------


def _format_tool_detail(detail: str, term_width: int) -> Text:
    """Render tool args inline, but split onto continuation lines when long.

    Accepts either `key=value, key=value` or a free-form string. Output mirrors
    Claude Code's compact one-liner that wraps gracefully on narrow terminals.
    """
    out = Text()
    out.append("(", style=DIM)
    # Heuristic split: if it looks like kwargs, format each on its own line
    if "=" in detail and "," in detail and len(detail) > term_width - 20:
        parts = [p.strip() for p in re.split(r",(?=\s*\w+=)", detail) if p.strip()]
        for i, part in enumerate(parts):
            out.append("\n    ", style=DIM)
            if "=" in part:
                key, val = part.split("=", 1)
                out.append(key, style="cyan")
                out.append("=", style=DIM)
                out.append(val, style="white")
            else:
                out.append(part, style="white")
            if i < len(parts) - 1:
                out.append(",", style=DIM)
        out.append("\n  ", style=DIM)
    else:
        if len(detail) > term_width - 6:
            detail = detail[: term_width - 7] + "…"
        out.append(detail, style="white")
    out.append(")", style=DIM)
    return out


def print_tool_call(tool_name: str, detail: str = ""):
    """Render a tool invocation, e.g. `⏺ Read(path/to/file.py)`."""
    width = console.size.width or 100
    line = Text()
    line.append("⏺ ", style=ACCENT)
    line.append(tool_name, style="bold white")
    if detail:
        line.append_text(_format_tool_detail(detail, width))
    console.print(line)


def print_tool_result(text: str, max_len: int = 100):
    """Render a tool result follow-up, e.g. `  ⎿ Read 42 lines`."""
    first = text.strip().splitlines()[0] if text.strip() else ""
    if len(first) > max_len:
        first = first[: max_len - 1] + "…"
    line = Text()
    line.append("  ⎿ ", style=DIM)
    line.append(first, style=DIM)
    console.print(line)


def print_user_message(text: str):
    """Show the user's message echoed back, Claude-Code-style: `> their text`."""
    console.print()
    console.print(Text(f"> {text}", style="bold"))


def print_agent_label():
    """Print no label; Claude Code lets responses flow without a header."""
    # Kept as a no-op so existing call sites that wanted a label can stay quiet.
    console.print()


# ---------------------------------------------------------------------------
# Diff rendering
# ---------------------------------------------------------------------------


def show_diff(old_text: str, new_text: str, path: str, max_lines: int = 30) -> None:
    """Display a colored unified diff inside a bordered panel."""
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    if old_lines and not old_lines[-1].endswith("\n"):
        old_lines[-1] += "\n"
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"

    diff = list(
        difflib.unified_diff(
            old_lines, new_lines, fromfile=f"a/{path}", tofile=f"b/{path}", lineterm=""
        )
    )

    if not diff:
        console.print(f"[{DIM}](no visible changes)[/{DIM}]")
        return

    body = Text()
    for raw in diff[:max_lines]:
        line = raw.rstrip("\n")
        if line.startswith("+++") or line.startswith("---"):
            body.append(line + "\n", style="bold")
        elif line.startswith("+"):
            body.append(line + "\n", style="green")
        elif line.startswith("-"):
            body.append(line + "\n", style="red")
        elif line.startswith("@@"):
            body.append(line + "\n", style="cyan")
        else:
            body.append(line + "\n")

    if len(diff) > max_lines:
        body.append(f"… ({len(diff) - max_lines} more lines)\n", style=DIM)

    console.print(
        Panel(body, title=f"diff · {path}", border_style=DIM, padding=(0, 1), expand=False)
    )


# ---------------------------------------------------------------------------
# Confirmation prompt
# ---------------------------------------------------------------------------


def confirm(prompt: str, default: bool = False) -> bool:
    """Y/N confirmation; matches Claude Code's compact style."""
    suffix = "[Y/n]" if default else "[y/N]"
    console.print()
    response = input(f"  ? {prompt} {suffix}: ").strip().lower()
    if not response:
        return default
    return response in ("y", "yes")


# ---------------------------------------------------------------------------
# Spinner (used by code that wants frame-by-frame control; the chat loop now
# uses rich.status instead)
# ---------------------------------------------------------------------------


def spinner_frame(frame: int) -> str:
    frames = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    return frames[frame % len(frames)]


_THINKING_VERBS = (
    "Thinking",
    "Pondering",
    "Cooking",
    "Brewing",
    "Wrangling",
    "Untangling",
    "Crafting",
    "Mulling",
    "Sketching",
    "Reasoning",
    "Weaving",
    "Plotting",
)


def thinking_status(message: Optional[str] = None):
    """Context manager for an animated 'thinking' status line.

    Rotates through Claude-Code-style verbs every few seconds while the
    spinner runs, so long model calls feel alive.

    Usage:
        with thinking_status():
            response = await agent.chat(...)
    """
    import random
    import threading
    import time

    verb = message or random.choice(_THINKING_VERBS)
    status = console.status(f"[{DIM}]{verb}…[/{DIM}]", spinner="dots")

    if message is not None:
        return status  # static message — no rotation

    class _Rotating:
        def __init__(self, status):
            self._status = status
            self._stop = threading.Event()
            self._thread: Optional[threading.Thread] = None

        def _loop(self):
            while not self._stop.wait(2.8):
                new = random.choice(_THINKING_VERBS)
                try:
                    self._status.update(status=f"[{DIM}]{new}…[/{DIM}]")
                except Exception:
                    return

        def __enter__(self):
            self._status.__enter__()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            return self

        def __exit__(self, *exc):
            self._stop.set()
            return self._status.__exit__(*exc)

    return _Rotating(status)


# ---------------------------------------------------------------------------
# Streaming markdown renderer
# ---------------------------------------------------------------------------


class StreamingMarkdownRenderer:
    """Render markdown chunks as they stream in.

    Uses Rich for code-block syntax highlighting; inline formatting stays
    lightweight so partial chunks render cleanly.
    """

    def __init__(self):
        self._buffer = ""
        self._in_code_block = False
        self._code_lang = ""
        self._code_lines: list[str] = []

    def feed(self, chunk: str):
        self._buffer += chunk
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self._process_line(line)

    def flush(self):
        if self._buffer:
            if self._in_code_block:
                self._code_lines.append(self._buffer)
            else:
                self._emit_inline(self._buffer)
            self._buffer = ""

        if self._in_code_block:
            self._render_code_block()
            self._in_code_block = False

    def _process_line(self, line: str):
        stripped = line.strip()
        if stripped.startswith("```"):
            if self._in_code_block:
                self._render_code_block()
                self._in_code_block = False
            else:
                self._in_code_block = True
                self._code_lang = stripped[3:].strip()
                self._code_lines = []
            return

        if self._in_code_block:
            self._code_lines.append(line)
        else:
            self._emit_line(line)

    def _emit_line(self, line: str):
        stripped = line.strip()
        if stripped.startswith("### "):
            console.print(Text(stripped[4:], style=HEADING))
            return
        if stripped.startswith("## "):
            console.print(Text(stripped[3:], style=HEADING))
            return
        if stripped.startswith("# "):
            console.print(Text(stripped[2:], style=HEADING))
            return
        if stripped.startswith(("- ", "* ")):
            indent = len(line) - len(line.lstrip())
            content = stripped[2:]
            t = Text(" " * indent)
            t.append("• ", style="cyan")
            t.append(self._inline_text(content))
            console.print(t)
            return
        match = re.match(r"^(\s*)(\d+)\.\s+(.+)", line)
        if match:
            t = Text(match.group(1))
            t.append(f"{match.group(2)}. ", style="cyan")
            t.append(self._inline_text(match.group(3)))
            console.print(t)
            return
        if stripped.startswith("> "):
            t = Text("│ ", style=DIM)
            t.append(self._inline_text(stripped[2:]), style=DIM)
            console.print(t)
            return
        if stripped in ("---", "***", "___"):
            console.print(Text("─" * 50, style=DIM))
            return
        self._emit_inline(line + "\n")

    def _emit_inline(self, text: str):
        console.print(self._inline_text(text), end="")

    def _inline_text(self, text: str) -> Text:
        """Lightweight inline markdown → Rich Text."""
        result = Text()
        i = 0
        while i < len(text):
            # `code`
            if text[i] == "`":
                end = text.find("`", i + 1)
                if end != -1:
                    result.append(text[i + 1 : end], style="yellow")
                    i = end + 1
                    continue
            # **bold**
            if text.startswith("**", i):
                end = text.find("**", i + 2)
                if end != -1:
                    result.append(text[i + 2 : end], style="bold")
                    i = end + 2
                    continue
            # *italic*
            if text[i] == "*" and not text.startswith("**", i):
                end = text.find("*", i + 1)
                if end != -1 and not text.startswith("**", end):
                    result.append(text[i + 1 : end], style="italic")
                    i = end + 1
                    continue
            result.append(text[i])
            i += 1
        return result

    def _render_code_block(self):
        code = "\n".join(self._code_lines)
        lang = self._code_lang or "text"
        try:
            syntax = Syntax(
                code, lang, theme="monokai", line_numbers=False, background_color="default"
            )
            console.print(
                Panel(
                    syntax,
                    title=f"[{DIM}]{lang}[/{DIM}]",
                    title_align="left",
                    border_style=DIM,
                    padding=(0, 1),
                    expand=False,
                )
            )
        except Exception:
            console.print(Panel(code, border_style=DIM, padding=(0, 1), expand=False))


def render_markdown(text: str) -> str:
    """Render a complete markdown string. Returns empty since output goes
    directly to the console — kept for signature compatibility."""
    renderer = StreamingMarkdownRenderer()
    renderer.feed(text + "\n")
    renderer.flush()
    return ""
