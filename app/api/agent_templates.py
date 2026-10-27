"""Generated downstream coding-agent configuration endpoints.

The generated config points clients at Harnoq's streamable-http MCP endpoint,
served at ``/mcp`` on the same process/port as this web server (see
``main.py``), so the URL is derived from the request host (or the
``HARNOQ_MCP_URL`` override for reverse-proxied / custom deployments).

The download is a zip of the ready-to-use files at their repo-relative paths,
so a developer unzips it at their repo root — no JSON to hand-assemble.
"""
import io
import os
import zipfile

from flask import Blueprint, Response, request

from app.services import agent_template_service

bp = Blueprint("agent_templates", __name__)


def _mcp_url():
    """Harnoq's MCP endpoint. Explicit override wins; otherwise derive
    ``<scheme>://<request-host>/mcp`` (same host:port as this web server)."""
    override = os.environ.get("HARNOQ_MCP_URL")
    if override:
        return override
    return f"{request.scheme}://{request.host}/mcp"


_DEFAULT_OPENCODE_MODEL = "REPLACE_WITH_YOUR_OPENCODE_MODEL"


@bp.get("/projects/<int:project_id>/agent-template")
def get_agent_template(project_id):
    flavor = request.args.get("flavor", "")
    # opencode model is environment-specific (see builder); take it from the
    # caller, else a clearly-invalid placeholder so a missing choice is obvious.
    model = request.args.get("model") or _DEFAULT_OPENCODE_MODEL
    files = agent_template_service.generate(project_id, flavor, _mcp_url(), model)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, content in files.items():
            archive.writestr(path, content)

    filename = f"harnoq-{flavor}-agent-config.zip"
    fingerprint = agent_template_service.prompt_fingerprint()
    return Response(
        buffer.getvalue(),
        mimetype="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Harnoq-Agent-Template-Fingerprint": fingerprint,
        },
    )
