"""ChangeRequest business logic (see docs/cn/design-evolution.md#changes).

Two things live here and they are kept apart on purpose:

- the **workflow** (propose → analyze → approve → apply → close), which is
  governance: it records intent, blast radius and approval under the applicable
  authorization (including ordinary repairs within existing user authorization);
- the **impact analysis**, which walks the existing traceability chain
  (DESIGN → TASK) and reports what sits downstream of the target,
  together with what the change policy says to do about each one given how far it got.

What this module deliberately does NOT do is edit anything. Applying a change
request marks that the change was made; the new wording is authored through the
normal object APIs by whoever is doing the work. An "apply" that rewrote objects
would put an unreviewable editor behind a status transition, and the downstream
staleness signal (suspect) already fires on its own from the real edit.
"""
import json

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.time import utcnow
from app.models.change_request import (
    ANALYZING,
    APPLIED,
    APPROVED,
    CHANGE_REQUEST_TRANSITIONS,
    CHANGE_TARGET_TYPES,
    CLOSED,
    PROPOSED,
)
from app.repositories import (
    change_request_repo,
    design_case_repo,
    development_task_repo,
    project_repo,
)
from app.services import numbering, soft_delete as _softdel

_LABEL = "change request"

NEW = "new"
DESIGN_CASE = "design_case"
DEVELOPMENT_TASK = "development_task"


def _also_list(raw):
    """A stored JSON list column; a corrupt one contributes nothing."""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []


def _also(change_request):
    """The stored secondary targets; a corrupt column contributes nothing
    rather than failing the whole analysis."""
    try:
        value = json.loads(change_request.also_changes or "[]")
    except (TypeError, ValueError):
        return []
    return value if isinstance(value, list) else []

#: The staged change response, keyed by how far the downstream object got. The point
#: of naming these is that the right answer is NOT "redo it" in most of them —
#: once something has shipped, the default is to ADD: approved CRs are grouped
#: into source-backed TASKs, while each CR belongs to at most one TASK.
_TASK_RESPONSE = {
    "completed": (
        "已交付:默认新增而不是推翻——一个或多个获批 CR 可合并到一个来源任务;"
        "每个 CR 只能归属一个 TASK。只有判定这次交付本身失效, 才 reopen_completed "
        "并说明它漏了绑定契约的哪一条。"
    ),
    "claimable": "尚未领取:改完设计重新确认即可, 任务会跟着更新。",
}
_TASK_IN_FLIGHT_RESPONSE = (
    "开发中:会标为 suspect 提示。由 manager 判断是继续做完再改, 还是"
    " withdraw_from_pr 撤回重做。"
)


def _ensure_project(project_id):
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")


def _resolve_target(project_id, target_type, target_id):
    """A target must exist and belong to the project — except ``new``, which
    by definition points at something that does not exist yet."""
    if target_type not in CHANGE_TARGET_TYPES:
        raise ValidationError(
            f"invalid target_type: {target_type}; valid values: "
            f"{', '.join(CHANGE_TARGET_TYPES)}"
        )
    if target_type == NEW:
        if target_id is not None:
            raise ValidationError(
                "target_id must be omitted for target_type 'new' (there is "
                "nothing to point at yet)"
            )
        return None
    if not isinstance(target_id, int):
        raise ValidationError(f"invalid target_id: {target_id}")
    if target_type == DEVELOPMENT_TASK:
        target = development_task_repo.get(target_id)
    else:
        target = design_case_repo.get(target_id)
    if target is None or target.project_id != project_id:
        raise ValidationError(f"invalid target_id: {target_id}")
    return target


def _resolve_also_changes(project_id, primary_type, primary_id, raw):
    """Validate the secondary targets and return them normalised.

    Each is resolved exactly like the primary, so an unreachable id fails here
    rather than silently contributing nothing to the impact analysis — which
    would reproduce the very blind spot this field exists to close.
    """
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValidationError(
            "also_changes must be a list of {target_type, target_id}"
        )
    seen = {(primary_type, primary_id)}
    out = []
    for index, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            raise ValidationError(
                f"also_changes[{index}] must be an object with target_type and target_id"
            )
        entry_type = entry.get("target_type")
        entry_id = entry.get("target_id")
        if entry_type == NEW:
            raise ValidationError(
                "also_changes cannot contain 'new': a thing that does not exist "
                "yet is not also being rewritten by this change"
            )
        _resolve_target(project_id, entry_type, entry_id)
        key = (entry_type, entry_id)
        if key in seen:
            raise ValidationError(
                f"also_changes[{index}] repeats a target already listed"
            )
        seen.add(key)
        out.append({"target_type": entry_type, "target_id": entry_id})
    return out


