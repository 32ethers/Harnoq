"""SourceRequirement HTTP endpoints."""
from flask import Blueprint, jsonify, request

from app.services import source_requirement_service

bp = Blueprint("source_requirements", __name__)


def _serialize_source_requirement(source_requirement):
    data = source_requirement.to_dict()
    data["current_version_number"] = source_requirement_service.current_version_number(
        source_requirement.id
    )
    return data


@bp.post("/projects/<int:project_id>/source-requirements")
def create_source_requirement(project_id):
    data = request.get_json(silent=True) or {}
    source_requirement = source_requirement_service.create(project_id, data)
    return jsonify(_serialize_source_requirement(source_requirement)), 201


@bp.get("/projects/<int:project_id>/source-requirements")
def list_source_requirements(project_id):
    items = source_requirement_service.list_by_project(project_id)
    return jsonify([item.to_dict() for item in items])


@bp.get("/projects/<int:project_id>/source-requirements/trash")
def list_deleted_source_requirements(project_id):
    items = source_requirement_service.list_deleted(project_id)
    return jsonify([item.to_dict() for item in items])


@bp.get("/source-requirements/<int:source_requirement_id>")
def get_source_requirement(source_requirement_id):
    source_requirement = source_requirement_service.get(source_requirement_id)
    return jsonify(_serialize_source_requirement(source_requirement))


@bp.patch("/source-requirements/<int:source_requirement_id>")
def update_source_requirement(source_requirement_id):
    data = request.get_json(silent=True) or {}
    source_requirement = source_requirement_service.update(source_requirement_id, data)
    return jsonify(_serialize_source_requirement(source_requirement))


@bp.post("/source-requirements/<int:source_requirement_id>/confirm")
def confirm_source_requirement(source_requirement_id):
    source_requirement = source_requirement_service.confirm(source_requirement_id)
    return jsonify(_serialize_source_requirement(source_requirement))


@bp.post("/source-requirements/<int:source_requirement_id>/reopen")
def reopen_source_requirement(source_requirement_id):
    source_requirement = source_requirement_service.reopen(source_requirement_id)
    return jsonify(_serialize_source_requirement(source_requirement))


@bp.get("/source-requirements/<int:source_requirement_id>/versions")
def list_source_requirement_versions(source_requirement_id):
    versions = source_requirement_service.list_versions(source_requirement_id)
    current_version_number = source_requirement_service.current_version_number(source_requirement_id)
    return jsonify([
        version.to_dict() | {
            "is_current": version.version_number == current_version_number,
        }
        for version in versions
    ])


@bp.post("/source-requirements/<int:source_requirement_id>/versions/<int:version_number>/restore")
def restore_source_requirement_version(source_requirement_id, version_number):
    source_requirement = source_requirement_service.restore_version(source_requirement_id, version_number)
    return jsonify(_serialize_source_requirement(source_requirement))


@bp.delete("/source-requirements/<int:source_requirement_id>")
def delete_source_requirement(source_requirement_id):
    source_requirement = source_requirement_service.delete(source_requirement_id)
    return jsonify(_serialize_source_requirement(source_requirement))


@bp.post("/source-requirements/<int:source_requirement_id>/restore")
def restore_source_requirement(source_requirement_id):
    source_requirement = source_requirement_service.restore(source_requirement_id)
    return jsonify(_serialize_source_requirement(source_requirement))


@bp.delete("/source-requirements/<int:source_requirement_id>/purge")
def purge_source_requirement(source_requirement_id):
    source_requirement_service.purge(source_requirement_id)
    return "", 204
