from flask import Blueprint, jsonify, request
from app.services import delivery_stage_service as service

bp = Blueprint("delivery_stage_runs", __name__)

@bp.get("/projects/<project_number>/delivery-stage-runs")
def list_runs(project_number):
    from app.core.errors import ValidationError
    from app.models.delivery_stage_run import STAGES
    from app.repositories import delivery_stage_run_repo
    from app.services import project_service, work_owner_service
    stage = request.args.get("stage")
    if stage and stage not in STAGES:
        raise ValidationError("stage must be integration or system")
    project = project_service.get_by_number(project_number)
    return jsonify([run.to_dict() for run in work_owner_service.preload(delivery_stage_run_repo.list_by_project(project.id))
                    if stage is None or run.stage == stage])

@bp.post("/projects/<project_number>/delivery-stage-runs/<stage_or_run_number>")
def start_or_update(project_number, stage_or_run_number):
    from app.services import project_service
    project = project_service.get_by_number(project_number)
    data = request.get_json(silent=True) or {}
    if stage_or_run_number in ("integration", "system"):
        return jsonify(service.start(project.id, stage_or_run_number, data).to_dict()), 201
    return jsonify(service.update(project.id, stage_or_run_number, data).to_dict())

@bp.patch("/projects/<project_number>/delivery-stage-runs/<run_number>")
@bp.post("/projects/<project_number>/delivery-stage-runs/<run_number>/update")
def update(project_number, run_number):
    from app.services import project_service
    return jsonify(service.update(project_service.get_by_number(project_number).id, run_number, request.get_json(silent=True) or {}).to_dict())

@bp.post("/projects/<project_number>/delivery-stage-runs/<run_number>/close")
def close(project_number, run_number):
    from app.services import project_service
    return jsonify(service.close(project_service.get_by_number(project_number).id, run_number, request.get_json(silent=True) or {}).to_dict())

@bp.post("/projects/<project_number>/delivery-stage-runs/<run_number>/abandon")
def abandon(project_number, run_number):
    from app.services import project_service
    return jsonify(service.abandon(project_service.get_by_number(project_number).id, run_number, request.get_json(silent=True) or {}).to_dict())
