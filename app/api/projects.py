"""Project HTTP endpoints."""
from flask import Blueprint, jsonify, request

from app.services import delivery_gate, project_service, trash_service

bp = Blueprint("projects", __name__)


@bp.post("/projects")
def create_project():
    data = request.get_json(silent=True) or {}
    project = project_service.create(data)
    return jsonify(project.to_dict()), 201


@bp.get("/projects")
def list_projects():
    projects = project_service.list_all()
    return jsonify([project.to_dict() for project in projects])


@bp.get("/projects/trash")
def list_deleted_projects():
    projects = trash_service.deleted_projects()
    return jsonify([project.to_dict() for project in projects])


@bp.get("/projects/<int:project_id>")
def get_project(project_id):
    project = project_service.get(project_id)
    return jsonify(project.to_dict())


@bp.get("/projects/<int:project_id>/delivery-gate")
def get_delivery_gate(project_id):
    """Whether this project still owes integration / system work.

    Derived on read (see app/services/delivery_gate.py). Deliberately its own
    endpoint rather than a key on the project dict: the project list renders
    one dict per row, and this walks every task of the project.
    """
    project_service.get(project_id)  # 404s for an unknown project
    return jsonify(delivery_gate.gate_status(project_id))


@bp.patch("/projects/<int:project_id>")
def update_project(project_id):
    data = request.get_json(silent=True) or {}
    project = project_service.update(project_id, data)
    return jsonify(project.to_dict())


@bp.delete("/projects/<int:project_id>")
def delete_project(project_id):
    project = project_service.delete(project_id)
    return jsonify(project.to_dict())


@bp.post("/projects/<int:project_id>/restore")
def restore_project(project_id):
    project = project_service.restore(project_id)
    return jsonify(project.to_dict())


@bp.delete("/projects/<int:project_id>/purge")
def purge_project(project_id):
    project_service.purge(project_id)
    return "", 204
