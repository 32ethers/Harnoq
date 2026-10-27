from datetime import datetime

from flask import Blueprint, jsonify, request

from app.core.errors import ValidationError
from app.core.http import json_object
from app.models.user import AuditRecord
from app.services import auth_service, development_task_service, task_owner_service, user_service, work_owner_service

bp = Blueprint("users", __name__)


@bp.get("/users")
def list_users():
    return jsonify([user.to_dict() for user in user_service.list_users()])


@bp.post("/users")
def create_user():
    auth_service.require_admin()
    return jsonify(user_service.create(json_object()).to_dict()), 201


@bp.patch("/users/<user_number>")
def update_user(user_number):
    auth_service.require_admin()
    return jsonify(user_service.update(user_number, json_object()).to_dict())


@bp.delete("/users/<user_number>")
def delete_user(user_number):
    auth_service.require_admin()
    return jsonify(user_service.delete(user_number).to_dict())


@bp.post("/tasks/<int:task_id>/assignee")
def assign_task(task_id):
    data = json_object()
    if "assignee_number" not in data:
        raise ValidationError("assignee_number is required; use null to clear")
    return jsonify(task_owner_service.assign(development_task_service.get(task_id), data["assignee_number"]).to_dict())


@bp.post("/projects/<project_number>/tasks/<task_number>/assignee")
def assign_task_by_number(project_number, task_number):
    from app.services import project_service
    project = project_service.get_by_number(project_number)
    task = development_task_service.get_by_number(project.id, task_number)
    return assign_task(task.id)


@bp.get("/users/workload")
def workload():
    try:
        since = datetime.strptime(request.args["since"], "%Y-%m-%d") if request.args.get("since") else None
        until = datetime.strptime(request.args["until"], "%Y-%m-%d") if request.args.get("until") else None
        project_id = int(request.args["project_id"]) if request.args.get("project_id") else None
    except ValueError:
        raise ValidationError("invalid date or project_id") from None
    if since and until and until <= since:
        raise ValidationError("until must be later than since (exclusive end date)")
    return jsonify(task_owner_service.workload(project_id=project_id, since=since, until=until))


@bp.post("/source-requirements/<int:source_requirement_id>/assignee")
def assign_requirement(source_requirement_id):
    from app.services import source_requirement_service
    entity = source_requirement_service.get(source_requirement_id)
    data = work_owner_service.assign(entity, json_object()).to_dict()
    data["current_version_number"] = source_requirement_service.current_version_number(entity.id)
    return jsonify(data)


@bp.post("/projects/<project_number>/source-requirements/<source_requirement_number>/assignee")
def assign_requirement_by_number(project_number, source_requirement_number):
    from app.services import project_service, source_requirement_service
    project = project_service.get_by_number(project_number)
    entity = source_requirement_service.get_by_number(project.id, source_requirement_number)
    data = work_owner_service.assign(entity, json_object()).to_dict()
    data["current_version_number"] = source_requirement_service.current_version_number(entity.id)
    return jsonify({key: value for key, value in data.items() if key != "id" and not key.endswith("_id")})


@bp.post("/delivery-items/<int:item_id>/assignee")
def assign_delivery_item(item_id):
    from app.services import delivery_item_service
    return jsonify(work_owner_service.assign(delivery_item_service.get(item_id), json_object()).to_dict())


@bp.post("/projects/<project_number>/delivery-items/<item_number>/assignee")
def assign_delivery_item_by_number(project_number, item_number):
    from app.services import project_service, delivery_item_service
    project = project_service.get_by_number(project_number)
    entity = delivery_item_service.get_by_number(project.id, item_number)
    data = work_owner_service.assign(entity, json_object()).to_dict()
    return jsonify({key: value for key, value in data.items() if key != "id" and not key.endswith("_id")})


@bp.get("/users/<user_number>/activity")
def activity(user_number):
    user = user_service.get(user_number, include_deleted=True)
    return jsonify([row.to_dict() for row in AuditRecord.select().where(AuditRecord.actor == user).order_by(AuditRecord.id.desc()).limit(100)])
