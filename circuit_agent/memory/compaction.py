"""
Context compaction for Circuit Agent.

Summarizes older messages to reduce token usage while preserving context.
"""

import json
from typing import Any, Callable, Dict, List, Optional


class ContextCompactor:
    """
    Compacts conversation history by summarizing older messages.

    This helps manage long conversations that would exceed token limits.
    """

    def __init__(self, max_messages: int = 50, keep_recent: int = 10, summary_trigger: int = 40):
        """
        Initialize compactor.

        Args:
            max_messages: Maximum messages before forcing compaction
            keep_recent: Number of recent messages to keep intact
            summary_trigger: Trigger compaction when message count exceeds this
        """
        self.max_messages = max_messages
        self.keep_recent = keep_recent
        self.summary_trigger = summary_trigger

    def needs_compaction(self, history: List[Dict[str, Any]]) -> bool:
        """
        Check if history needs compaction.

        Args:
            history: Conversation history

        Returns:
            True if compaction is recommended
        """
        return len(history) >= self.summary_trigger

    def estimate_tokens(self, history: List[Dict[str, Any]]) -> int:
        """
        Estimate token count for history (rough approximation).

        Args:
            history: Conversation history

        Returns:
            Estimated token count
        """
        total_chars = 0
        for msg in history:
            content = msg.get("content", "")
            if isinstance(content, str):
                total_chars += len(content)
            elif isinstance(content, list):
                # Handle multi-part messages
                for part in content:
                    if isinstance(part, dict) and "text" in part:
                        total_chars += len(part["text"])

            # Count tool calls
            if "tool_calls" in msg:
                for tc in msg["tool_calls"]:
                    total_chars += len(json.dumps(tc.get("function", {})))

        # Rough estimate: ~4 chars per token
        return total_chars // 4

    def create_summary_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """
        Create a prompt asking the LLM to summarize messages.

        Args:
            messages: Messages to summarize

        Returns:
            Prompt string for summarization
        """
        # Format messages for summarization
        formatted = []
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")

            if isinstance(content, str) and content:
                formatted.append(f"[{role.upper()}]: {content[:500]}")
            elif "tool_calls" in msg:
                tools = [tc["function"]["name"] for tc in msg["tool_calls"]]
                formatted.append(f"[{role.upper()}]: Called tools: {', '.join(tools)}")

        messages_text = "\n".join(formatted)

        return f"""Summarize this conversation history concisely, preserving:
1. Key decisions and actions taken
2. Files that were created, edited, or discussed
3. Important context about the project/task
4. Any unresolved issues or pending tasks

Keep the summary under 500 words. Focus on what's important for continuing the conversation.

CONVERSATION:
{messages_text}

SUMMARY:"""

    def compact(
        self, history: List[Dict[str, Any]], summarize_fn: Optional[Callable[[str], str]] = None
    ) -> tuple[List[Dict[str, Any]], str]:
        """
        Compact conversation history.

        Args:
            history: Full conversation history
            summarize_fn: Optional function to call LLM for summarization.
                         If None, creates a simple textual summary.

        Returns:
            (compacted_history, summary_message) tuple
        """
        if len(history) <= self.keep_recent:
            return history, "History too short to compact"

        # Split into old and recent
        old_messages = history[: -self.keep_recent]
        recent_messages = history[-self.keep_recent :]

        # Generate summary
        if summarize_fn:
            prompt = self.create_summary_prompt(old_messages)
            summary = summarize_fn(prompt)
        else:
            summary = self._simple_summary(old_messages)

        # Create compacted history with summary as system message
        compacted = [
            {
                "role": "system",
                "content": f"[CONVERSATION SUMMARY - {len(old_messages)} messages compacted]\n\n{summary}",
            }
        ] + recent_messages

        stats = f"Compacted {len(old_messages)} messages into summary. Kept {len(recent_messages)} recent messages."

        return compacted, stats

    def _simple_summary(self, messages: List[Dict[str, Any]]) -> str:
        """
        Create a simple textual summary without LLM.

        Args:
            messages: Messages to summarize

        Returns:
            Summary string
        """
        files_mentioned = set()
        tools_used = set()
        key_actions = []

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

            # Look for key actions in assistant messages
            if role == "assistant" and isinstance(content, str):
                if any(
                    word in content.lower()
                    for word in ["created", "edited", "fixed", "added", "removed", "implemented"]
                ):
                    # Extract first sentence as key action
                    first_sentence = content.split(".")[0][:100]
                    if first_sentence:
                        key_actions.append(first_sentence)

        # Build summary
        summary_parts = []

        if files_mentioned:
            summary_parts.append(f"**Files worked on:** {', '.join(sorted(files_mentioned)[:10])}")

        if tools_used:
            summary_parts.append(f"**Tools used:** {', '.join(sorted(tools_used))}")

        if key_actions:
            summary_parts.append("**Key actions:**")
            for action in key_actions[:5]:
                summary_parts.append(f"- {action}")

        summary_parts.append(f"\n*{len(messages)} messages summarized*")

        return "\n".join(summary_parts)

    def get_compaction_stats(self, history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Get statistics about current history and potential compaction.

        Args:
            history: Conversation history

        Returns:
            Dict with stats
        """
        estimated_tokens = self.estimate_tokens(history)

        return {
            "message_count": len(history),
            "estimated_tokens": estimated_tokens,
            "needs_compaction": self.needs_compaction(history),
            "would_compact": max(0, len(history) - self.keep_recent),
            "would_keep": min(len(history), self.keep_recent),
        }
