"""Quick self-check for MCP integration.

Run from the project root::

    .venv\\Scripts\\python.exe examples\\verify_mcp.py

It connects to the server(s) named in ``MCP_SERVERS`` (or the bundled demo
server when ``MCP_SERVERS`` is unset), lists the discovered tools, and performs
a remember / recall round-trip when the demo "memory" server is present.
"""

from __future__ import annotations

import json
import os
import sys

from dotenv import load_dotenv

from repair_agent.mcp import MCPToolRegistry, parse_mcp_servers


def main() -> int:
    load_dotenv()
    raw = os.getenv("MCP_SERVERS", "")
    if not raw.strip():
        raw = json.dumps(
            [
                {
                    "name": "memory",
                    "command": sys.executable,
                    "args": ["-m", "examples.demo_mcp_server"],
                }
            ]
        )
    specs = parse_mcp_servers(raw)
    print(f"Connecting to {len(specs)} MCP server(s)...")
    registry = MCPToolRegistry(specs)
    try:
        registry.connect(timeout=30)
        print("Discovered tools:")
        for name in sorted(registry.tool_names()):
            print(f"  - {name}")
        if "mcp__memory__remember" in registry.tool_names():
            print("remember:", registry.call("mcp__memory__remember", {"key": "k", "value": "v"}))
            print("recall:", registry.call("mcp__memory__recall", {"key": "k"}))
        print("MCP OK")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"MCP check failed: {type(exc).__name__}: {exc}")
        return 1
    finally:
        registry.close()


if __name__ == "__main__":
    raise SystemExit(main())
