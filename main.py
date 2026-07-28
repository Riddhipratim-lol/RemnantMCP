"""
RemnantMCP — Main entry point.

Launches the FastMCP server which exposes the five RemnantMCP tools
to any compliant AI coding assistant (Cursor, Claude Desktop, Windsurf, etc.).

Transport is auto-selected by FastMCP:
  - stdio  → when launched as a subprocess by an MCP client (local use)
  - SSE    → when run as a standalone HTTP service (team/multi-user use)

Usage:
    python main.py
    # or
    python -m remnant.mcp_server
"""

from remnant.mcp_server import mcp


def main():
    mcp.run()


if __name__ == "__main__":
    main()