def create(project_id, data):
    _ensure_project(project_id)
    if "status" in data:
        raise ValidationError(
            "status is server-managed; omit it (new change requests start at 'proposed')"
        )
    target_type = data.get("target_type")
    target_id = data.get("target_id")
    _resolve_target(project_id, target_type, target_id)
    also_changes = _resolve_also_changes(
        project_id, target_type, target_id, data.get("also_changes")
    )
    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        raise ValidationError("reason is required")

    change_request = change_request_repo.create(
        project_id=project_id,
        target_type=target_type,
        target_id=target_id,
        also_changes=json.dumps(also_changes, ensure_ascii=False),
        target_versions=json.dumps(
            _snapshot_versions(target_type, target_id, also_changes),
            ensure_ascii=False,
        ),
        reason=reason.strip(),
    )
    return numbering.assign_number(
        change_request, "CR", "change_request_number", project_id=project_id
    )


def get(change_request_id):
    change_request = change_request_repo.get(change_request_id)
    if change_request is None:
        raise NotFoundError(f"change request {change_request_id} not found")
    return change_request


def get_by_number(project_id, change_request_number):
    change_request = change_request_repo.get_by_number(project_id, change_request_number)
    if change_request is None:
        raise NotFoundError(f"change request {change_request_number} not found")
    return change_request


def get_deleted_by_number(project_id, change_request_number):
    change_request = change_request_repo.get_deleted_by_number(
        project_id, change_request_number
    )
    if change_request is None:
        raise NotFoundError(f"change request {change_request_number} not found")
    return change_request


def list_by_project(project_id):
    _ensure_project(project_id)
    return change_request_repo.list_by_project(project_id)


def list_deleted(project_id):
    return change_request_repo.list_deleted_by_project(project_id)


