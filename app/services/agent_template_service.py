"""Generate downstream coding-agent configuration from current project data."""
import hashlib
import json
from pathlib import Path

from app.core.errors import ConflictError, ValidationError
from app.services import project_constitution_service, project_service
from app.services import agent_template_builder

TEMPLATE_MANIFEST = ".harnoq-agent-template.json"
TEMPLATE_FORMAT_VERSION = 1


def prompt_fingerprint():
    """Stable fingerprint of the generator source that owns downstream prose."""
    source = Path(agent_template_builder.__file__).read_bytes()
    return "sha256:" + hashlib.sha256(source).hexdigest()


def _with_manifest(files, flavor, project_number):
    manifest = {
        "format_version": TEMPLATE_FORMAT_VERSION,
        "flavor": flavor,
        "prompt_fingerprint": prompt_fingerprint(),
        "refresh_command": (
            "uv run scripts/refresh-agent-config.py <repo-path> "
            f"--project {project_number} --flavor {flavor}"
        ),
    }
    return files | {
        TEMPLATE_MANIFEST: json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
    }


def generate(project_id, flavor, mcp_url, model):
    """Build one non-persisted configuration flavor for a governed project.

    ``mcp_url`` is Harnoq's remote streamable-http MCP endpoint (e.g.
    ``http://harnoq-host:5000/mcp``) that generated configs point clients at.
    ``model`` is the opencode model id every agent uses (environment-specific,
    supplied at download time); it is ignored by the Claude flavor, whose
    subagents inherit the parent session's model.
    """
    project = project_service.get(project_id)
    constitution = project_constitution_service.require_for_project(project_id)
    if constitution.status != "confirmed":
        raise ConflictError("project constitution must be confirmed before generating agent config")
    if flavor == "claude":
        return _with_manifest(
            agent_template_builder.build_claude_code(project, constitution, mcp_url),
            flavor,
            project.project_number,
        )
    if flavor == "opencode":
        return _with_manifest(
            agent_template_builder.build_opencode(
                project, constitution, mcp_url, model
            ),
            flavor,
            project.project_number,
        )
    raise ValidationError("flavor must be 'claude' or 'opencode'")
