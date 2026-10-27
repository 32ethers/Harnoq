"""Business-number keyed task execution endpoints for coding agents.

The dashboard's existing task endpoints intentionally keep their internal-id
routes for backwards compatibility.  This surface is for downstream coding
agents, which only ever identify a project/task by P-* and TASK-*.
"""
import json

from flask import Blueprint, jsonify, request

from app.core.agent_text import normalize_agent_text
from app.core.errors import NotFoundError
from app.db.base import database_proxy
from app.repositories import delivery_item_repo, design_case_repo, feature_repo
from app.services import (
    design_case_service,
    development_task_service,
    project_constitution_service,
    project_service,
    suspect_service,
    task_source_service,
)

bp = Blueprint("agent_tasks", __name__)


@bp.after_request
def _normalize_outgoing_text(response):
    """Repair literal \\n in agent-facing task responses (design body included).

    Same read-side repair as the skills blueprint: the joined DESIGN-* text a
    coder implements against must not arrive as one unbroken line. Stored data
    is untouched — see app.core.agent_text.
    """
    if response.is_json:
        response.set_data(
            json.dumps(normalize_agent_text(response.get_json()), ensure_ascii=False)
        )
    return response


def _task(project_number, task_number):
    project = project_service.get_by_number(project_number)
    return development_task_service.get_by_number(project.id, task_number)


def _public(value):
    """Remove database primary/foreign keys from an agent-facing response."""
    if isinstance(value, dict):
        return {
            key: _public(item)
            for key, item in value.items()
            if key != "id" and not key.endswith("_id")
        }
    if isinstance(value, list):
        return [_public(item) for item in value]
    return value


@bp.post("/projects/<project_number>/designs/<design_number>/tasks")
def create_task(project_number, design_number):
    """Create one claimable TASK-* directly under a confirmed DESIGN-*.

    This is the agent-facing counterpart to POST
    /design-cases/<id>/tasks: an agent (or a human) reads a confirmed design
    and derives the one design-backed TASK from it. There is no intermediate
    draft or batch generation step. A second live TASK for the same DESIGN is
    rejected; additional work must be attached to an explicit source-backed
    TASK.
    """
    project = project_service.get_by_number(project_number)
    design_case = design_case_service.get_by_number(project.id, design_number)
    data = request.get_json(silent=True) or {}
    task = design_case_service.create_task(design_case.id, data)
    return jsonify(_public(task.to_dict())), 201


@bp.post("/projects/<project_number>/tasks/from-sources")
def create_source_task(project_number):
    """Create one task and atomically attach its approved CR sources."""
    project = project_service.get_by_number(project_number)
    data = request.get_json(silent=True) or {}
    task, _sources = task_source_service.create_and_assign(project.id, data)
    return jsonify(_public(task.to_dict() | task_source_service.list_sources(task))), 201


@bp.post("/projects/<project_number>/tasks/<task_number>/change-requests")
def assign_change_requests(project_number, task_number):
    project = project_service.get_by_number(project_number)
    data = request.get_json(silent=True) or {}
    task, requests = task_source_service.assign_change_requests(
        project.id, task_number, data.get("change_request_numbers")
    )
    return jsonify(_public({"task": task.to_dict(), "change_requests": [item.to_dict() for item in requests]}))


@bp.post("/projects/<project_number>/tasks/<task_number>/dependencies")
def add_task_dependencies(project_number, task_number):
    project = project_service.get_by_number(project_number)
    data = request.get_json(silent=True) or {}
    numbers = data.get("depends_on_task_numbers")
    if not isinstance(numbers, list) or not numbers:
        from app.core.errors import ValidationError
        raise ValidationError("depends_on_task_numbers must be a non-empty list")
    task = development_task_service.get_by_number(project.id, task_number)
    dependencies = []
    # Resolve every business number before writing, then keep cycle and
    # duplicate checks inside one transaction so a later invalid edge cannot
    # leave the earlier half of the batch committed.
    resolved = [development_task_service.get_by_number(project.id, number) for number in numbers]
    with database_proxy.atomic():
        for dependency in resolved:
            dependencies.append(
                development_task_service.add_dependency(task.id, dependency.id)
            )
    return jsonify(_public({"task": task.to_dict(), "dependencies": [row.to_dict() for row in dependencies]}))


@bp.post("/projects/<project_number>/tasks/<task_number>/dependencies/remove")
@bp.delete("/projects/<project_number>/tasks/<task_number>/dependencies")
def remove_task_dependencies(project_number, task_number):
    project = project_service.get_by_number(project_number)
    data = request.get_json(silent=True) or {}
    numbers = data.get("depends_on_task_numbers")
    if not isinstance(numbers, list) or not numbers:
        from app.core.errors import ValidationError
        raise ValidationError("depends_on_task_numbers must be a non-empty list")
    task = development_task_service.get_by_number(project.id, task_number)
    resolved = [development_task_service.get_by_number(project.id, number) for number in numbers]
    with database_proxy.atomic():
        for dependency in resolved:
            development_task_service.remove_dependency(task.id, dependency.id)
    return jsonify(_public({
        "task": task.to_dict(),
        "dependencies": [
            row.to_dict() for row in development_task_service.list_dependencies(task.id)
        ],
    }))