def update(change_request_id, data):
    """Only the reason is editable, and only before approval.

    After a stated reason was approved, editing that reason would make the
    approval a signature on text nobody read.
    """
    change_request = get(change_request_id)
    if change_request.status not in (PROPOSED, ANALYZING):
        raise ConflictError(
            f"cannot edit change request in state '{change_request.status}'; "
            "the reason is what was approved"
        )
    if "reason" in data:
        reason = data["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise ValidationError("reason is required")
        change_request.reason = reason.strip()
    return change_request_repo.save(change_request)


def _transition(change_request, target_status):
    allowed = CHANGE_REQUEST_TRANSITIONS.get(change_request.status, set())
    if target_status not in allowed:
        raise ConflictError(
            f"cannot move change request from '{change_request.status}' to "
            f"'{target_status}'"
        )
    change_request.status = target_status


def analyze(change_request_id):
    """Walk the chain and store what is downstream of the target."""
    change_request = get(change_request_id)
    _transition(change_request, ANALYZING)
    change_request.impact = json.dumps(
        impact_of(
            change_request.project_id,
            change_request.target_type,
            change_request.target_id,
            _also(change_request),
        ),
        ensure_ascii=False,
    )
    return change_request_repo.save(change_request)


def approve(change_request_id):
    change_request = get(change_request_id)
    _transition(change_request, APPROVED)
    return change_request_repo.save(change_request)


def list_for_target(project_id, target_type, target_id):
    """Every change request that rewrites this object — primary or secondary.

    Traceability ran one way: a request recorded which designs it changed, and
    a design had no idea it had ever been changed by anything. Reading one
    therefore showed what it says NOW with no way to see why it says that, or
    that it used to say something else, short of opening every request in the
    project.

    This is for reading WHY, never for reconciling WHAT. A request never
    carries content — after ``apply`` the design itself IS the outcome — so a
    list treated as a diff sheet against the design is being misread.
    """
    matches = []
    for change_request in list_by_project(project_id):
        for target in _all_targets(
            change_request.target_type,
            change_request.target_id,
            _also(change_request),
        ):
            if (target.get("target_type") == target_type
                    and target.get("target_id") == target_id):
                matches.append(change_request)
                break
    return matches


def _all_targets(target_type, target_id, also_changes):
    """Every object this request rewrites: the primary one, then the rest."""
    targets = [{"target_type": target_type, "target_id": target_id}]
    targets.extend(also_changes or [])
    return targets


def _snapshot_versions(target_type, target_id, also_changes):
    """Each target's version right now, skipping the ones with no version.

    Only DESIGN-*/FEATURE-* carry version rows. A delivered task does not, so
    it is left out here and reported as unverifiable at apply time rather than
    silently counted as unchanged — which would turn a legitimate code-only
    change into a blocked one.
    """
    from app.services import suspect_service

    out = []
    for target in _all_targets(target_type, target_id, also_changes):
        kind, oid = target.get("target_type"), target.get("target_id")
        if kind == NEW or oid is None:
            continue
        version = suspect_service.current_version_number(kind, oid)
        if version is None:
            continue
        out.append({"target_type": kind, "target_id": oid, "version_number": version})
    return out


def unchanged_targets(change_request):
    """Targets whose version is exactly what it was when this was written.

    Derived, never stored: after someone finally does edit the design, this
    empties itself. The point is to make "applied" falsifiable — a request can
    say the change was made upstream while every upstream it names sits at the
    version it started at, and until now nothing compared the two.
    """
    from app.services import suspect_service

    out = []
    for entry in _also_list(change_request.target_versions):
        kind, oid = entry.get("target_type"), entry.get("target_id")
        current = suspect_service.current_version_number(kind, oid)
        if current is not None and current == entry.get("version_number"):
            out.append({
                "target_type": kind,
                "target_id": oid,
                "version_number": current,
            })
    return out


def apply(change_request_id):
    """Record that the change has been made upstream.

    Nothing is rewritten here — see the module docstring. Downstream suspect
    edges light up from the real edit, not from this transition.

    Refused when EVERY versioned target still sits at the version this request
    was written against: ``applied`` asserts the change was made, and if
    nothing upstream moved, it was not. Partial is allowed and only reported —
    a request may legitimately conclude that one of its targets needed no edit
    after all, and blocking that would push people to file a second request
    for work they correctly did not do.
    """
    change_request = get(change_request_id)
    recorded = _also_list(change_request.target_versions)
    if recorded:
        stale = unchanged_targets(change_request)
        if len(stale) == len(recorded):
            raise ConflictError(
                "cannot apply: every target is still at the version this "
                "change request was written against, so nothing was changed "
                "upstream. Edit and re-confirm the target(s) first, or close "
                "this request with a resolution saying it was dropped."
            )
    _transition(change_request, APPLIED)
    change_request.applied_at = utcnow()
    return change_request_repo.save(change_request)


def close(change_request_id, data=None):
    """Terminal. ``resolution`` must say which ending this was."""
    change_request = get(change_request_id)
    resolution = (data or {}).get("resolution")
    if not isinstance(resolution, str) or not resolution.strip():
        raise ValidationError(
            "resolution is required: say whether the change was made, dropped "
            "or rejected, so the record is readable later"
        )
    _transition(change_request, CLOSED)
    change_request.resolution = resolution.strip()
    return change_request_repo.save(change_request)


def delete(change_request_id):
    return _softdel.delete(change_request_repo, change_request_id, _LABEL)


def restore(change_request_id):
    return _softdel.restore(change_request_repo, change_request_id, _LABEL)


def purge(change_request_id):
    _softdel.purge(change_request_repo, change_request_id, _LABEL)


# --- impact analysis -------------------------------------------------------

def _task_entry(task):
    return {
        "object_type": "development_task",
        "object_id": task.id,
        "object_number": task.task_number,
        "status": task.status,
        "response": _TASK_RESPONSE.get(task.status, _TASK_IN_FLIGHT_RESPONSE),
    }


def impact_of(project_id, target_type, target_id, also_changes=None):
    """The downstream chain under EVERY object this change rewrites.

    Uses the existing foreign key — DESIGN -> its TASKs — rather than any new
    graph structure. A ``new`` target has no downstream by construction: that
    is the whole appeal of answering a change with an increment. A
    ``development_task`` target is its own downstream: the delivered work IS
    the thing being changed, and reporting nothing there would read as "this
    disturbs no one".

    ``also_changes`` is walked identically. It is the reason this takes a list
    at all: the analysis is only as wide as the set of targets it is given, so
    a second design left off the record does not merely go unmentioned — its
    tasks never enter the blast radius. Entries are de-duplicated by task, so
    two targets sharing a task report it once.
    """
    entries = []
    seen = set()

    def _walk(kind, oid):
        if kind == NEW or oid is None:
            return
        if kind == DEVELOPMENT_TASK:
            task = development_task_repo.get(oid)
            if task is not None and task.id not in seen:
                seen.add(task.id)
                entries.append(_task_entry(task))
            return
        design_case = design_case_repo.get(oid)
        if design_case is None:
            return
        for task in development_task_repo.list_by_design_case(design_case.id):
            if task.id in seen:
                continue
            seen.add(task.id)
            entries.append(_task_entry(task))

    _walk(target_type, target_id)
    for entry in also_changes or []:
        _walk(entry.get("target_type"), entry.get("target_id"))
    return entries
