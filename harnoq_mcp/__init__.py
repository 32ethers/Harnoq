"""Harnoq MCP server — native tools for Claude (and any MCP client).

This is the Claude-side adapter agreed in the project's integration design: the
shared contract is Harnoq's HTTP/OpenAPI skill API (``app/api/skills.py`` +
``skills/openapi.yaml``). OpenWebUI consumes that OpenAPI directly; this package
is a thin MCP wrapper over the *same* HTTP endpoints so an MCP client gets the
skills as schema-validated native tools. It adds no new business logic — every
tool POSTs a flat JSON body to one skill endpoint and returns its envelope.
"""
from .server import mcp

__all__ = ["mcp"]
