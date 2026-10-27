"""Feature HTTP endpoints."""
from flask import Blueprint, jsonify, request

from app.services import feature_service

bp = Blueprint("features", __name__)


def _serialize_feature(feature, ctx=None, *, include_current_version=False):
    if ctx is None:
        data = feature.to_dict()
    else:
        data = feature.to_dict(
            dependencies=ctx["dependencies_by_feature_id"].get(feature.id, []),
            design_cases=ctx["design_cases_by_feature_id"].get(feature.id, []),
        )
    if include_current_version:
        data["current_version_number"] = feature_service.current_version_number(
            feature.id
        )
    return data


@bp.post("/projects/<int:project_id>/features")
def create_feature(project_id):
    data = request.get_json(silent=True) or {}
    feature = feature_service.create(project_id, data)
    return jsonify(_serialize_feature(feature, include_current_version=True)), 201


@bp.get("/projects/<int:project_id>/features")
def list_features(project_id):
    features = feature_service.list_by_project(project_id)
    ctx = feature_service.dict_ctx(project_id)
    return jsonify([_serialize_feature(feature, ctx=ctx) for feature in features])


@bp.get("/projects/<int:project_id>/features/trash")
def list_deleted_features(project_id):
    features = feature_service.list_deleted(project_id)
    return jsonify([_serialize_feature(feature) for feature in features])


@bp.get("/features/<int:feature_id>")
def get_feature(feature_id):
    feature = feature_service.get(feature_id)
    return jsonify(_serialize_feature(feature, include_current_version=True))


@bp.patch("/features/<int:feature_id>")
def update_feature(feature_id):
    data = request.get_json(silent=True) or {}
    feature = feature_service.update(feature_id, data)
    return jsonify(_serialize_feature(feature, include_current_version=True))


@bp.get("/features/<int:feature_id>/versions")
def list_feature_versions(feature_id):
    versions = feature_service.list_versions(feature_id)
    current = feature_service.current_version_number(feature_id)
    return jsonify([
        version.to_dict() | {"is_current": version.version_number == current}
        for version in versions
    ])


@bp.post("/features/<int:feature_id>/versions/<int:version_number>/restore")
def restore_feature_version(feature_id, version_number):
    feature = feature_service.restore_version(feature_id, version_number)
    return jsonify(_serialize_feature(feature, include_current_version=True))


@bp.post("/features/<int:feature_id>/assignee")
def set_feature_assignee(feature_id):
    data = request.get_json(silent=True) or {}
    assignment = {"assignee_number": data["assignee_number"]} if "assignee_number" in data else {"assignee": data.get("assignee")}
    feature = feature_service.update(feature_id, assignment)
    return jsonify(_serialize_feature(feature, include_current_version=True))


@bp.delete("/features/<int:feature_id>")
def delete_feature(feature_id):
    feature = feature_service.delete(feature_id)
    return jsonify(_serialize_feature(feature))


@bp.post("/features/<int:feature_id>/restore")
def restore_feature(feature_id):
    feature = feature_service.restore(feature_id)
    return jsonify(_serialize_feature(feature))


@bp.delete("/features/<int:feature_id>/purge")
def purge_feature(feature_id):
    feature_service.purge(feature_id)
    return "", 204
