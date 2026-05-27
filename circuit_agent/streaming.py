"""
Streaming response handling for Circuit Agent.
Parses Server-Sent Events (SSE) from OpenAI-compatible APIs.
"""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx


@dataclass
class StreamingToolCall:
    """Represents a tool call being streamed."""

    id: str = ""
    name: str = ""
    arguments: str = ""


@dataclass
class StreamingResponse:
    """Accumulates streaming response data."""

    content: str = ""
    tool_calls: List[StreamingToolCall] = field(default_factory=list)
    finish_reason: Optional[str] = None
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0 and any(tc.id for tc in self.tool_calls)

    def get_tool_calls_dict(self) -> List[Dict[str, Any]]:
        """Convert tool calls to dict format expected by the API."""
        return [
            {
                "id": tc.id,
                "type": "function",
                "function": {"name": tc.name, "arguments": tc.arguments},
            }
            for tc in self.tool_calls
            if tc.id  # Only include complete tool calls
        ]


async def stream_chat_completion(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    on_content: Optional[callable] = None,
    on_tool_call_start: Optional[callable] = None,
) -> StreamingResponse:
    """
    Stream a chat completion response.

    Args:
        client: httpx AsyncClient
        url: API endpoint URL
        headers: Request headers
        payload: Request payload (will add stream=True)
        on_content: Callback for content chunks (content: str)
        on_tool_call_start: Callback when a tool call starts (name: str)

    Returns:
        StreamingResponse with accumulated data
    """
    # Ensure streaming is enabled
    payload = {**payload, "stream": True}

    response = StreamingResponse()

    async with client.stream("POST", url, headers=headers, json=payload) as r:
        if r.status_code != 200:
            error_text = await r.aread()
            raise Exception(f"API call failed: {r.status_code} - {error_text.decode()[:500]}")

        async for line in r.aiter_lines():
            if not line:
                continue

            if line.startswith("data: "):
                data_str = line[6:]  # Remove "data: " prefix

                if data_str == "[DONE]":
                    break

                try:
                    data = json.loads(data_str)
                except json.JSONDecodeError:
                    continue

                # Extract choice data
                choices = data.get("choices", [])
                if not choices:
                    continue

                choice = choices[0]
                delta = choice.get("delta", {})
                finish = choice.get("finish_reason")

                if finish:
                    response.finish_reason = finish

                # Handle content
                content = delta.get("content")
                if content:
                    response.content += content
                    if on_content:
                        on_content(content)

                # Handle tool calls
                tool_calls = delta.get("tool_calls", [])
                for tc in tool_calls:
                    index = tc.get("index", 0)

                    # Ensure we have enough tool call slots
                    while len(response.tool_calls) <= index:
                        response.tool_calls.append(StreamingToolCall())

                    current_tc = response.tool_calls[index]

                    # Update tool call data
                    if "id" in tc:
                        current_tc.id = tc["id"]

                    if "function" in tc:
                        func = tc["function"]
                        if "name" in func:
                            current_tc.name = func["name"]
                            if on_tool_call_start:
                                on_tool_call_start(func["name"])
                        if "arguments" in func:
                            current_tc.arguments += func["arguments"]

                # Extract usage if present (usually in final message)
                usage = data.get("usage", {})
                if usage:
                    response.prompt_tokens = usage.get("prompt_tokens", 0)
                    response.completion_tokens = usage.get("completion_tokens", 0)

    return response


async def non_streaming_chat_completion(
    client: httpx.AsyncClient,
    url: str,
    headers: Dict[str, str],
    payload: Dict[str, Any],
) -> StreamingResponse:
    """
    Make a non-streaming chat completion request.
    Returns data in the same format as streaming for consistency.
    """
    r = await client.post(url, headers=headers, json=payload)

    if r.status_code != 200:
        raise Exception(f"API call failed: {r.status_code} - {r.text[:500]}")

    data = r.json()
    response = StreamingResponse()

    # Extract choice
    choices = data.get("choices", [])
    if choices:
        choice = choices[0]
        message = choice.get("message", {})

        response.content = message.get("content", "")
        response.finish_reason = choice.get("finish_reason")

        # Handle tool calls
        tool_calls = message.get("tool_calls", [])
        for tc in tool_calls:
            response.tool_calls.append(
                StreamingToolCall(
                    id=tc.get("id", ""),
                    name=tc.get("function", {}).get("name", ""),
                    arguments=tc.get("function", {}).get("arguments", ""),
                )
            )

    # Extract usage
    usage = data.get("usage", {})
    response.prompt_tokens = usage.get("prompt_tokens", 0)
    response.completion_tokens = usage.get("completion_tokens", 0)

    return response
