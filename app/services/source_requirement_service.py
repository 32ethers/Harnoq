"""SourceRequirement business logic.

Shares the 3-state lifecycle (see app/services/lifecycle.py): draft ⇄
needs_clarification → confirmed. Unlike DesignCase, confirm()
has NO required-field checklist — it is a bare status flip, so raw_content
stays free text and is never structurally validated. The gate exists purely
so the pipeline knows "the requirement discussion is done" (see
app/api/skills.py::_current_stage, which requires every INPUT-* in a project
confirmed before advancing past the requirement stage).
"""
import json

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.base import database_proxy
from app.models.source_requirement import SOURCE_REQUIREMENT_TYPES
from app.repositories import (
    project_repo,
    source_requirement_repo,
    source_requirement_version_repo,
)
from app.services import lifecycle, numbering, work_owner_service, soft_delete as _softdel

_LABEL = "source requirement"


def _require(data, key):
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{key} is required")
    return value


def _ensure_project(project_id):
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")


def create(project_id, data):
    with database_proxy.atomic():
        return _create(project_id, data)


def _create(project_id, data):
    _ensure_project(project_id)
    if "status" in data:
        raise ValidationError(
            "status is server-managed; omit it (new source requirements start at 'draft')"
        )
    title = _require(data, "title")
    source_type = _require(data, "source_type")
    if source_type not in SOURCE_REQUIREMENT_TYPES:
        raise ValidationError(
            f"invalid source_type: {source_type}; "
            f"valid values: {', '.join(SOURCE_REQUIREMENT_TYPES)}"
        )
    attachments = data.get("attachments")
    if attachments is None:
        attachments = []
    if not isinstance(attachments, list):
        raise ValidationError("attachments must be a list")

    source_requirement = source_requirement_repo.create(
        project_id=project_id,
        title=title,
        source_type=source_type,
        raw_content=data.get("raw_content") or "",
        attachments_json=json.dumps(attachments),
        source=data.get("source") or "",
        submitted_by=data.get("submitted_by"),
    )
    source_requirement = numbering.assign_number(
        source_requirement, "INPUT", "source_requirement_number", project_id=project_id
    )
    work_owner_service.apply(source_requirement, data, default_to_actor=True)
    _create_version(source_requirement, change_type="create")
    return source_requirement


def get(source_requirement_id):
    source_requirement = source_requirement_repo.get(source_requirement_id)
    if source_requirement is None:
        raise NotFoundError(f"source requirement {source_requirement_id} not found")
    return source_requirement


def get_by_number(project_id, source_requirement_number):
    source_requirement = source_requirement_repo.get_by_number(project_id, source_requirement_number)
    if source_requirement is None:
        raise NotFoundError(f"source requirement {source_requirement_number} not found")
    return source_requirement


def list_by_project(project_id):
    _ensure_project(project_id)
    return work_owner_service.preload(source_requirement_repo.list_by_project(project_id))


def list_deleted(project_id):
    """Soft-deleted source requirements for this project (its own recycle bin)."""
    return source_requirement_repo.list_deleted_by_project(project_id)


def update(source_requirement_id, data):
    with database_proxy.atomic():
        return _update(source_requirement_id, data)


def _update(source_requirement_id, data):
    source_requirement = get(source_requirement_id)
    lifecycle.ensure_editable(source_requirement.status, _LABEL)
    before = _version_payload(source_requirement)
    if "status" in data:
        target = data["status"]
        lifecycle.validate_patch_transition(source_requirement.status, target, _LABEL)
        source_requirement.status = target
    if "title" in data:
        _require(data, "title")
        source_requirement.title = data["title"].strip()
    if "source_type" in data:
        if data["source_type"] not in SOURCE_REQUIREMENT_TYPES:
            raise ValidationError(
                f"invalid source_type: {data['source_type']}; "
                f"valid values: {', '.join(SOURCE_REQUIREMENT_TYPES)}"
            )
        source_requirement.source_type = data["source_type"]
    if "raw_content" in data:
        source_requirement.raw_content = data["raw_content"] or ""
    if "source" in data:
        source_requirement.source = data["source"] or ""
    if "submitted_by" in data:
        source_requirement.submitted_by = data["submitted_by"]
    if "attachments" in data:
        if not isinstance(data["attachments"], list):
            raise ValidationError("attachments must be a list")
        source_requirement.attachments = json.dumps(data["attachments"])
    source_requirement = source_requirement_repo.save(source_requirement)
    work_owner_service.apply(source_requirement, data)
    if _version_payload(source_requirement) != before:
        _create_version(source_requirement, change_type="update")
    return source_requirement


