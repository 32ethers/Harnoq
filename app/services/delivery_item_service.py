"""Business rules for DeliveryItem — see app/models/delivery_item.py for what
this entity is and why it exists.

Two rules are enforced here, not just suggested in prompt text, because a
status change with no reason attached is indistinguishable from having
forgotten about the item:

* ``status="waived"`` requires a non-empty ``waive_reason``.
* ``status="covered"`` requires exact executable evidence and the code
  revision that was actually verified.

Both are checked on create AND on update, from whichever value (existing or
incoming) ends up as the final status — so waiving an item in one call and
clearing its reason in a later call is refused the same way a bare waive is.
"""

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.base import database_proxy
from app.models.delivery_item import KINDS, STATUSES
from app.repositories import (
    delivery_item_repo,
    delivery_stage_run_repo,
    project_repo,
)
from app.services import numbering, work_owner_service
from app.services import soft_delete as _softdel

_LABEL = "delivery item"

# Only these kinds carry a driver (see model docstring); anything else must
# leave it null rather than storing a value nothing will ever read.
_DRIVER_KINDS = ("journey", "nfr")
_INTEGRATION_KINDS = frozenset({"assembly", "seam", "duplicate"})


def _ensure_project(project_id):
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")


def _required_text(data, field):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} is required")
    return value.strip()


def _optional_text(data, field):
    value = data.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    return value


def _check_kind(kind):
    if kind not in KINDS:
        raise ValidationError(f"kind must be one of {list(KINDS)}, got {kind!r}")
    return kind


def _check_status(status):
    if status not in STATUSES:
        raise ValidationError(f"status must be one of {list(STATUSES)}, got {status!r}")
    return status


def _finding_key(kind, subject, source):
    def normalize(value):
        return " ".join((value or "").split()).casefold()

    return kind, normalize(subject), normalize(source)


def _check_driver(kind, driver):
    if driver is None:
        return None
    if not isinstance(driver, str):
        raise ValidationError("driver must be a string")
    driver = driver.strip()
    if not driver:
        return None
    if kind not in _DRIVER_KINDS:
        raise ValidationError(
            f"driver only applies to kind in {list(_DRIVER_KINDS)}, not {kind!r}"
        )
    return driver


def _require_driver(kind, driver):
    """Journey/NFR rows without a driving method cannot enter a new audit.

    Historical empty rows remain readable, but a current create or update must
    make the final state explicit; this also catches kind changes into SYS.
    """
    if kind in _DRIVER_KINDS and not (isinstance(driver, str) and driver.strip()):
        raise ValidationError(f"driver is required for kind={kind!r}")


def _check_status_evidence(status, covered_by, waive_reason):
    """The two rules the model docstring promises: a status with no evidence
    of the decision behind it is refused, not silently accepted."""
    if status == "waived" and not (waive_reason or "").strip():
        raise ValidationError(
            "waive_reason is required when status is 'waived': say who "
            "decided not to do it and why — an unexplained waive is "
            "indistinguishable from having forgotten this item"
        )
    if status == "covered" and not (covered_by or "").strip():
        raise ValidationError(
            "covered_by is required when status is 'covered': name the "
            "the exact command and result that actually discharges it"
        )


def _check_coverage(status, evidence, revision):
    if status == "covered" and (not evidence.strip() or not revision.strip()):
        raise ValidationError(
            "covered requires exact executable evidence and verified_revision; "
            "record the command, result and the code revision that was run"
        )


def create(project_id, data):
    with database_proxy.atomic():
        return _create(project_id, data)


def _create(project_id, data):
    _ensure_project(project_id)
    kind = _check_kind(data.get("kind"))
    subject = _required_text(data, "subject")
    source = _optional_text(data, "source")
    driver = _check_driver(kind, data.get("driver"))
    _require_driver(kind, driver)
    status = _check_status(data.get("status") or "open")
    covered_by = _optional_text(data, "covered_by")
    waive_reason = _optional_text(data, "waive_reason")
    _check_status_evidence(status, covered_by, waive_reason)
    if status == "covered":
        raise ValidationError("a delivery item cannot be created already covered")
    key = _finding_key(kind, subject, source)
    existing = next(
        (
            item
            for item in delivery_item_repo.list_by_project(project_id)
            if _finding_key(item.kind, item.subject, item.source) == key
        ),
        None,
    )
    if existing is not None:
        raise ConflictError(
            f"matching finding already exists as {existing.item_number} "
            f"(status={existing.status}); update that item instead of "
            "creating a duplicate"
        )
    found_in_round = data.get("found_in_round", 1)
    if (
        not isinstance(found_in_round, int)
        or isinstance(found_in_round, bool)
        or found_in_round < 1
    ):
        raise ValidationError("found_in_round must be a positive integer")

    item = delivery_item_repo.create(
        project_id,
        kind=kind,
        subject=subject,
        source=source,
        driver=driver,
        status=status,
        covered_by=covered_by,
        waive_reason=waive_reason,
        found_in_round=found_in_round,
    )
    # Rows use the stage-specific public number.
    prefix = "INTEG" if kind in _INTEGRATION_KINDS else "SYS"
    item = numbering.assign_number(item, prefix, "item_number", project_id=project_id)
    work_owner_service.apply(item, data, default_to_actor=True)
    return item


def get(item_id):
    item = delivery_item_repo.get(item_id)
    if item is None:
        raise NotFoundError(f"{_LABEL} {item_id} not found")
    return item


def get_by_number(project_id, item_number):
    item = delivery_item_repo.get_by_number(project_id, item_number)
    if item is None:
        raise NotFoundError(f"{_LABEL} {item_number} not found")
    return item


