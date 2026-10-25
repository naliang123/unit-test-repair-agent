"""Model Context Protocol (MCP) tool integration.

MCP servers are configured through the ``MCP_SERVERS`` environment variable as a
JSON array of server specs. Each server's tools are exposed to the model under a
``mcp__<server>__<tool>`` name so they never collide with built-in tools.

Example (stdio)::

    MCP_SERVERS='[{"name":"memory","command":"python","args":["-m","examples.demo_mcp_server"]}]'

Example (sse)::

    MCP_SERVERS='[{"name":"remote","transport":"sse","url":"http://127.0.0.1:8765/sse"}]'

Sessions live on a dedicated background event loop so they stay connected across
many tool calls without re-establishing a connection each time.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class MCPServerSpec:
    name: str
    transport: str = "stdio"  # "stdio" or "sse"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPServerSpec":
        name = str(data.get("name", "")).strip()
        if not name:
            raise ValueError("Each MCP server spec needs a non-empty 'name'.")
        transport = str(data.get("transport", "stdio")).lower()
        spec = cls(name=name, transport=transport)
        spec.command = data.get("command")
        spec.args = [str(arg) for arg in (data.get("args") or [])]
        spec.env = {str(k): str(v) for k, v in (data.get("env") or {}).items()}
        spec.url = data.get("url")
        if transport not in {"stdio", "sse"}:
            raise ValueError(f"Unsupported MCP transport: {transport}")
        if transport == "stdio" and not spec.command:
            raise ValueError("stdio MCP servers need a 'command'.")
        if transport == "sse" and not spec.url:
            raise ValueError("sse MCP servers need a 'url'.")
        return spec


def parse_mcp_servers(raw: str | None) -> list[MCPServerSpec]:
    """Parse the ``MCP_SERVERS`` JSON value into server specs."""
    if not raw or not raw.strip():
        return []
    data = json.loads(raw)
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        raise ValueError("MCP_SERVERS must be a JSON object or array.")
    return [MCPServerSpec.from_dict(item) for item in data]


class MCPToolRegistry:
    """Connects to configured MCP servers and dispatches tool calls."""

    def __init__(self, specs: list[MCPServerSpec]) -> None:
        self._specs = specs
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._contexts: list[tuple[Any, Any]] = []
        self._tools: dict[str, tuple[Any, str]] = {}
        self._schemas: list[dict[str, Any]] = []

    # -- lifecycle ---------------------------------------------------------
    def connect(self, timeout: float = 60.0) -> None:
        if not self._specs:
            return
        self._run(self._connect_all(), timeout=timeout)

    def close(self) -> None:
        if not self._contexts:
            return
        try:
            self._run(self._close_all(), timeout=15.0)
        except Exception:
            pass
        self._contexts.clear()
        self._tools.clear()
        self._schemas.clear()
        if self._loop is not None:
            self._loop.call_soon_threadsafe(self._loop.stop)
            self._loop = None
            self._thread = None

    @property
    def has_tools(self) -> bool:
        return bool(self._tools)

    # -- tool discovery ----------------------------------------------------
    def tool_schemas(self) -> list[dict[str, Any]]:
        return list(self._schemas)

    def tool_names(self) -> set[str]:
        return set(self._tools)

    # -- execution ---------------------------------------------------------
    def call(self, name: str, arguments: dict[str, Any], timeout: float = 120.0) -> dict[str, Any]:
        entry = self._tools.get(name)
        if entry is None:
            return {"ok": False, "error": f"Unknown MCP tool: {name}"}
        session, original_name = entry
        try:
            result = self._run(self._call(session, original_name, arguments or {}), timeout=timeout)
            return result
        except Exception as exc:  # noqa: BLE001 - surface any transport error to the model
            return {"ok": False, "error": f"MCP call failed: {exc}"}

    # -- event-loop plumbing ----------------------------------------------
    def _ensure_loop(self) -> None:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(target=self._loop.run_forever, daemon=True)
            self._thread.start()

    def _run(self, coro: Any, timeout: float) -> Any:
        self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=timeout)

    async def _connect_all(self) -> None:
        for spec in self._specs:
            await self._connect_one(spec)

    async def _connect_one(self, spec: MCPServerSpec) -> None:
        from mcp import ClientSession

        if spec.transport == "sse":
            from mcp.client.sse import sse_client

            transport_cm = sse_client(spec.url)
        else:
            from mcp import StdioServerParameters
            from mcp.client.stdio import stdio_client

            env = {**os.environ, **spec.env} if spec.env else None
            params = StdioServerParameters(command=spec.command or "", args=spec.args, env=env)
            transport_cm = stdio_client(params)

        read, write = await transport_cm.__aenter__()
        session_cm = ClientSession(read, write)
        session = await session_cm.__aenter__()
        await session.initialize()
        listing = await session.list_tools()
        self._contexts.append((transport_cm, session_cm))
        for tool in listing.tools:
            prefixed = f"mcp__{spec.name}__{tool.name}"
            self._tools[prefixed] = (session, tool.name)
            self._schemas.append(self._to_schema(prefixed, tool))

    @staticmethod
    def _to_schema(name: str, tool: Any) -> dict[str, Any]:
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None) or {}
        properties = schema.get("properties", {}) if isinstance(schema, dict) else {}
        required = schema.get("required", []) if isinstance(schema, dict) else []
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": getattr(tool, "description", None) or name,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                    "additionalProperties": False,
                },
            },
        }

    async def _call(self, session: Any, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await session.call_tool(tool_name, arguments)
        texts: list[str] = []
        for item in getattr(result, "content", []) or []:
            if getattr(item, "type", None) == "text":
                texts.append(getattr(item, "text", ""))
            elif hasattr(item, "model_dump"):
                texts.append(json.dumps(item.model_dump(), ensure_ascii=False, default=str))
            else:
                texts.append(str(item))
        return {
            "ok": not getattr(result, "isError", False),
            "content": "\n".join(texts).strip(),
            "error": "MCP tool returned an error" if getattr(result, "isError", False) else None,
        }

    async def _close_all(self) -> None:
        for transport_cm, session_cm in reversed(self._contexts):
            try:
                await session_cm.__aexit__(None, None, None)
            except Exception:
                pass
            try:
                await transport_cm.__aexit__(None, None, None)
            except Exception:
                pass
