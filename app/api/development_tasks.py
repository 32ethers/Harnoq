"""DevelopmentTask HTTP endpoints.

P1.3: read (list/get). Creation (see docs/cn/design-evolution.md#objects) is direct — a claimable task is
created by POST /design-cases/<id>/tasks (see app/api/design_cases.py) — no
intermediate draft, no separate generation step.
P1.4: claim + pre-development review actions.
"""
from flask import Blueprint, jsonify, request

from app.services import development_task_service, suspect_service
from app.repositories import change_request_repo, development_task_repo

bp = Blueprint("development_tasks", __name__)


@bp.get("/projects/<int:project_id>/tasks")
def list_tasks(project_id):
    return jsonify(development_task_service.list_by_project_with_gate(project_id))


@bp.get("/projects/<int:project_id>/tasks/trash")
def list_deleted_tasks(project_id):
    tasks = development_task_service.list_deleted(project_id)
    return jsonify([task.to_dict() for task in tasks])


@bp.get("/tasks/<int:task_id>")
def get_task(task_id):
    task = development_task_service.get(task_id)
    payload = task.to_dict()
    payload.update({
        "change_requests": [
            row.change_request_number for row in change_request_repo.list_by_task(task.id)
        ],
        "dependencies": [
            {
                "task_number": row.depends_on_task.task_number,
                "status": row.depends_on_task.status,
                "completed": row.depends_on_task.status == "completed",
            }
            for row in development_task_repo.list_dependencies(task.id)
        ],
    })
    # Derived here rather than in to_dict(): the task list renders one dict per
    # row, and this walks every agent run of the task.
    payload["cleared_failures"] = development_task_service.cleared_failures(
        task.id
    )
    # From the latest qa verdict: which criteria no test covers, per that
    # verdict's structured evidence. See development_task_service.uncovered_criteria.
    payload["uncovered_criteria"] = development_task_service.uncovered_criteria(
        task.id
    )
    return jsonify(
        suspect_service.annotate(
            payload, suspect_service.DEVELOPMENT_TASK, task.id
        )
    )


@bp.post("/tasks/<int:task_id>/realign-basis")
def realign_task_basis(task_id):
    """Digest a suspect edge: the design moved, a human looked, the task as
    written still stands. Re-records the basis without touching the task."""
    data = request.get_json(silent=True) or {}
    task = development_task_service.realign_basis(task_id, data)
    return jsonify(
        suspect_service.annotate(
            task.to_dict(), suspect_service.DEVELOPMENT_TASK, task.id
        )
    )


@bp.patch("/tasks/<int:task_id>")
def update_task(task_id):
    data = request.get_json(silent=True) or {}
    task = development_task_service.update(task_id, data)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/claim")
def claim_task(task_id):
    data = request.get_json(silent=True) or {}
    task = development_task_service.claim(task_id, data)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/release")
def release_task(task_id):
    task = development_task_service.release(task_id)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/reopen")
def reopen_task(task_id):
    task = development_task_service.reopen_needs_revision(task_id)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/reopen-needs-revision")
def reopen_needs_revision(task_id):
    task = development_task_service.reopen_needs_revision(task_id)
    return jsonify(task.to_dict())


@bp.get("/tasks/<int:task_id>/timeline")
def get_task_timeline(task_id):
    return jsonify(development_task_service.timeline(task_id))


@bp.post("/tasks/<int:task_id>/reopen-completed")
def reopen_completed_task(task_id):
    data = request.get_json(silent=True) or {}
    task = development_task_service.reopen_completed(task_id, data)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/start-pre-review")
def start_pre_review(task_id):
    task = development_task_service.start_pre_review(task_id)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/approve-pre-review")
def approve_pre_review(task_id):
    data = request.get_json(silent=True) or {}
    task = development_task_service.approve_pre_review(task_id, data)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/reject-pre-review")
def reject_pre_review(task_id):
    data = request.get_json(silent=True) or {}
    task = development_task_service.reject_pre_review(task_id, data)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/start-coding")
def start_coding(task_id):
    task = development_task_service.start_coding(task_id)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/reject-to-design")
def reject_to_design(task_id):
    data = request.get_json(silent=True) or {}
    task = development_task_service.reject_to_design(task_id, data)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/agent-runs")
def record_agent_run(task_id):
    data = request.get_json(silent=True) or {}
    run, task = development_task_service.record_agent_run(task_id, data)
    return jsonify({"agent_run": run.to_dict(), "task": task.to_dict()})


@bp.post("/tasks/<int:task_id>/mark-ready-for-pr")
def mark_ready_for_pr(task_id):
    task = development_task_service.mark_ready_for_pr(task_id)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/link-pr")
def link_pr(task_id):
    data = request.get_json(silent=True) or {}
    record, task = development_task_service.link_pr(task_id, data)
    return jsonify({"pull_request_record": record.to_dict(), "task": task.to_dict()})


@bp.post("/tasks/<int:task_id>/mark-pr-merged")
def mark_pr_merged(task_id):
    task, record = development_task_service.mark_pr_merged(task_id)
    return jsonify(
        {
            "task": task.to_dict(),
            "pull_request_record": record.to_dict(),
        }
    )


@bp.post("/tasks/<int:task_id>/withdraw-from-pr")
def withdraw_from_pr(task_id):
    data = request.get_json(silent=True) or {}
    task = development_task_service.withdraw_from_pr(task_id, data)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/mark-completed")
def mark_completed(task_id):
    data = request.get_json(silent=True) or {}
    task = development_task_service.mark_completed(task_id, data)
    return jsonify(task.to_dict())


@bp.delete("/tasks/<int:task_id>")
def delete_task(task_id):
    task = development_task_service.delete(task_id)
    return jsonify(task.to_dict())


@bp.post("/tasks/<int:task_id>/restore")
def restore_task(task_id):
    task = development_task_service.restore(task_id)
    return jsonify(task.to_dict())


@bp.delete("/tasks/<int:task_id>/purge")
def purge_task(task_id):
    development_task_service.purge(task_id)
    return "", 204
