"""
Smart Context Management for Circuit Agent v4.0.

Intelligently manages what stays in context to maximize
the utility of the available token window.
"""

import json
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class ContextItem:
    """Represents an item in the context with priority scoring."""

    content: str
    item_type: str  # "message", "file", "tool_result", "summary"
    priority: int = 5  # 1-10, higher = more important
    tokens_estimate: int = 0
    timestamp: float = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        if self.tokens_estimate == 0:
            # Rough estimate: ~4 chars per token
            self.tokens_estimate = len(self.content) // 4


class SmartContextManager:
    """
    Intelligently manages conversation context to maximize utility.

    Features:
    - Priority-based retention (recent and relevant content stays)
    - File deduplication (only keep latest version of each file)
    - Error compression (summarize repetitive errors)
    - Tool result optimization (truncate large results)
    - Selective file loading (relevant sections only)
    """

    def __init__(self, max_tokens: int = 100000):
        self.max_tokens = max_tokens
        self.reserve_tokens = 10000  # Reserve for response
        self.available_tokens = max_tokens - self.reserve_tokens

        # Track active context
        self.file_cache: Dict[str, str] = {}  # path -> latest content
        self.file_versions: Dict[str, int] = {}  # path -> version count
        self.active_files: Set[str] = set()  # Files being worked on
        self.error_counts: Dict[str, int] = {}  # error_type -> count
        self.tool_results: OrderedDict[str, str] = OrderedDict()

        # Priority settings
        self.priority_weights = {
            "system": 10,
            "recent_user": 9,
            "recent_assistant": 8,
            "active_file": 8,
            "tool_result": 6,
            "old_message": 4,
            "compressed_error": 3,
            "old_file": 2,
        }

    def estimate_tokens(self, content: str) -> int:
        """Estimate token count for content."""
        if not content:
            return 0
        # Rough estimate: ~4 chars per token for code/text
        return len(content) // 4

    def estimate_message_tokens(self, message: Dict[str, Any]) -> int:
        """Estimate tokens for a single message."""
        content = message.get("content", "")
        if isinstance(content, str):
            tokens = self.estimate_tokens(content)
        elif isinstance(content, list):
            tokens = sum(
                self.estimate_tokens(part.get("text", ""))
                for part in content
                if isinstance(part, dict)
            )
        else:
            tokens = 0

        # Add overhead for role, tool_calls, etc.
        if "tool_calls" in message:
            tokens += self.estimate_tokens(json.dumps(message["tool_calls"]))

        return tokens + 10  # Base overhead per message

    def track_file_read(self, path: str, content: str):
        """Track when a file is read."""
        self.file_cache[path] = content
        self.file_versions[path] = self.file_versions.get(path, 0) + 1
        self.active_files.add(path)

    def track_file_write(self, path: str, content: str):
        """Track when a file is written."""
        self.file_cache[path] = content
        self.file_versions[path] = self.file_versions.get(path, 0) + 1
        self.active_files.add(path)

    def track_error(self, error_type: str, error_msg: str):
        """Track errors for compression."""
        self.error_counts[error_type] = self.error_counts.get(error_type, 0) + 1

    def mark_file_inactive(self, path: str):
        """Mark a file as no longer actively being worked on."""
        self.active_files.discard(path)

    def get_active_files(self) -> Set[str]:
        """Get set of files currently being worked on."""
        return self.active_files.copy()

    def compress_tool_result(self, tool_name: str, result: str, max_length: int = 8000) -> str:
        """Compress a tool result to save context space."""
        if len(result) <= max_length:
            return result

        if tool_name == "read_file":
            # Keep first and last portions for file content
            half = max_length // 2
            return (
                result[:half]
                + f"\n\n... [{len(result) - max_length} chars truncated] ...\n\n"
                + result[-half:]
            )

        elif tool_name in ("search_files", "list_files"):
            # Keep first N results
            lines = result.split("\n")
            kept_lines = []
            current_length = 0

            for line in lines:
                if current_length + len(line) > max_length:
                    break
                kept_lines.append(line)
                current_length += len(line) + 1

            remaining = len(lines) - len(kept_lines)
            if remaining > 0:
                kept_lines.append(f"\n... [{remaining} more results truncated]")

            return "\n".join(kept_lines)

        elif tool_name == "run_command":
            # Prioritize error output
            if "[stderr]" in result:
                # Keep stderr, truncate stdout
                parts = result.split("[stderr]")
                stdout = parts[0][: max_length // 3]
                stderr = parts[1] if len(parts) > 1 else ""
                if len(stderr) > max_length * 2 // 3:
                    stderr = stderr[: max_length * 2 // 3] + "\n[truncated]"
                return stdout + "[stderr]" + stderr
            else:
                return result[:max_length] + "\n[truncated]"

        elif tool_name in ("web_fetch", "web_search"):
            # Keep beginning for web content
            return result[:max_length] + "\n\n[content truncated]"

        else:
            # Generic truncation
            return result[:max_length] + "\n[truncated]"

    def compress_errors(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Compress repetitive error messages."""
        error_pattern = re.compile(r"Error:|error:|Exception:|exception:", re.IGNORECASE)
        error_groups: Dict[str, List[int]] = {}  # error_signature -> message indices

        # Find error messages and group similar ones
        for i, msg in enumerate(messages):
            content = msg.get("content", "")
            if isinstance(content, str) and error_pattern.search(content):
                # Create signature from first line of error
                first_line = content.split("\n")[0][:100]
                signature = re.sub(r"\d+", "N", first_line)  # Normalize numbers

                if signature not in error_groups:
                    error_groups[signature] = []
                error_groups[signature].append(i)

        # Compress groups with 3+ similar errors
        indices_to_remove = set()
        summaries_to_add = []

        for _signature, indices in error_groups.items():
            if len(indices) >= 3:
                # Keep first and last, summarize middle
                first_idx = indices[0]
                indices[-1]
                middle_count = len(indices) - 2

                # Mark middle messages for removal
                for idx in indices[1:-1]:
                    indices_to_remove.add(idx)

                # Add summary after first error
                summary = f"[{middle_count} similar errors omitted]"
                summaries_to_add.append((first_idx + 1, summary))

        # Build new message list
        if not indices_to_remove:
            return messages

        new_messages = []
        summary_idx = 0

        for i, msg in enumerate(messages):
            if i in indices_to_remove:
                continue

            new_messages.append(msg)

            # Add summaries
            while summary_idx < len(summaries_to_add) and summaries_to_add[summary_idx][0] == i + 1:
                new_messages.append({"role": "system", "content": summaries_to_add[summary_idx][1]})
                summary_idx += 1

        return new_messages

    def deduplicate_file_reads(self, messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Remove redundant file read results, keeping only the latest."""
        # Find all file read tool results
        file_reads: Dict[str, List[int]] = {}  # path -> message indices

        for i, msg in enumerate(messages):
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                # Check if this looks like a file read result
                if isinstance(content, str):
                    # Look for line number pattern that indicates file content
                    if re.match(r"\s*\d+\|", content) or content.startswith("[Lines"):
                        # Try to extract file path from previous assistant message
                        for j in range(i - 1, max(0, i - 5), -1):
                            prev_msg = messages[j]
                            if prev_msg.get("role") == "assistant":
                                tool_calls = prev_msg.get("tool_calls", [])
                                for tc in tool_calls:
                                    if tc.get("function", {}).get("name") == "read_file":
                                        try:
                                            args = json.loads(tc["function"].get("arguments", "{}"))
                                            path = args.get("path", "")
                                            if path:
                                                if path not in file_reads:
                                                    file_reads[path] = []
                                                file_reads[path].append(i)
                                        except Exception:
                                            pass
                                break

        # Mark old reads for summarization
        indices_to_summarize = set()
        for _path, indices in file_reads.items():
            if len(indices) > 1:
                # Keep only the last read, summarize earlier ones
                for idx in indices[:-1]:
                    indices_to_summarize.add(idx)

        if not indices_to_summarize:
            return messages

        # Replace old reads with summaries
        new_messages = []
        for i, msg in enumerate(messages):
            if i in indices_to_summarize:
                new_messages.append(
                    {
                        "role": msg.get("role", "tool"),
                        "tool_call_id": msg.get("tool_call_id"),
                        "content": "[File content superseded by later read]",
                    }
                )
            else:
                new_messages.append(msg)

        return new_messages

    def optimize_context(
        self, messages: List[Dict[str, Any]], target_tokens: Optional[int] = None
    ) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        """
        Optimize messages to fit within token budget.

        Returns:
            (optimized_messages, stats_dict)
        """
        if target_tokens is None:
            target_tokens = self.available_tokens

        original_count = len(messages)
        original_tokens = sum(self.estimate_message_tokens(m) for m in messages)

        # Step 1: Compress errors
        messages = self.compress_errors(messages)

        # Step 2: Deduplicate file reads
        messages = self.deduplicate_file_reads(messages)

        # Step 3: Compress large tool results
        for i, msg in enumerate(messages):
            if msg.get("role") == "tool":
                content = msg.get("content", "")
                if isinstance(content, str) and len(content) > 8000:
                    # Try to determine tool type from context
                    tool_name = "unknown"
                    for j in range(i - 1, max(0, i - 3), -1):
                        prev = messages[j]
                        if prev.get("tool_calls"):
                            for tc in prev["tool_calls"]:
                                if tc.get("id") == msg.get("tool_call_id"):
                                    tool_name = tc.get("function", {}).get("name", "unknown")
                                    break
                            break

                    messages[i] = {**msg, "content": self.compress_tool_result(tool_name, content)}

        # Step 4: If still over budget, truncate old messages
        current_tokens = sum(self.estimate_message_tokens(m) for m in messages)

        if current_tokens > target_tokens:
            # Keep system message and recent messages
            keep_recent = 20
            if len(messages) > keep_recent + 1:
                system_msg = messages[0] if messages[0].get("role") == "system" else None
                recent = messages[-keep_recent:]

                # Summarize middle section
                middle = messages[1:-keep_recent] if system_msg else messages[:-keep_recent]
                middle_summary = self._summarize_messages(middle)

                messages = []
                if system_msg:
                    messages.append(system_msg)
                messages.append(
                    {
                        "role": "system",
                        "content": f"[Earlier conversation summary]\n{middle_summary}",
                    }
                )
                messages.extend(recent)

        final_tokens = sum(self.estimate_message_tokens(m) for m in messages)

        stats = {
            "original_messages": original_count,
            "final_messages": len(messages),
            "original_tokens": original_tokens,
            "final_tokens": final_tokens,
            "tokens_saved": original_tokens - final_tokens,
            "compression_ratio": round(final_tokens / original_tokens, 2)
            if original_tokens > 0
            else 1.0,
        }

        return messages, stats

    def _summarize_messages(self, messages: List[Dict[str, Any]]) -> str:
        """Create a brief summary of messages."""
        files_mentioned = set()
        tools_used = set()
        key_points = []

        for msg in messages:
            role = msg.get("role", "")
            content = msg.get("content", "")

            # Track tool usage
            if "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    func = tc.get("function", {})
                    tool_name = func.get("name", "")
                    tools_used.add(tool_name)

                    # Extract file paths
                    try:
                        args = json.loads(func.get("arguments", "{}"))
                        if "path" in args:
                            files_mentioned.add(args["path"])
                    except Exception:
                        pass

            # Extract key actions
            if role == "assistant" and isinstance(content, str):
                # Look for action verbs
                action_words = [
                    "created",
                    "edited",
                    "fixed",
                    "added",
                    "removed",
                    "updated",
                    "implemented",
                    "refactored",
                ]
                for word in action_words:
                    if word in content.lower():
                        # Get first sentence containing this word
                        for sentence in content.split("."):
                            if word in sentence.lower():
                                key_points.append(sentence.strip()[:100])
                                break
                        break

        # Build summary
        summary_parts = []

        if files_mentioned:
            summary_parts.append(f"Files: {', '.join(sorted(files_mentioned)[:10])}")

        if tools_used:
            summary_parts.append(f"Tools used: {', '.join(sorted(tools_used))}")

        if key_points:
            summary_parts.append("Actions: " + "; ".join(key_points[:5]))

        summary_parts.append(f"({len(messages)} messages summarized)")

        return "\n".join(summary_parts)

    def get_context_stats(self, messages: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Get statistics about current context."""
        total_tokens = sum(self.estimate_message_tokens(m) for m in messages)

        role_counts = {}
        for msg in messages:
            role = msg.get("role", "unknown")
            role_counts[role] = role_counts.get(role, 0) + 1

        return {
            "message_count": len(messages),
            "estimated_tokens": total_tokens,
            "available_tokens": self.available_tokens,
            "utilization": round(total_tokens / self.available_tokens * 100, 1),
            "messages_by_role": role_counts,
            "active_files": len(self.active_files),
            "cached_files": len(self.file_cache),
        }
