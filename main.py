"""Development entry point.

Runs Harnoq's Flask web/API app and its FastMCP streamable-http server in a
single process on a single port (5000):

    uv run python main.py

- ``/mcp``  -> the FastMCP streamable-http endpoint (see harnoq_mcp/server.py)
- everything else -> the Flask app (bridged WSGI -> ASGI)

A plain dispatcher ASGI app (below) routes by path instead of Starlette
``Mount``, because the FastMCP Starlette sub-app returned by
``streamable_http_app()`` already owns the exact route ``/mcp`` (and its own
lifespan that starts/stops the MCP session manager); nesting it under another
``Mount("/mcp", ...)`` would double the prefix into ``/mcp/mcp``. Dispatching
by scope path keeps each sub-app's own routing/lifespan intact and lets
``/mcp`` and the rest of the site share one uvicorn process and port.

To run the MCP server standalone instead (its own process/port), use
``uv run --group mcp python -m harnoq_mcp``.
"""
from a2wsgi import WSGIMiddleware

from app import create_app
from harnoq_mcp import server as mcp_server
from harnoq_mcp.server import mcp
from harnoq_mcp.auth import AuthenticatedMCP

flask_app = create_app()
# Co-hosted: MCP tools dispatch into Flask in-process, not over TCP to
# themselves (a self-connection on the shared event loop would deadlock).
mcp_server.use_in_process_wsgi(flask_app)
mcp_asgi_app = AuthenticatedMCP(mcp.streamable_http_app(), flask_app=flask_app)
wsgi_asgi_app = WSGIMiddleware(flask_app)


class HarnoqASGIApp:
    """Dispatches by path: ``/mcp`` (and sub-paths) to the FastMCP ASGI app,
    everything else to the WSGI-bridged Flask app. Lifespan scopes go to the
    FastMCP app so its streamable-http session manager starts/stops with the
    server; the WSGI bridge has no lifespan of its own to run.
    """

    def __init__(self, mcp_app, wsgi_app):
        self._mcp_app = mcp_app
        self._wsgi_app = wsgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "lifespan":
            await self._mcp_app(scope, receive, send)
            return
        path = scope.get("path", "")
        if path == "/mcp" or path.startswith("/mcp/"):
            # Harnoq's MCP server never sends server-initiated messages, so it
            # does not offer the optional GET SSE stream. The Streamable HTTP
            # spec says to decline that GET with 405 Method Not Allowed (not
            # 404) so clients like Codex don't read /mcp as a missing endpoint.
            # RPC still flows over POST, which reaches the FastMCP app below.
            if scope["type"] == "http" and scope.get("method") == "GET":
                await self._respond_405(send)
                return
            await self._mcp_app(scope, receive, send)
        else:
            await self._wsgi_app(scope, receive, send)

    @staticmethod
    async def _respond_405(send):
        body = (
            b'{"error":"Method Not Allowed: this MCP endpoint has no GET SSE '
            b'stream; use POST for JSON-RPC."}'
        )
        await send({
            "type": "http.response.start",
            "status": 405,
            "headers": [
                (b"content-type", b"application/json"),
                (b"allow", b"POST"),
                (b"content-length", str(len(body)).encode()),
            ],
        })
        await send({"type": "http.response.body", "body": body})


app = HarnoqASGIApp(mcp_asgi_app, wsgi_asgi_app)


if __name__ == "__main__":
    import uvicorn

    # 0.0.0.0 so other machines on the LAN (e.g. an OpenWebUI container or a
    # downstream coding agent on a different host) can reach this dev server;
    # Browser and MCP requests share JWT authentication.
    uvicorn.run(app, host="0.0.0.0", port=5000)
