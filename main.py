"""
RemnantMCP — Main entry point.

Launches the FastMCP server which exposes the five RemnantMCP tools
to any compliant AI coding assistant (Cursor, Claude Desktop, Windsurf, VS Code, Antigravity IDE, etc.).

Transport is auto-selected:
  - stdio → when launched locally as a subprocess by an MCP client
  - http  → Streamable HTTP (modern MCP standard) when run as a cloud Web Service (Render, Railway)

Usage:
    python main.py
    # or
    python -m remnant.mcp_server
"""

import os
from remnant.mcp_server import mcp


def main():
    port_env = os.getenv("PORT")
    transport_env = os.getenv("REMNANT_TRANSPORT")

    if port_env or transport_env:
        port = int(port_env or 8000)
        transport = transport_env or "http"
        mcp.run(transport=transport, host="0.0.0.0", port=port)
    else:
        mcp.run()


if __name__ == "__main__":
    main()
