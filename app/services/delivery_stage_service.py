"""Lifecycle for one operator's project-wide delivery round."""
import json
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.time import utcnow
from app.models.delivery_stage_run import STAGES
from app.repositories import delivery_item_repo, delivery_stage_run_repo, project_repo
from app.services import numbering

_KINDS = {"integration": ("assembly", "seam", "duplicate"), "system": ("journey", "nfr")}


def _checked_rerun(value, code_revision):
    try:
        entries = json.loads(value)
    except (TypeError, ValueError):
        raise ValidationError("integration_rerun_evidence must be a JSON list of exact checks")
    if not isinstance(entries, list) or not entries:
        raise ValidationError("integration_rerun_evidence must contain affected Integration checks")
    for entry in entries:
        if not isinstance(entry, dict) or not all(
            isinstance(entry.get(name), str) and entry[name].strip()
            for name in ("command", "output", "code_revision")
        ) or entry.get("exit_code") != 0 or entry.get("code_revision") != code_revision:
            raise ValidationError(
                "each Integration rerun needs command, output, exit_code=0 and the current code_revision"
            )


def _checked_integration_qa(value, code_revision, covered_item_numbers):
    """Require one independent assembled-run QA result for a changed run."""
    try:
        report = json.loads(value)
    except (TypeError, ValueError):
        raise ValidationError("Integration evidence must be a JSON qa report")
    if not isinstance(report, dict) or report.get("role") != "qa":
        raise ValidationError("Integration evidence requires role=qa")
    if not all(isinstance(report.get(key), str) and report[key].strip()
               for key in ("command", "output", "code_revision")):
        raise ValidationError("Integration qa needs command, raw output and code_revision")
    if report.get("exit_code") != 0 or report["code_revision"] != code_revision:
        raise ValidationError("Integration qa must pass on the current code_revision")
    numbers = report.get("item_numbers")
    if (not isinstance(numbers, list) or not all(isinstance(number, str) for number in numbers)
            or len(numbers) != len(set(numbers))
            or set(numbers) != set(covered_item_numbers)):
        raise ValidationError("Integration qa item_numbers must cover every non-waived INTEG-* item exactly once")


def _text(data, name, required=False):
    value = data.get(name, "")
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise ValidationError(f"{name} must be a string")
    value = value.strip()
    if required and not value:
        raise ValidationError(f"{name} is required")
    return value


def _run_field(data, name, required=False):
    value = data.get(name)
    if name in ("evidence", "integration_rerun_evidence") and isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return _text(data, name, required)


def start(project_id, stage, data):
    from app.db.base import database_proxy
    with database_proxy.atomic():
        return _start(project_id, stage, data)


def _start(project_id, stage, data):
    if stage not in STAGES:
        raise ValidationError("stage must be integration or system")
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    if delivery_stage_run_repo.active_any(project_id):
        raise ConflictError("a project can have only one active delivery stage run")
    from app.services import delivery_gate
    gate = delivery_gate.gate_status(project_id)
    if stage == "integration" and not gate["integration_owed"]:
        raise ConflictError("Integration cannot start until current capability work makes it owed")
    if stage == "system" and not gate["system_owed"]:
        raise ConflictError("System cannot start until a current Integration round is complete")
    from app.services.integration_inventory_validator import completion_basis_fingerprint
    run = delivery_stage_run_repo.create(project_id, stage=stage, branch=_text(data, "branch"),
        code_revision=_text(data, "code_revision", True), design_revision=_text(data, "design_revision", True),
        basis_fingerprint=completion_basis_fingerprint(project_id))
    run = numbering.assign_number(run, "RUN", "run_number", project_id=project_id)
    from app.services import work_owner_service
    work_owner_service.apply(run, data, default_to_actor=True)
    return run