@bp.post("/projects/<project_number>/tasks/<task_number>/update")
def update_task(project_number, task_number):
    """Edit an existing task contract without changing state or source links."""
    from app.core.errors import ValidationError
    from app.models.task_enums import TASK_TYPES, TASK_PRIORITIES, TASK_COMPLEXITIES, TASK_RISK_LEVELS

    task = _task(project_number, task_number)
    data = request.get_json(silent=True)
    allowed = (set(development_task_service._EDITABLE_TASK_FIELDS) - {"description"}) | {"goal"}
    if not isinstance(data, dict) or not data:
        raise ValidationError("provide at least one editable task field")
    unknown = set(data) - allowed
    if unknown:
        raise ValidationError("unsupported task update fields: " + ", ".join(sorted(unknown)))
    for field, value in data.items():
        if not isinstance(value, str):
            raise ValidationError(f"{field} must be a string; omit unchanged fields")
        if field in {"title", "goal", "acceptance_criteria"} and not value.strip():
            raise ValidationError(f"{field} must not be empty")
    for field, values in (
        ("task_type", TASK_TYPES), ("priority", TASK_PRIORITIES),
        ("complexity", TASK_COMPLEXITIES), ("risk_level", TASK_RISK_LEVELS),
    ):
        if field in data and data[field] not in values:
            raise ValidationError(f"invalid {field}: {data[field]}")
    if "goal" in data:
        data["description"] = data.pop("goal")
    result = development_task_service.update(task.id, data)
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/claim")
def claim_task(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.claim(task.id, request.get_json(silent=True) or {})
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/reopen")
def reopen_task(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.reopen_needs_revision(task.id)
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/reopen-completed")
def reopen_completed_task(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.reopen_completed(
        task.id, request.get_json(silent=True) or {}
    )
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/realign-basis")
def realign_task_basis(project_number, task_number):
    task = _task(project_number, task_number)
    development_task_service.realign_basis(
        task.id, request.get_json(silent=True) or {}
    )
    return jsonify(
        _public(
            suspect_service.annotate(
                task.to_dict(), suspect_service.DEVELOPMENT_TASK, task.id
            )
        )
    )


@bp.post("/projects/<project_number>/tasks/<task_number>/release")
def release_task(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.release(task.id)
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/start-pre-review")
def start_pre_review(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.start_pre_review(task.id)
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/approve-pre-review")
def approve_pre_review(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.approve_pre_review(
        task.id, request.get_json(silent=True) or {}
    )
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/reject-pre-review")
def reject_pre_review(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.reject_pre_review(
        task.id, request.get_json(silent=True) or {}
    )
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/start-coding")
def start_coding(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.start_coding(task.id)
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/reject-to-design")
def reject_to_design(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.reject_to_design(
        task.id, request.get_json(silent=True) or {}
    )
    return jsonify(_public(result.to_dict()))


@bp.get("/projects/<project_number>/tasks/<task_number>")
def get_task(project_number, task_number):
    """Read one task, with its linked design case's full text joined in.

    The join is the point of this endpoint.  A 1:1 task's own fields are
    deliberately a POINTER ("以 DESIGN-X 的验收/约束为准") rather than a re-encoding of the
    design, and the task-planning prompt promises the coder that the design
    arrives with the task.  Without the join a coder reads four pointer strings
    plus a bare ``design_number`` and has to know to make a second call, so the
    spec silently degrades to whatever the title implies -- which is how TASK-1
    shipped missing the three modules that existed only in the design body.

    ``design`` is null only when the linked design case is missing or
    soft-deleted; that is surfaced rather than hidden, because a task whose
    spec cannot be read must not look like a task with no constraints.

    ``project_constitution`` rides along the same way and for the same
    reason: a coding agent only ever receives ONE TASK-*, so project-wide
    governance (fixed stack, quality bar, UI conventions, NFR baseline,
    boundaries) has to be PUSHED to it here rather than left for the coder to
    go fetch on its own -- pushing is the option a server has that a written
    spec handed to a human does not. It is populated only when the project's
    constitution ``status == "confirmed"``; a draft/needs_clarification
    constitution is not yet a governance decision, so handing its half-written
    content to a coder would let it be read as settled when it is not.
    Missing or unconfirmed both serialize to ``null`` rather than the key
    being omitted, for the same reason ``design`` is null instead of absent:
    a coder that cannot tell "no confirmed constitution yet" from "the field
    was left out" cannot tell an intentional gap from a bug.

    ``feature`` identifies the capability owner without exposing its raw id.
    Integration and System obligations belong to delivery-stage runs rather
    than to development tasks.
    """
    task = _task(project_number, task_number)
    payload = task.to_dict()
    design_case = (
        design_case_repo.get(task.design_case_id) if task.design_case_id else None
    )
    payload["design"] = design_case.to_dict() if design_case is not None else None
    feature_id = task.feature_id or (
        design_case.feature_id
        if design_case is not None and design_case.feature_id is not None
        else None
    )
    feature = feature_repo.get(feature_id) if feature_id is not None else None
    payload["feature"] = (
        {
            "feature_number": feature.feature_number,
            "name": feature.name,
            "foundation_kind": feature.foundation_kind,
        }
        if feature is not None
        else None
    )
    payload["execution_kind"] = "capability"
    sources = task_source_service.list_sources(task)
    payload["change_requests"] = sources["change_requests"]
    payload["dependencies"] = [
        row.to_dict() for row in development_task_service.list_dependencies(task.id)
    ]
    constitution = project_constitution_service.get_for_project(task.project_id)
    payload["project_constitution"] = (
        constitution.to_dict()
        if constitution is not None and constitution.status == "confirmed"
        else None
    )
    # Oldest first. A reopened task has prior attempts, and a manager picking
    # up a retry needs to see that PR #1 already merged -- otherwise it reports
    # the rework as if it were the first delivery.
    payload["pull_requests"] = [
        record.to_dict()
        for record in development_task_service.list_pull_requests(task.id)
    ]
    # Criteria qa once judged broken that stopped being broken without the
    # suite gaining anything, or without the criterion surviving. Pushed for the
    # same reason the constitution is: manager decides whether the delivery is
    # believable, and it cannot ask a question nobody put in front of it.
    payload["cleared_failures"] = development_task_service.cleared_failures(
        task.id
    )
    # From the latest qa verdict: criteria whose structured evidence names no
    # covering test. Pushed for the same reason cleared_failures is: manager
    # decides whether a delivery is believable and cannot ask a question the
    # response never puts in front of it.
    payload["uncovered_criteria"] = development_task_service.uncovered_criteria(
        task.id
    )
    # Where each criterion of this design was last found satisfied — the index
    # a re-verifying qa run would otherwise rebuild from scratch. Agent-facing
    # only, and deliberately not on the browser route: it is a working aid for
    # the run in flight, not a fact about the task worth reading on a page.
    payload["prior_evidence"] = development_task_service.prior_evidence(task.id)
    # Whether the design has been revised since this task was created — the
    # coder is otherwise working from a spec that moved under it.
    payload = suspect_service.annotate(
        payload, suspect_service.DEVELOPMENT_TASK, task.id
    )
    return jsonify(_public(payload))


@bp.get("/projects/<project_number>/integration-items/<item_number>")
def get_integration_item_by_number(project_number, item_number):
    """Read one project-wide Integration finding by business number."""
    project = project_service.get_by_number(project_number)
    item = delivery_item_repo.get_by_number(project.id, item_number)
    if item is None or item.kind not in ("assembly", "seam", "duplicate"):
        raise NotFoundError(f"integration item {item_number} not found")
    return jsonify(_public(item.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/agent-runs")
def record_agent_run(project_number, task_number):
    task = _task(project_number, task_number)
    run, result_task = development_task_service.record_agent_run(
        task.id, request.get_json(silent=True) or {}
    )
    return jsonify(_public({"agent_run": run.to_dict(), "task": result_task.to_dict()}))


@bp.post("/projects/<project_number>/tasks/<task_number>/mark-ready-for-pr")
def mark_ready_for_pr(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.mark_ready_for_pr(task.id)
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/link-pr")
def link_pr(project_number, task_number):
    task = _task(project_number, task_number)
    data = request.get_json(silent=True) or {}
    record, result_task = development_task_service.link_pr(task.id, data)
    return jsonify(_public({"pull_request_record": record.to_dict(), "task": result_task.to_dict()}))


@bp.post("/projects/<project_number>/tasks/<task_number>/mark-pr-merged")
def mark_pr_merged(project_number, task_number):
    task = _task(project_number, task_number)
    result_task, record = development_task_service.mark_pr_merged(task.id)
    return jsonify(_public({
        "task": result_task.to_dict(),
        "pull_request_record": record.to_dict(),
    }))


@bp.post("/projects/<project_number>/tasks/<task_number>/withdraw-from-pr")
def withdraw_from_pr(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.withdraw_from_pr(
        task.id, request.get_json(silent=True) or {}
    )
    return jsonify(_public(result.to_dict()))


@bp.post("/projects/<project_number>/tasks/<task_number>/mark-completed")
def mark_completed(project_number, task_number):
    task = _task(project_number, task_number)
    result = development_task_service.mark_completed(
        task.id, request.get_json(silent=True) or {}
    )
    return jsonify(_public(result.to_dict()))
