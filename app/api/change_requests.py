"""ChangeRequest HTTP endpoints."""
from flask import Blueprint, jsonify, request

from app.repositories import design_case_repo, development_task_repo
from app.services import (
    change_request_service,
    design_case_service,
    development_task_service,
)

bp = Blueprint("change_requests", __name__)


def _target_number(change_request, ctx=None):
    """The target's business number, since the UI never shows raw ids.

    ``ctx`` is a pre-built {(type, id): number} map for list rendering; without
    it this is one lookup, which is what a single-object read wants anyway.
    """
    if change_request.target_id is None:
        return None
    key = (change_request.target_type, change_request.target_id)
    if ctx is not None:
        return ctx.get(key)
    if change_request.target_type == "development_task":
        target = development_task_repo.get(change_request.target_id)
        return target.task_number if target else None
    target = design_case_repo.get(change_request.target_id)
    return target.design_number if target else None


def _list_ctx(project_id):
    """One query per target kind for a whole list — not one per row."""
    ctx = {}
    for design_case in design_case_repo.list_by_project(project_id):
        ctx[("design_case", design_case.id)] = design_case.design_number
    for task in development_task_repo.list_by_project(project_id):
        ctx[("development_task", task.id)] = task.task_number
    return ctx


def _serialize(change_request, ctx=None):
    return change_request.to_dict(target_number=_target_number(change_request, ctx))


@bp.post("/projects/<int:project_id>/change-requests")
def create_change_request(project_id):
    data = request.get_json(silent=True) or {}
    change_request = change_request_service.create(project_id, data)
    return jsonify(_serialize(change_request)), 201


@bp.get("/projects/<int:project_id>/change-requests")
def list_change_requests(project_id):
    change_requests = change_request_service.list_by_project(project_id)
    ctx = _list_ctx(project_id)
    return jsonify([_serialize(cr, ctx) for cr in change_requests])


@bp.get("/projects/<int:project_id>/change-requests/trash")
def list_deleted_change_requests(project_id):
    change_requests = change_request_service.list_deleted(project_id)
    ctx = _list_ctx(project_id)
    return jsonify([_serialize(cr, ctx) for cr in change_requests])


@bp.get("/change-requests/<int:change_request_id>")
def get_change_request(change_request_id):
    change_request = change_request_service.get(change_request_id)
    return jsonify(_serialize(change_request))


@bp.patch("/change-requests/<int:change_request_id>")
def update_change_request(change_request_id):
    data = request.get_json(silent=True) or {}
    change_request = change_request_service.update(change_request_id, data)
    return jsonify(_serialize(change_request))


@bp.get("/projects/<int:project_id>/change-impact")
def preview_change_impact(project_id):
    """What WOULD be affected, before anything is proposed.

    Read-only on purpose: asking "what does this break" must not require first
    creating a record, or people will skip the question.
    """
    target_type = request.args.get("target_type", "")
    raw_target_id = request.args.get("target_id")
    target_id = int(raw_target_id) if raw_target_id and raw_target_id.isdigit() else None
    return jsonify(change_request_service.impact_of(project_id, target_type, target_id))


@bp.post("/change-requests/<int:change_request_id>/analyze")
def analyze_change_request(change_request_id):
    change_request = change_request_service.analyze(change_request_id)
    return jsonify(_serialize(change_request))


@bp.post("/change-requests/<int:change_request_id>/approve")
def approve_change_request(change_request_id):
    change_request = change_request_service.approve(change_request_id)
    return jsonify(_serialize(change_request))


@bp.post("/change-requests/<int:change_request_id>/apply")
def apply_change_request(change_request_id):
    change_request = change_request_service.apply(change_request_id)
    return jsonify(_serialize(change_request))


@bp.post("/change-requests/<int:change_request_id>/close")
def close_change_request(change_request_id):
    data = request.get_json(silent=True) or {}
    change_request = change_request_service.close(change_request_id, data)
    return jsonify(_serialize(change_request))


@bp.delete("/change-requests/<int:change_request_id>")
def delete_change_request(change_request_id):
    change_request = change_request_service.delete(change_request_id)
    return jsonify(_serialize(change_request))


@bp.post("/change-requests/<int:change_request_id>/restore")
def restore_change_request(change_request_id):
    change_request = change_request_service.restore(change_request_id)
    return jsonify(_serialize(change_request))


@bp.delete("/change-requests/<int:change_request_id>/purge")
def purge_change_request(change_request_id):
    change_request_service.purge(change_request_id)
    return "", 204


@bp.get("/design-cases/<int:design_case_id>/change-requests")
def change_requests_for_design(design_case_id):
    """Which change requests rewrote this design. Read-only and derived.

    Separate from the design payload on purpose: folding it into to_dict()
    would run this scan once PER ROW on the design list page.
    """
    design_case = design_case_service.get(design_case_id)
    rows = change_request_service.list_for_target(
        design_case.project_id, "design_case", design_case.id
    )
    ctx = _list_ctx(design_case.project_id)
    return jsonify([_serialize(cr, ctx) for cr in rows])


@bp.get("/tasks/<int:task_id>/change-requests")
def change_requests_for_task(task_id):
    """Change requests that rewrote this task's DELIVERED code, plus those that
    rewrote the design it is built against — the latter are what changed its
    specification, so a list without them answers the wrong question."""
    task = development_task_service.get(task_id)
    rows = change_request_service.list_for_target(
        task.project_id, "development_task", task.id
    )
    seen = {cr.id for cr in rows}
    if task.design_case_id:
        for cr in change_request_service.list_for_target(
            task.project_id, "design_case", task.design_case_id
        ):
            if cr.id not in seen:
                rows.append(cr)
    ctx = _list_ctx(task.project_id)
    return jsonify([_serialize(cr, ctx) for cr in rows])
