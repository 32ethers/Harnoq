"""DesignCase business logic.

Status follows the shared 4-state lifecycle (see app/services/lifecycle.py):
``draft ⇄ needs_clarification → confirmed``. Content edits
require an editable state; ``confirm`` / ``reopen`` cross
the editable/locked boundary.

A DesignCase attaches only to a FEATURE (``feature_id``), never to an INPUT-*
directly — see app/models/design_case.py for why. Multiple designs (for
different features) may all draw on the same INPUT-* material; nothing here
tracks or gates that.
"""
import json

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.time import utcnow
from app.models.design_case import (
    DESIGN_CASE_VERSIONED_FIELDS,
    IMPACT_AREAS,
    REQUIRED_FOR_CONFIRM,
)
from app.models.task_enums import (
    TASK_COMPLEXITIES,
    TASK_PRIORITIES,
    TASK_RISK_LEVELS,
    TASK_TYPES,
)
from app.repositories import (
    design_case_repo,
    design_case_version_repo,
    feature_repo,
    project_repo,
)
from app.services import (
    development_task_service,
    lifecycle,
    nfr_baseline,
    numbering,
    project_constitution_service,
    soft_delete as _softdel,
    suspect_service,
)

_LABEL = "design case"

_TEXT_FIELDS = (
    "problem_statement",
    "mined_requirements",
    "verifies_nfr",
    "design_scope",
    "non_goals",
    "assumptions",
    "constraints",
    "selected_solution",
    "selection_reason",
    "risks",
    "test_strategy",
    "acceptance_criteria",
    "reviewer",
)


def _validate_task_enums(data):
    if "task_type" in data and data["task_type"] not in TASK_TYPES:
        raise ValidationError(f"invalid task_type: {data['task_type']}")
    if "priority" in data and data["priority"] not in TASK_PRIORITIES:
        raise ValidationError(f"invalid priority: {data['priority']}")
    if "complexity" in data and data["complexity"] not in TASK_COMPLEXITIES:
        raise ValidationError(f"invalid complexity: {data['complexity']}")
    if "risk_level" in data and data["risk_level"] not in TASK_RISK_LEVELS:
        raise ValidationError(f"invalid risk_level: {data['risk_level']}")


def _ensure_confirmed_for_task(design_case):
    """A task may only be created directly under a confirmed design.

    DESIGN→TASK is strictly one-to-one. New scope discovered after the design
    task exists must enter through an approved CR source-backed TASK, never
    through a second task under this DESIGN. Delivery checks stay in stage runs.
    """
    if design_case.status != lifecycle.CONFIRMED:
        raise ConflictError("must confirm the design before creating a task")


def _require(data, key):
    value = data.get(key)
    if value is None or (isinstance(value, str) and not value.strip()):
        raise ValidationError(f"{key} is required")
    return value


def create(project_id, data):
    """Create a design case for ``project_id``. ``data["feature_id"]``
    (or ``feature_number`` resolved by the caller) is required — a design
    always belongs to exactly one FEATURE block; it does not itself reference
    any INPUT-*."""
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    if "status" in data:
        raise ValidationError(
            "status is server-managed; omit it (new designs start at 'draft')"
        )
    feature_id = _normalize_feature_id(project_id, data.get("feature_id"))
    if feature_id is None:
        raise ValidationError("feature_id (or feature_number) is required")

    design_case = design_case_repo.create(
        project_id=project_id,
        feature=feature_id,
        reviewer=data.get("reviewer"),
    )
    design_case = numbering.assign_number(
        design_case, "DESIGN", "design_number", project_id=project_id
    )
    _create_version(design_case, change_type="create")
    return design_case


def get(design_case_id):
    design_case = design_case_repo.get(design_case_id)
    if design_case is None:
        raise NotFoundError(f"design case {design_case_id} not found")
    return design_case


def get_by_number(project_id, design_number):
    design_case = design_case_repo.get_by_number(project_id, design_number)
    if design_case is None:
        raise NotFoundError(f"design case {design_number} not found")
    return design_case


def get_deleted_by_number(project_id, design_number):
    """Like get_by_number but for restoring: the design is expected to
    currently be in the recycle bin (deleted_at set)."""
    design_case = design_case_repo.get_deleted_by_number(project_id, design_number)
    if design_case is None:
        raise NotFoundError(f"design case {design_number} not in recycle bin")
    return design_case


