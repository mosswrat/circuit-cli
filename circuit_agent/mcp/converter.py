"""
Tool format conversion between MCP and OpenAI formats.

MCP uses a slightly different schema format than OpenAI's function calling.
This module handles bidirectional conversion.
"""

from typing import Any, Dict, List


def mcp_to_openai(mcp_tool: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert an MCP tool definition to OpenAI function calling format.

    MCP format:
    {
        "name": "create_issue",
        "description": "Create a new issue",
        "inputSchema": {
            "type": "object",
            "properties": {...},
            "required": [...]
        }
    }

    OpenAI format:
    {
        "type": "function",
        "function": {
            "name": "create_issue",
            "description": "Create a new issue",
            "parameters": {
                "type": "object",
                "properties": {...},
                "required": [...]
            }
        }
    }
    """
    name = mcp_tool.get("name", "")
    description = mcp_tool.get("description", "")
    input_schema = mcp_tool.get("inputSchema", {})

    # Ensure schema has required fields
    if "type" not in input_schema:
        input_schema["type"] = "object"
    if "properties" not in input_schema:
        input_schema["properties"] = {}

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": input_schema,
        },
    }


def openai_to_mcp(openai_tool: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert an OpenAI function definition to MCP format.
    """
    func = openai_tool.get("function", openai_tool)

    return {
        "name": func.get("name", ""),
        "description": func.get("description", ""),
        "inputSchema": func.get("parameters", {"type": "object", "properties": {}}),
    }


def openai_to_mcp_args(tool_call: Dict[str, Any]) -> Dict[str, Any]:
    """
    Convert OpenAI tool call arguments to MCP format.

    OpenAI returns:
    {
        "id": "call_xxx",
        "type": "function",
        "function": {
            "name": "tool_name",
            "arguments": "{\"key\": \"value\"}"  # JSON string
        }
    }

    MCP expects:
    {
        "name": "tool_name",
        "arguments": {"key": "value"}  # Parsed dict
    }
    """
    import json

    func = tool_call.get("function", {})
    name = func.get("name", "")
    args_str = func.get("arguments", "{}")

    # Parse arguments if they're a string
    if isinstance(args_str, str):
        try:
            arguments = json.loads(args_str)
        except json.JSONDecodeError:
            arguments = {}
    else:
        arguments = args_str

    return {
        "name": name,
        "arguments": arguments,
    }


def mcp_tools_to_openai(mcp_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert a list of MCP tools to OpenAI format."""
    return [mcp_to_openai(tool) for tool in mcp_tools]


def filter_tools_by_toolset(
    tools: List[Dict[str, Any]], enabled_toolsets: List[str]
) -> List[Dict[str, Any]]:
    """
    Filter MCP tools by enabled toolsets.

    Tools are typically named like "toolset_action" (e.g., "repos_create", "issues_list").
    If enabled_toolsets is empty, all tools are returned.
    """
    if not enabled_toolsets:
        return tools

    filtered = []
    for tool in tools:
        name = tool.get("name", "")
        # Check if tool name starts with any enabled toolset
        for toolset in enabled_toolsets:
            if name.startswith(f"{toolset}_") or name == toolset:
                filtered.append(tool)
                break

    return filtered
