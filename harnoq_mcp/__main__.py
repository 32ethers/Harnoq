"""Run the Harnoq MCP server as a standalone process.

Serves the streamable-http transport, listening on its own port so clients
connect over a URL (e.g. http://host:5001/mcp):

    uv run --group mcp python -m harnoq_mcp

Configure the listen address with HARNOQ_MCP_HOST (default "0.0.0.0") and
HARNOQ_MCP_PORT (default "5001"). Point it at a running Harnoq via
HARNOQ_BASE_URL (default http://127.0.0.1:5000).
"""
import os
import uvicorn

from .server import mcp, HARNOQ_BASE_URL
from .auth import AuthenticatedMCP

if __name__ == "__main__":
    uvicorn.run(AuthenticatedMCP(mcp.streamable_http_app(), base_url=HARNOQ_BASE_URL),
                host=os.environ.get("HARNOQ_MCP_HOST", "0.0.0.0"),
                port=int(os.environ.get("HARNOQ_MCP_PORT", "5001")))
