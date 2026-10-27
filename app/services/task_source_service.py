"""Attach approved change requests to one execution task."""

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.repositories import (
    change_request_repo,
    development_task_repo,
    feature_repo,
    project_repo,
)
from app.services import development_task_service
from app.db.base import database_proxy
from app.models.task_enums import TASK_COMPLEXITIES, TASK_PRIORITIES, TASK_RISK_LEVELS, TASK_TYPES


def _task(project_id, task_number):
    task = development_task_repo.get_by_number(project_id, task_number)
    if task is None:
        raise NotFoundError(f"task {task_number} not found")
    return task


def _ensure_project(project_id):
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")


def _ensure_assignable(task):
    if task.status == "claimable":
        return
    if task.status == "needs_revision" and task.source_kind != "design":
        return
    raise ConflictError(
        "task sources can be assigned only while a task is claimable or "
        "a source-backed task is in needs_revision"
    )


def assign_change_requests(project_id, task_number, change_request_numbers):
    """Assign approved CR-* rows to one execution task."""
    _ensure_project(project_id)
    task = _task(project_id, task_number)
    _ensure_assignable(task)
    if not isinstance(change_request_numbers, list) or not change_request_numbers:
        raise ValidationError("change_request_numbers must be a non-empty list")
    requests = []
    seen = set()
    for number in change_request_numbers:
        if not isinstance(number, str) or not number.strip():
            raise ValidationError(
                "change_request_numbers must contain business numbers"
            )
        request = change_request_repo.get_by_number(project_id, number.strip())
        if request is None:
            raise NotFoundError(f"change request {number} not found")
        if request.id in seen:
            raise ValidationError(f"duplicate change request {number}")
        seen.add(request.id)
        if request.status not in ("approved", "applied"):
            raise ConflictError(
                f"{request.change_request_number} must be approved before task assignment"
            )
        if request.task_id is not None and request.task_id != task.id:
            raise ConflictError(
                f"{request.change_request_number} is already assigned to another task"
            )
        requests.append(request)

    if task.design_case_id is not None or task.source_kind != "change_request":
        raise ConflictError(
            "a design task cannot also own change requests; create a separate "
            "change-request task"
        )
    with database_proxy.atomic():
        task.source_kind = "change_request"
        development_task_repo.save(task)
        for request in requests:
            request.task = task
            change_request_repo.save(request)
    return task, requests


def create_and_assign(project_id, data):
    """Atomically create one source task and attach all requested sources.

    A task with no source is never committed.  The caller supplies business
    numbers; only this service resolves them to internal ids.
    """
    source_kind = data.get("source_kind")
    if source_kind != "change_request":
        raise ValidationError("source_kind must be change_request")
    # CRs deliberately do not infer ownership from target_id: a request may
    # touch several objects or target a new object.  The caller must name the
    # one Feature whose execution gate owns this task.
    feature_number = data.get("feature_number")
    if not isinstance(feature_number, str) or not feature_number.strip():
        raise ValidationError("feature_number is required for a source task")
    feature = feature_repo.get_by_number(project_id, feature_number.strip())
    if feature is None:
        raise NotFoundError(f"feature {feature_number} not found")
    title = data.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValidationError("title is required for a source task")
    for field, allowed in (
        ("task_type", TASK_TYPES),
        ("priority", TASK_PRIORITIES),
        ("complexity", TASK_COMPLEXITIES),
        ("risk_level", TASK_RISK_LEVELS),
    ):
        value = data.get(field)
        if value is not None and value not in allowed:
            raise ValidationError(f"invalid {field}: {value}")
    description = data.get("goal") or data.get("description") or ""
    if not isinstance(description, str) or not description.strip():
        raise ValidationError("goal is required for a source task")
    acceptance = data.get("acceptance_criteria") or ""
    if not isinstance(acceptance, str) or not acceptance.strip():
        raise ValidationError("acceptance_criteria is required for a source task")
    change_numbers = data.get("change_request_numbers") or []
    if data.get("item_numbers"):
        raise ValidationError("Integration findings belong to the stage run, not a TASK")
    if not change_numbers:
        raise ValidationError("change_request_numbers must not be empty for change-request tasks")

    with database_proxy.atomic():
        task = development_task_service.create_source_task(
            project_id,
            feature_id=feature.id,
            title=title.strip(),
            source_kind=source_kind,
            task_type=data.get("task_type") or "feature",
            priority=data.get("priority") or "normal",
            complexity=data.get("complexity") or "medium",
            risk_level=data.get("risk_level") or "low",
            description=description.strip(),
            acceptance_criteria=acceptance.strip(),
            constraints=data.get("constraints") or "",
            non_goals=data.get("non_goals") or "",
        )
        task, sources = assign_change_requests(
            project_id, task.task_number, change_numbers
        )
    return task, sources


def list_sources(task):
    """Return public source references without exposing FK ids."""
    requests = change_request_repo.list_by_task(task.id)
    return {
        "change_requests": [request.to_dict() for request in requests],
    }
