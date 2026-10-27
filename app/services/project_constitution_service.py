"""Project constitution business logic.

A constitution is a singleton per project (1:1), harvested from the confirmed
INPUT-* set and read by every downstream stage. It shares the 3-state lifecycle
(``draft ⇄ needs_clarification → confirmed``) but is NOT
decomposed into items — its content lives in six governance fields, snapshotted
into version history on every content change (误写可恢复).
"""
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.time import utcnow
from app.models.project_constitution import _CONTENT_FIELDS
from app.repositories import (
    project_constitution_repo as _repo,
    project_constitution_version_repo as _version_repo,
    project_repo,
)
from app.services import lifecycle

_LABEL = "constitution"


def _ensure_project(project_id):
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")


def get_for_project(project_id):
    """Return the project's constitution, or None if it has none yet."""
    _ensure_project(project_id)
    return _repo.get_for_project(project_id)


def ensure_for_project(project_id):
    """Return the project's constitution, creating an empty draft if absent."""
    _ensure_project(project_id)
    constitution = _repo.get_for_project(project_id)
    if constitution is None:
        constitution = _repo.create(project_id, status=lifecycle.DRAFT)
        _create_version(constitution, change_type="create")
    return constitution


def require_for_project(project_id):
    constitution = get_for_project(project_id)
    if constitution is None:
        raise NotFoundError(f"project {project_id} has no constitution")
    return constitution


def update(project_id, data):
    """Upsert the singleton and edit it. Writable here: the six governance
    fields plus a status move among editable states (draft ⇄
    needs_clarification). confirm / reopen are dedicated actions, not content
    edits. Mirrors design_case_service.update."""
    constitution = ensure_for_project(project_id)
    lifecycle.ensure_editable(constitution.status, _LABEL)
    before = _version_payload(constitution)
    if "status" in data:
        target = data["status"]
        lifecycle.validate_patch_transition(constitution.status, target, _LABEL)
        constitution.status = target
    for field in _CONTENT_FIELDS:
        if field in data:
            value = data[field]
            if value is not None and not isinstance(value, str):
                raise ValidationError(f"{field} must be a string")
            setattr(constitution, field, value or "")
    _repo.save(constitution)
    if _version_payload(constitution) != before:
        _create_version(constitution, change_type="update")
    return constitution


def confirm(project_id):
    constitution = require_for_project(project_id)
    if not lifecycle.is_editable(constitution.status):
        raise ConflictError(
            f"cannot confirm constitution in state '{constitution.status}'"
        )
    if not any((getattr(constitution, field) or "").strip() for field in _CONTENT_FIELDS):
        raise ValidationError("cannot confirm an empty constitution")
    constitution.status = lifecycle.CONFIRMED
    constitution.confirmed_at = utcnow()
    return _repo.save(constitution)


def reopen(project_id):
    """confirmed -> draft. The only way to edit a locked constitution.

    Editing the constitution does NOT cascade to designs. The constitution is a
    cross-cutting background constraint (fixed stack, quality bar, boundaries),
    not an upstream artifact that designs are derived from — a design is made
    UNDER it, it does not COPY it. So revising the governance is not a reason to
    re-review every existing design: the new agreement takes effect for all
    subsequent design/development, and honouring it happens at build time, not
    by flagging settled work. (A rare fundamental change — the whole stack
    swapped, boundaries redrawn — is handled by a human re-reviewing, not by
    a standing cascade that turns every routine edit into project-wide noise.)
    The constitution therefore sits OUTSIDE the feature->design->task cascade,
    like requirements and the glossary."""
    constitution = require_for_project(project_id)
    if not lifecycle.is_locked(constitution.status):
        raise ConflictError(
            f"cannot reopen constitution in state '{constitution.status}'"
        )
    constitution.status = lifecycle.DRAFT
    constitution.confirmed_at = None
    return _repo.save(constitution)


def list_versions(project_id):
    constitution = require_for_project(project_id)
    return _version_repo.list_by_constitution(constitution.id)


def restore_version(project_id, version_number):
    constitution = require_for_project(project_id)
    lifecycle.ensure_editable(constitution.status, _LABEL)
    version = _version_repo.get(constitution.id, version_number)
    if version is None:
        raise NotFoundError(
            f"constitution version {version_number} not found"
        )
    for field in _CONTENT_FIELDS:
        setattr(constitution, field, getattr(version, field))
    _repo.save(constitution)
    _create_version(
        constitution,
        change_type="restore",
        restored_from_version_number=version.version_number,
    )
    return constitution


def current_version_number(project_id):
    constitution = require_for_project(project_id)
    latest = _version_repo.latest_for_constitution(constitution.id)
    return latest.version_number if latest is not None else None


def _version_payload(constitution):
    return {field: getattr(constitution, field) for field in _CONTENT_FIELDS}


def _create_version(constitution, *, change_type, restored_from_version_number=None):
    latest = _version_repo.latest_for_constitution(constitution.id)
    next_number = 1 if latest is None else latest.version_number + 1
    return _version_repo.create(
        project_constitution=constitution.id,
        version_number=next_number,
        change_type=change_type,
        restored_from_version_number=restored_from_version_number,
        **_version_payload(constitution),
    )