def list_by_project(project_id):
    return design_case_repo.list_by_project(project_id)


def list_by_feature(feature_id):
    return design_case_repo.list_by_feature(feature_id)


def list_deleted(project_id):
    """Soft-deleted design cases for this project (its own recycle bin)."""
    return design_case_repo.list_deleted_by_project(project_id)


def update(design_case_id, data):
    design_case = get(design_case_id)
    lifecycle.ensure_editable(design_case.status, _LABEL)
    before = _version_payload(design_case)
    if "feature_id" in data:
        design_case.feature = _normalize_feature_id(design_case.project_id, data.get("feature_id"))
    if "status" in data:
        target = data["status"]
        lifecycle.validate_patch_transition(design_case.status, target, _LABEL)
        design_case.status = target
    for field in _TEXT_FIELDS:
        if field in data:
            setattr(design_case, field, data[field] if data[field] is not None else "")
    if "impact_areas" in data:
        if not isinstance(data["impact_areas"], list):
            raise ValidationError("impact_areas must be a list")
        invalid = [a for a in data["impact_areas"] if a not in IMPACT_AREAS]
        if invalid:
            raise ValidationError(
                f"invalid impact_areas: {invalid}; "
                f"valid values: {', '.join(IMPACT_AREAS)}"
            )
        design_case.impact_areas = json.dumps(data["impact_areas"])
    if "rejected_solutions" in data:
        if not isinstance(data["rejected_solutions"], list):
            raise ValidationError("rejected_solutions must be a list")
        design_case.rejected_solutions = json.dumps(data["rejected_solutions"])
    if "open_questions" in data:
        if not isinstance(data["open_questions"], list):
            raise ValidationError("open_questions must be a list")
        design_case.open_questions = json.dumps(data["open_questions"])
    design_case = design_case_repo.save(design_case)
    if _version_payload(design_case) != before:
        _create_version(design_case, change_type="update")
    return design_case


def _constitution_has_nfr_commitments(project_id):
    """Whether this project's constitution promises anything provable.

    ``verifies_nfr`` is required to confirm only when there is a question to
    answer. A project whose baseline states no commitment — none at all, or
    only directions (see app/services/nfr_baseline.py) — would otherwise have
    every design write a ritual "none", and a required field that is always
    answered the same way stops being read.
    """
    constitution = project_constitution_service.get_for_project(project_id)
    return bool(
        constitution and nfr_baseline.has_commitment(constitution.nfr_baseline)
    )


def confirm(design_case_id):
    design_case = get(design_case_id)
    if not lifecycle.is_editable(design_case.status):
        raise ConflictError(
            f"cannot confirm design in state '{design_case.status}'"
        )
    for field in REQUIRED_FOR_CONFIRM:
        if not (getattr(design_case, field) or "").strip():
            raise ValidationError(
                f"design case missing required field for confirmation: {field}"
            )
    # Conditional, so it is not in REQUIRED_FOR_CONFIRM. An NFR row the
    # constitution states and no design claims is a promise with no owner, and
    # the coverage view can only report that if every design was asked. Asking
    # at confirmation is what makes the answer exist: a live project reached
    # thirteen confirmed designs with this field empty in all thirteen, not
    # because none verified an NFR but because nothing ever required a reply.
    # "None" is a valid reply — see nfr_baseline._NO_CLAIM — blank is not,
    # because blank cannot be told apart from unasked.
    if not (
        design_case.verifies_nfr or ""
    ).strip() and _constitution_has_nfr_commitments(design_case.project_id):
        raise ValidationError(
            "design case missing required field for confirmation: verifies_nfr"
            " — name the constitution nfr_baseline commitments this design "
            "proves, quoting each metric as the constitution writes it, or "
            "write 无 / none if it proves none of them"
        )
    design_case.status = lifecycle.CONFIRMED
    design_case.confirmed_at = utcnow()
    saved = design_case_repo.save(design_case)
    # Confirming is the moment the design commits to a specific reading of its
    # upstream, so it is the moment worth writing down (see docs/cn/design-evolution.md#changes).
    suspect_service.record_design_basis(saved)
    return saved


def reopen(design_case_id):
    """confirmed -> draft. The only way to edit a locked design."""
    design_case = get(design_case_id)
    if not lifecycle.is_locked(design_case.status):
        raise ConflictError(f"cannot reopen design in state '{design_case.status}'")
    design_case.status = lifecycle.DRAFT
    design_case.confirmed_at = None
    saved = design_case_repo.save(design_case)
    # Back to unsettled: there is no longer a confirmed reading to be stale
    # against, and re-confirming writes a fresh basis.
    suspect_service.forget(suspect_service.DESIGN_CASE, saved.id)
    return saved