def update(project_id, run_number, data):
    run = delivery_stage_run_repo.get_by_number(project_id, run_number)
    if run is None:
        raise NotFoundError(f"delivery stage run {run_number} not found")
    if run.status in ("closed", "abandoned"):
        raise ConflictError("a finished stage run is immutable; start a new round")
    for name in ("branch", "code_revision", "design_revision", "merge_reference", "evidence", "blocked_reason", "integration_rerun_evidence"):
        if name in data:
            setattr(run, name, _run_field(data, name, name in ("code_revision", "design_revision")))
    if "blocked_reason" in data:
        run.status = "blocked" if run.blocked_reason else "active"
    return delivery_stage_run_repo.save(run)


def close(project_id, run_number, data):
    run = delivery_stage_run_repo.get_by_number(project_id, run_number)
    if run is None:
        raise NotFoundError(f"delivery stage run {run_number} not found")
    if run.status in ("closed", "abandoned"):
        raise ConflictError("a finished stage run is immutable")
    # Validate a candidate before persisting anything: a rejected close must
    # not accidentally record a merge reference or unblock a run.
    candidate = {name: _run_field(data, name, name in ("code_revision", "design_revision")) for name in ("branch", "code_revision", "design_revision", "merge_reference", "evidence", "blocked_reason", "integration_rerun_evidence") if name in data}
    branch = candidate.get("branch", run.branch)
    merge_reference = candidate.get("merge_reference", run.merge_reference)
    code_revision = candidate.get("code_revision", run.code_revision)
    blocked_reason = candidate.get("blocked_reason", run.blocked_reason)
    if blocked_reason:
        raise ConflictError("cannot close a stage run while required resources are blocked")
    from app.services.integration_inventory_validator import completion_basis_fingerprint
    if run.basis_fingerprint != completion_basis_fingerprint(project_id):
        raise ConflictError("capability/design basis changed during this stage run; review affected obligations before closing")
    items = delivery_item_repo.list_by_project(run.project_id, kinds=_KINDS[run.stage])
    unsettled = [item.item_number for item in items if item.status == "open"]
    blocked = [item.item_number for item in items if item.blocked_reason.strip()]
    stale = [item.item_number for item in items if item.status == "covered" and item.verified_revision != code_revision]
    if unsettled or blocked or stale:
        raise ConflictError("cannot close stage run: " + "; ".join(
            (["open items: " + ", ".join(unsettled)] if unsettled else []) +
            (["items awaiting required user resources: " + ", ".join(blocked)] if blocked else []) +
            (["evidence must be rerun for current code revision: " + ", ".join(stale)] if stale else [])))
    if run.stage == "integration" and branch:
        if not items:
            raise ConflictError("a changed Integration run requires recorded INTEG-* findings")
        _checked_integration_qa(
            candidate.get("evidence", run.evidence), code_revision,
            [item.item_number for item in items if item.status == "covered"],
        )
    if branch and not merge_reference:
        raise ConflictError("a stage run with repository changes requires one final merge_reference")
    if run.stage == "integration":
        from app.services import integration_inventory_validator
        audit = integration_inventory_validator.completion_status(run.project_id)
        if not audit["complete"]:
            raise ConflictError("cannot close Integration without a current complete inventory audit: " + "; ".join(audit["errors"]))
    else:
        from app.services import system_inventory_validator
        audit = system_inventory_validator.status(run.project_id)
        if not audit["complete"]:
            raise ConflictError("cannot close System without a current complete inventory audit: " + "; ".join(audit["errors"]))
        if branch:
            _checked_rerun(candidate.get("integration_rerun_evidence", run.integration_rerun_evidence), code_revision)
    for name, value in candidate.items(): setattr(run, name, value)
    run.status, run.closed_at = "closed", utcnow()
    return delivery_stage_run_repo.save(run)


def abandon(project_id, run_number, data):
    run = delivery_stage_run_repo.get_by_number(project_id, run_number)
    if run is None:
        raise NotFoundError(f"delivery stage run {run_number} not found")
    if run.status in ("closed", "abandoned"):
        raise ConflictError("a finished stage run is immutable")
    run.abandon_reason = _text(data, "reason", True)
    run.status = "abandoned"
    return delivery_stage_run_repo.save(run)
