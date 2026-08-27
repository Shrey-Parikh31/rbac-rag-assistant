"""The same three tools, exposed over MCP.

This file is the argument for MCP in one screen: it adds no implementation. It
registers the functions from tools.py so any MCP client (Claude Desktop, Claude
Code, an IDE) gets the same access rules, the same validation, and the same
ticket log as agent.py, rather than each integration reimplementing them and
drifting.

An MCP client has no notion of the caller's university role, so it is read once
from KB_ROLE at startup and applies to the whole connection. That is the honest
design at this scale: the role belongs to the server process, not to the
conversation, which is exactly what keeps the model from choosing it.

    KB_ROLE=staff python mcp_server.py
"""
import os

from mcp.server.mcpserver import MCPServer  # FastMCP in mcp 1.x

import tools

ROLE = os.environ.get("KB_ROLE", "student")
KNOWN_ROLES = sorted(tools.TOOL_ACCESS["search_docs"])
if ROLE not in KNOWN_ROLES:
    # Fail at startup, not on every call. A typo already fails closed, but it
    # fails as an error on each tool invocation, which looks like a broken
    # server rather than a wrong setting.
    raise SystemExit(f"KB_ROLE={ROLE!r} is not a known role; use one of {KNOWN_ROLES}")
tools.set_role(ROLE)

mcp = MCPServer("kb-assistant")
for fn in tools.TOOLS:
    mcp.tool()(fn)


if __name__ == "__main__":
    mcp.run()
