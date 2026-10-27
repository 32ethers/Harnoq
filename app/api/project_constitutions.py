"""Project constitution HTTP endpoints.

The constitution is a singleton per project, so every route is keyed by
project (there is no constitution id or business number in the URL)."""
from flask import Blueprint, jsonify, request

from app.core.errors import NotFoundError
from app.services import project_constitution_service as service

bp = Blueprint("project_constitutions", __name__)


def _serialize(constitution):
    return constitution.to_dict()


@bp.get("/projects/<int:project_id>/constitution")
def get_constitution(project_id):
    constitution = service.get_for_project(project_id)
    if constitution is None:
        raise NotFoundError(f"project {project_id} has no constitution")
    return jsonify(_serialize(constitution))


@bp.patch("/projects/<int:project_id>/constitution")
def update_constitution(project_id):
    """Upsert + edit the singleton constitution (content fields and/or an
    editable status move). Creates an empty draft on first call."""
    data = request.get_json(silent=True) or {}
    constitution = service.update(project_id, data)
    return jsonify(_serialize(constitution))


@bp.post("/projects/<int:project_id>/constitution/confirm")
def confirm_constitution(project_id):
    constitution = service.confirm(project_id)
    return jsonify(_serialize(constitution))


@bp.post("/projects/<int:project_id>/constitution/reopen")
def reopen_constitution(project_id):
    constitution = service.reopen(project_id)
    return jsonify(_serialize(constitution))


@bp.get("/projects/<int:project_id>/constitution/versions")
def list_constitution_versions(project_id):
    versions = service.list_versions(project_id)
    return jsonify([version.to_dict() for version in versions])


@bp.post("/projects/<int:project_id>/constitution/versions/<int:version_number>/restore")
def restore_constitution_version(project_id, version_number):
    constitution = service.restore_version(project_id, version_number)
    return jsonify(_serialize(constitution))
