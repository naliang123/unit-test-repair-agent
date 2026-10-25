"""A tiny stdio MCP server used to demo MCP tool integration end to end.

Point the agent at it with (use a Python that has ``mcp`` installed, e.g. the
project venv's ``.venv\\Scripts\\python.exe``)::

    MCP_SERVERS='[{"name":"memory","command":".venv\\Scripts\\python.exe","args":["-m","examples.demo_mcp_server"]}]'

The agent will then see two extra tools: ``mcp__memory__remember`` and
``mcp__memory__recall``.
"""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("memory")

_memory: dict[str, str] = {}


@mcp.tool()
def remember(key: str, value: str) -> str:
    """Store a string value under a key in the demo memory."""
    _memory[key] = value
    return f"stored {key!r}"


@mcp.tool()
def recall(key: str) -> str:
    """Retrieve a string value previously stored under a key."""
    return _memory.get(key, "(not found)")


if __name__ == "__main__":
    mcp.run(transport="stdio")
