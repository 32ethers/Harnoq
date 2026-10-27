"""DesignCase HTTP endpoints."""
from flask import Blueprint, jsonify, request

from app.repositories import development_task_repo
from app.services import (
    design_case_service,
    nfr_coverage_service,
    requirement_digest_service,
    suspect_service,
)

bp = Blueprint("design_cases", __name__)


def _serialize_design_case(design_case):
    data = design_case.to_dict()
    data["current_version_number"] = design_case_service.current_version_number(
        design_case.id
    )
    # Which TASK-* this design already has, so the page can show "already
    # decomposed" / offer "new task" without a separate round trip (task
    # creation is direct now — no intermediate draft, no generation step).
    tasks = development_task_repo.list_by_design_case(design_case.id)
    data["task_numbers"] = [task.task_number for task in tasks]
    data["task_count"] = len(tasks)
    # Which of the things this design was confirmed against have moved since.
    return suspect_service.annotate(data, suspect_service.DESIGN_CASE, design_case.id)


@bp.post("/projects/<int:project_id>/design-cases")
def create_project_design_case(project_id):
    data = request.get_json(silent=True) or {}
    design_case = design_case_service.create(project_id, data)
    return jsonify(_serialize_design_case(design_case)), 201


@bp.get("/projects/<int:project_id>/design-cases")
def list_design_cases_by_project(project_id):
    design_cases = design_case_service.list_by_project(project_id)
    ctx = design_case_service.dict_ctx(project_id)
    # One bulk lookup for the whole list — never a suspect query per design.
    stale = suspect_service.stale_edges_by_downstream(project_id)
    return jsonify([
        suspect_service.annotate(
            design_case.to_dict(ctx=ctx),
            suspect_service.DESIGN_CASE,
            design_case.id,
            stale,
        )
        for design_case in design_cases
    ])


@bp.get("/projects/<int:project_id>/requirement-digest")
def project_requirement_digest(project_id):
    """The project's whole requirement, derived from every design's
    mined_requirements. Read-only and stored nowhere — see
    app/services/requirement_digest_service.py for why it is derived."""
    return jsonify(requirement_digest_service.build(project_id))


@bp.get("/projects/<int:project_id>/nfr-coverage")
def project_nfr_coverage(project_id):
    """Which constitution nfr_baseline rows a design claims to verify, and
    which nobody does. Read-only and stored nowhere — see
    app/services/nfr_coverage_service.py for why it is derived."""
    return jsonify(nfr_coverage_service.build(project_id))


@bp.get("/projects/<int:project_id>/design-cases/trash")
def list_deleted_design_cases(project_id):
    design_cases = design_case_service.list_deleted(project_id)
    return jsonify([design_case.to_dict() for design_case in design_cases])


@bp.get("/design-cases/<int:design_case_id>")
def get_design_case(design_case_id):
    design_case = design_case_service.get(design_case_id)
    return jsonify(_serialize_design_case(design_case))


@bp.patch("/design-cases/<int:design_case_id>")
def update_design_case(design_case_id):
    data = request.get_json(silent=True) or {}
    design_case = design_case_service.update(design_case_id, data)
    return jsonify(_serialize_design_case(design_case))


@bp.post("/design-cases/<int:design_case_id>/confirm")
def confirm_design_case(design_case_id):
    design_case = design_case_service.confirm(design_case_id)
    return jsonify(_serialize_design_case(design_case))


@bp.post("/design-cases/<int:design_case_id>/reject")
def reject_design_case(design_case_id):
    design_case = design_case_service.reject(design_case_id)
    return jsonify(_serialize_design_case(design_case))


@bp.post("/design-cases/<int:design_case_id>/realign-basis")
def realign_design_case_basis(design_case_id):
    """Digest a suspect edge: "I looked at the upstream change, this design
    still holds." Re-records the basis at today's versions without touching
    content or status."""
    design_case = design_case_service.get(design_case_id)
    suspect_service.realign(suspect_service.DESIGN_CASE, design_case.id)
    return jsonify(_serialize_design_case(design_case))


@bp.post("/design-cases/<int:design_case_id>/reopen")
def reopen_design_case(design_case_id):
    design_case = design_case_service.reopen(design_case_id)
    return jsonify(_serialize_design_case(design_case))


@bp.get("/design-cases/<int:design_case_id>/versions")
def list_design_case_versions(design_case_id):
    versions = design_case_service.list_versions(design_case_id)
    current = design_case_service.current_version_number(design_case_id)
    return jsonify([
        version.to_dict() | {"is_current": version.version_number == current}
        for version in versions
    ])


@bp.post("/design-cases/<int:design_case_id>/versions/<int:version_number>/restore")
def restore_design_case_version(design_case_id, version_number):
    design_case = design_case_service.restore_version(design_case_id, version_number)
    return jsonify(_serialize_design_case(design_case))


@bp.delete("/design-cases/<int:design_case_id>")
def delete_design_case(design_case_id):
    design_case = design_case_service.delete(design_case_id)
    return jsonify(_serialize_design_case(design_case))


@bp.post("/design-cases/<int:design_case_id>/restore")
def restore_design_case(design_case_id):
    design_case = design_case_service.restore(design_case_id)
    return jsonify(_serialize_design_case(design_case))


@bp.delete("/design-cases/<int:design_case_id>/purge")
def purge_design_case(design_case_id):
    design_case_service.purge(design_case_id)
    return "", 204


@bp.post("/design-cases/<int:design_case_id>/tasks")
def create_design_case_task(design_case_id):
    data = request.get_json(silent=True) or {}
    task = design_case_service.create_task(design_case_id, data)
    return jsonify(task.to_dict()), 201