def dict_ctx(project_id):
    """Pre-fetch, in 1 query, the feature map ``DesignCase.to_dict(ctx=...)``
    needs so a LIST of designs serializes without 1 query per design (an N+1
    in list rendering)."""
    from app.models.feature import Feature

    feature_by_id = {
        row.id: (row.feature_number, row.name)
        for row in Feature.select(
            Feature.id, Feature.feature_number, Feature.name
        ).where(Feature.project_id == project_id)
    }
    return {"feature_by_id": feature_by_id}


def create_task(design_case_id, data):
    """Create one claimable DevelopmentTask directly under a confirmed design.

    This is the entire "decompose into work" step now (see
    docs/cn/design-evolution.md#objects): no intermediate draft or batch generation.
    A live DESIGN has exactly one design-backed TASK;
    additional work must use an explicit source-backed TASK.
    """
    design_case = get(design_case_id)
    _ensure_confirmed_for_task(design_case)
    title = _require(data, "title")
    task_type = _require(data, "task_type")
    _validate_task_enums(data)
    return development_task_service.create_task(
        design_case,
        title=title.strip(),
        task_type=task_type,
        priority=data.get("priority", "normal"),
        complexity=data.get("complexity", "medium"),
        risk_level=data.get("risk_level", "low"),
        description=data.get("goal") or data.get("description") or "",
        acceptance_criteria=data.get("acceptance_criteria") or "",
        constraints=data.get("constraints") or "",
        non_goals=data.get("non_goals") or "",
    )


def reject(design_case_id):
    """Back-compat: reject is dissolved into reopen (see app/services/lifecycle.py).
    Kept as a thin alias so existing callers (HTTP route, skills) keep working
    until they're renamed in the reopen/delete UI + tooling pass."""
    return reopen(design_case_id)


def list_versions(design_case_id):
    get(design_case_id)
    return design_case_version_repo.list_by_design(design_case_id)


def restore_version(design_case_id, version_number):
    design_case = get(design_case_id)
    # Restoring rewrites content, so it is an edit: forbidden once the design
    # is locked (confirmed) — reopen first, mirroring update().
    lifecycle.ensure_editable(design_case.status, _LABEL)
    version = design_case_version_repo.get(design_case_id, version_number)
    if version is None:
        raise NotFoundError(
            f"design case {design_case_id} version {version_number} not found"
        )
    for field in DESIGN_CASE_VERSIONED_FIELDS:
        setattr(design_case, field, getattr(version, field))
    design_case = design_case_repo.save(design_case)
    _create_version(
        design_case,
        change_type="restore",
        restored_from_version_number=version.version_number,
    )
    return design_case


def current_version_number(design_case_id):
    latest = design_case_version_repo.latest_for_design(design_case_id)
    return latest.version_number if latest is not None else None


def _version_payload(design_case):
    return {
        field: getattr(design_case, field) for field in DESIGN_CASE_VERSIONED_FIELDS
    }


def _create_version(design_case, *, change_type, restored_from_version_number=None):
    latest = design_case_version_repo.latest_for_design(design_case.id)
    next_number = 1 if latest is None else latest.version_number + 1
    return design_case_version_repo.create(
        design_case=design_case.id,
        version_number=next_number,
        change_type=change_type,
        restored_from_version_number=restored_from_version_number,
        **_version_payload(design_case),
    )


def delete(design_case_id):
    return _softdel.delete(design_case_repo, design_case_id, _LABEL)


def restore(design_case_id):
    return _softdel.restore(design_case_repo, design_case_id, _LABEL)


def _normalize_feature_id(project_id, raw_feature_id):
    if raw_feature_id is None:
        return None
    if not isinstance(raw_feature_id, int):
        raise ValidationError(f"invalid feature_id: {raw_feature_id}")
    feature = feature_repo.get(raw_feature_id)
    if feature is None or feature.project_id != project_id:
        raise ValidationError(f"invalid feature_id: {raw_feature_id}")
    return feature.id


def purge(design_case_id):
    suspect_service.forget(suspect_service.DESIGN_CASE, design_case_id)
    _softdel.purge(design_case_repo, design_case_id, _LABEL)