def list_by_project(project_id, kinds=None):
    _ensure_project(project_id)
    return work_owner_service.preload(delivery_item_repo.list_by_project(project_id, kinds=kinds))


def update(item_id, data):
    with database_proxy.atomic():
        return _update(item_id, data)


def _update(item_id, data):
    item = get(item_id)
    kind = _check_kind(data["kind"]) if "kind" in data else item.kind
    subject = _required_text(data, "subject") if "subject" in data else item.subject
    source = _optional_text(data, "source") if "source" in data else item.source
    driver = (
        _check_driver(kind, data.get("driver")) if "driver" in data else item.driver
    )
    _require_driver(kind, driver)
    status = _check_status(data["status"]) if "status" in data else item.status
    covered_by = (
        _optional_text(data, "covered_by") if "covered_by" in data else item.covered_by
    )
    waive_reason = (
        _optional_text(data, "waive_reason")
        if "waive_reason" in data
        else item.waive_reason
    )
    _check_status_evidence(status, covered_by, waive_reason)
    verified_revision = _optional_text(data, "verified_revision") if "verified_revision" in data else item.verified_revision
    repair_record = _optional_text(data, "repair_record") if "repair_record" in data else item.repair_record
    blocked_reason = _optional_text(data, "blocked_reason") if "blocked_reason" in data else item.blocked_reason
    _check_coverage(status, covered_by, verified_revision)
    evidence_command = _optional_text(data, "evidence_command") if "evidence_command" in data else item.evidence_command
    evidence_output = _optional_text(data, "evidence_output") if "evidence_output" in data else item.evidence_output
    evidence_exit_code = data.get("evidence_exit_code") if "evidence_exit_code" in data else item.evidence_exit_code
    if status == "covered":
        stage = "integration" if kind in _INTEGRATION_KINDS else "system"
        run = delivery_stage_run_repo.active(item.project_id, stage)
        if run is None:
            raise ConflictError(f"cannot cover {item.item_number}: no active {stage} stage run")
        if not evidence_command.strip() or not evidence_output.strip() or evidence_exit_code != 0:
            raise ValidationError("covered requires evidence_command, non-empty evidence_output and evidence_exit_code=0")
        if verified_revision != run.code_revision:
            raise ConflictError("covered evidence revision must equal the active stage run code_revision")
        item.stage_run_number = run.run_number
    # A kind change that drops out of _DRIVER_KINDS must not leave a stale
    # driver behind (e.g. journey -> assembly), even when the caller didn't
    # touch driver in this same call.
    if kind not in _DRIVER_KINDS:
        driver = None
    if "status" in data:
        if status == "open":
            covered_by = "" if "covered_by" not in data else covered_by
            verified_revision = "" if "verified_revision" not in data else verified_revision
            waive_reason = "" if "waive_reason" not in data else waive_reason
        elif status == "covered":
            waive_reason = ""
        elif status == "waived":
            covered_by = ""

    item.kind = kind
    item.subject = subject
    item.source = source
    item.driver = driver
    item.status = status
    item.covered_by = covered_by
    item.verified_revision = verified_revision
    item.repair_record = repair_record
    item.blocked_reason = blocked_reason
    item.evidence_command = evidence_command
    item.evidence_output = evidence_output
    item.evidence_exit_code = evidence_exit_code
    item.waive_reason = waive_reason
    if "found_in_round" in data:
        found_in_round = data["found_in_round"]
        if (
            not isinstance(found_in_round, int)
            or isinstance(found_in_round, bool)
            or found_in_round < 1
        ):
            raise ValidationError("found_in_round must be a positive integer")
        item.found_in_round = found_in_round
    work_owner_service.apply(item, data)
    return delivery_item_repo.save(item)


def delete(item_id):
    """Delete an item — unless it carries a decision that will be asked again.

    A ``waived`` item is the only kind of record here that answers a question
    the next round WILL re-ask. The gap it names was found by scanning the code
    or reading the requirements, so the next scan finds it again; without the
    waiver on record it comes back looking new, and whoever waived it the first
    time has to make the same call from scratch with none of the reasoning that
    settled it. That is exactly the rediscovery loop this whole stage exists to
    end.

    So the answer is not an undo. A recycle bin would add a page, a route and a
    purge action and still leave the record deletable at the end of it; refusing
    the delete keeps the decision instead of offering to retrieve it. Changing
    your mind is still a two-step, and the two steps are the point: put the item
    back to ``open`` — which is itself the honest statement that the question is
    live again — and then delete it if it was never real.

    ``open`` and ``covered`` items delete freely. An ``open`` one is often just
    a bad scan hit with nothing worth keeping, and a ``covered`` one points at a
    DESIGN-*/TASK-* that outlives it.
    """
    item = get(item_id)
    if item.status == "waived":
        raise ConflictError(
            f"cannot delete {item.item_number}: it is waived, and the reason "
            "recorded on it is the answer to a question the next round will "
            "ask again — deleting it means someone re-decides this from "
            "scratch. Set status back to 'open' first if the decision no "
            "longer stands."
        )
    return _softdel.delete(delivery_item_repo, item_id, _LABEL)


def counts_by_kind(project_id):
    """Derived, not stored: ``{kind: {total, open, covered, waived}}`` for
    every kind that has at least one item. The count bar at the top of the
    integration/system pages reads this directly."""
    _ensure_project(project_id)
    counts = {}
    for item in delivery_item_repo.list_by_project(project_id):
        bucket = counts.setdefault(
            item.kind, {"total": 0, "open": 0, "covered": 0, "waived": 0}
        )
        bucket["total"] += 1
        bucket[item.status] += 1
    return counts