def confirm(source_requirement_id):
    """{draft, needs_clarification} -> confirmed. Bare status flip — no
    required-field checklist; raw_content stays free text, never validated."""
    source_requirement = get(source_requirement_id)
    if not lifecycle.is_editable(source_requirement.status):
        raise ConflictError(
            f"cannot confirm source requirement in state '{source_requirement.status}'"
        )
    source_requirement.status = lifecycle.CONFIRMED
    return source_requirement_repo.save(source_requirement)


def reopen(source_requirement_id):
    """confirmed -> draft. The only way to edit a locked INPUT."""
    source_requirement = get(source_requirement_id)
    if not lifecycle.is_locked(source_requirement.status):
        raise ConflictError(
            f"cannot reopen source requirement in state '{source_requirement.status}'"
        )
    source_requirement.status = lifecycle.DRAFT
    return source_requirement_repo.save(source_requirement)


def list_versions(source_requirement_id):
    get(source_requirement_id)
    return source_requirement_version_repo.list_by_input(source_requirement_id)


def restore_version(source_requirement_id, version_number):
    source_requirement = get(source_requirement_id)
    # Restoring rewrites content, so it is an edit: forbidden once confirmed
    # — reopen first, mirroring update().
    lifecycle.ensure_editable(source_requirement.status, _LABEL)
    version = source_requirement_version_repo.get(source_requirement_id, version_number)
    if version is None:
        raise NotFoundError(
            f"source requirement {source_requirement_id} version {version_number} not found"
        )
    source_requirement.title = version.title
    source_requirement.source_type = version.source_type
    source_requirement.raw_content = version.raw_content
    source_requirement.attachments = version.attachments
    source_requirement.source = version.source
    source_requirement.submitted_by = version.submitted_by
    source_requirement = source_requirement_repo.save(source_requirement)
    _create_version(
        source_requirement,
        change_type="restore",
        restored_from_version_number=version.version_number,
    )
    return source_requirement


def delete(source_requirement_id):
    return _softdel.delete(source_requirement_repo, source_requirement_id, _LABEL)


def restore(source_requirement_id):
    return _softdel.restore(source_requirement_repo, source_requirement_id, _LABEL)


def purge(source_requirement_id):
    _softdel.purge(source_requirement_repo, source_requirement_id, _LABEL)


def current_version_number(source_requirement_id):
    latest = source_requirement_version_repo.latest_for_input(source_requirement_id)
    return latest.version_number if latest is not None else None


def _version_payload(source_requirement):
    return {
        "title": source_requirement.title,
        "source_type": source_requirement.source_type,
        "raw_content": source_requirement.raw_content or "",
        "attachments": source_requirement.attachments or "[]",
        "source": source_requirement.source or "",
        "submitted_by": source_requirement.submitted_by,
    }


def _create_version(source_requirement, *, change_type, restored_from_version_number=None):
    latest = source_requirement_version_repo.latest_for_input(source_requirement.id)
    next_number = 1 if latest is None else latest.version_number + 1
    payload = _version_payload(source_requirement)
    return source_requirement_version_repo.create(
        source_requirement=source_requirement.id,
        version_number=next_number,
        change_type=change_type,
        restored_from_version_number=restored_from_version_number,
        **payload,
    )
