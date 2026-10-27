"""DevelopmentTask business logic.

P1.3: creation (via task generation), read access, entry-status resolution.
P1.4: claim + pre-development review. The pre-review actions are action-driven:
each enforces its own precondition, so the API cannot skip review into the AI
development phase. Later transitions (coding / agent review / PR / CI) arrive
in P1.5-P1.8.
"""
from app.core.i18n import gettext
import json

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.time import utcnow
from app.db.base import database_proxy
from app.models.agent_run import (
    AGENT_ROLES,
    AGENT_RUN_STATUSES,
    CRITERION_VERDICTS,
)
from app.repositories import (
    agent_run_repo,
    design_case_repo,
    change_request_repo,
    development_task_repo,
    feature_repo,
    project_repo,
    pull_request_repo,
    task_event_repo,
)
from app.services import (
    acceptance_criteria,
    feature_service,
    lifecycle,
    numbering,
    suspect_service,
)
from app.services import soft_delete as _softdel

_LABEL = "development task"

# Terminal AgentRun statuses that drive the loop state machine.
_TERMINAL_STATUSES = frozenset({"passed", "failed", "needs_fix"})
#: Reported, but not a judgement: the run never got far enough to have one.
#: Deliberately outside _TERMINAL_STATUSES so it moves no task — see
#: record_agent_run.
_BLOCKED = "blocked"
#: Both roles record against the one in-progress state. Role SEQUENCING used to
#: be enforced here — coder only in `coding`, qa only in `agent_qa` — which
#: cost three task states to express something the quality gate already decides
#: on better evidence: it requires the latest run of each role to have passed
#: AND the qa run to be newer than the coder run. Order that matters is checked
#: where it matters; order that does not is the manager's own business.
_ROLE_ALLOWED_STATES = {
    "coder": frozenset({"in_progress"}),
    "qa": frozenset({"in_progress"}),
}
#: qa has no ``failed``. It used to, as a byte-identical alias of
#: ``needs_fix`` — both sent the task to ``fixing`` with the same fields — and
#: an alias with no distinct meaning is where an outcome with no home ends up:
#: the qa run that could not reach its design at all was filed as "qa failed"
#: (TASK-1, 2026-07-21), which read downstream as a verdict against the code.
#: That outcome now has its own name, so the alias is gone: qa either judged
#: (passed / needs_fix) or did not (blocked).
_ROLE_ALLOWED_TERMINAL_STATUSES = {
    "coder": frozenset({"passed", "failed"}),
    "qa": frozenset({"passed", "needs_fix"}),
}
#: qa outcomes that assert a judgement about the design, and so must prove the
#: run actually held it. ``failed``/``blocked`` assert nothing and prove nothing.
_QA_VERDICT_STATUSES = frozenset({"passed", "needs_fix"})


def create_task(
    design_case,
    *,
    title,
    task_type,
    priority="normal",
    complexity="medium",
    risk_level="low",
    description="",
    acceptance_criteria="",
    constraints="",
    non_goals="",
):
    """Create one task directly under a confirmed design, stored as
    "claimable" — that only means "created, awaiting claim", NOT "can be
    claimed right now". Whether it can actually be claimed is computed live
    from the upstream Feature graph at claim time (see
    feature_service.feature_satisfied), never stored.

    The caller (design_case_service.create_task) already validated the
    design is confirmed and the enums are valid.
    """
    if development_task_repo.list_by_design_case(design_case.id):
        raise ConflictError(
            f"design {design_case.design_number} already has its one TASK-*; "
            "use an approved CR source task for new capability scope"
        )
    task = development_task_repo.create(
        project_id=design_case.project_id,
        design_case_id=design_case.id,
        feature_id=design_case.feature_id,
        source_kind="design",
        title=title,
        description=description,
        task_type=task_type,
        priority=priority,
        complexity=complexity,
        risk_level=risk_level,
        source_type="requirement",
        acceptance_criteria=acceptance_criteria,
        constraints=constraints,
        non_goals=non_goals,
        status="claimable",
    )
    task = numbering.assign_number(
        task, "TASK", "task_number", project_id=design_case.project_id
    )
    # The task is built against the design as it reads right now; record that
    # version so a later design revision shows up as a suspect edge instead of
    # silently leaving the task working from an outdated spec (see docs/cn/design-evolution.md#changes).
    suspect_service.record_task_basis(task, design_case)
    return task


def create_source_task(
    project_id,
    *,
    feature_id,
    title,
    source_kind,
    task_type,
    priority="normal",
    complexity="medium",
    risk_level="low",
    description="",
    acceptance_criteria="",
    constraints="",
    non_goals="",
):
    """Create a claimable task with no DESIGN source.

    Approved CR sources are attached after creation through
    ``task_source_service``. Keeping this constructor separate prevents a
    design task from silently becoming a CR task.
    """
    from app.models.task_enums import (
        TASK_COMPLEXITIES,
        TASK_PRIORITIES,
        TASK_RISK_LEVELS,
        TASK_SOURCE_CATEGORIES,
        TASK_TYPES,
    )

    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    if not isinstance(feature_id, int):
        raise ValidationError("feature_id is required for a source task")
    feature = feature_repo.get(feature_id)
    if feature is None or feature.project_id != project_id:
        raise NotFoundError(f"feature {feature_id} not found")
    if source_kind not in TASK_SOURCE_CATEGORIES:
        raise ValidationError(f"invalid source_kind: {source_kind}")
    if source_kind == "design":
        raise ValidationError("design tasks must be created from a confirmed design")
    for field, value, allowed in (
        ("task_type", task_type, TASK_TYPES),
        ("priority", priority, TASK_PRIORITIES),
        ("complexity", complexity, TASK_COMPLEXITIES),
        ("risk_level", risk_level, TASK_RISK_LEVELS),
    ):
        if value not in allowed:
            raise ValidationError(f"invalid {field}: {value}")
    task = development_task_repo.create(
        project_id=project_id,
        design_case_id=None,
        feature_id=feature_id,
        title=title,
        description=description,
        task_type=task_type,
        priority=priority,
        complexity=complexity,
        risk_level=risk_level,
        source_kind=source_kind,
        acceptance_criteria=acceptance_criteria,
        constraints=constraints,
        non_goals=non_goals,
        status="claimable",
    )
    task = numbering.assign_number(
        task, "TASK", "task_number", project_id=project_id
    )
    return task


def list_dependencies(task_id):
    return development_task_repo.list_dependencies(task_id)


def _require_dependency_editable(task):
    if task.status == "claimable":
        return
    if task.status == "needs_revision" and task.source_kind != "design":
        return
    raise ConflictError(
        "task dependencies can be changed only while a task is claimable or "
        "a source-backed task is in needs_revision"
    )


def add_dependency(task_id, depends_on_task_id):
    task = get(task_id)
    _require_dependency_editable(task)
    dependency = get(depends_on_task_id)
    if task.project_id != dependency.project_id:
        raise ValidationError("task dependencies must stay within one project")
    if task.id == dependency.id:
        raise ValidationError("a task cannot depend on itself")
    if development_task_repo.dependency_exists(task.id, dependency.id):
        raise ConflictError("task dependency already exists")
    # A proposed edge is invalid if the dependency can already reach the task.
    # This small DFS keeps the graph acyclic without storing a transitive cache.
    pending = [dependency.id]
    seen = set()
    while pending:
        current = pending.pop()
        if current in seen:
            continue
        seen.add(current)
        if current == task.id:
            raise ConflictError("task dependency would create a cycle")
        pending.extend(row.depends_on_task_id for row in development_task_repo.list_dependencies(current))
    return development_task_repo.add_dependency(task.id, dependency.id)


def remove_dependency(task_id, depends_on_task_id):
    task = get(task_id)
    _require_dependency_editable(task)
    get(depends_on_task_id)
    return development_task_repo.remove_dependency(task_id, depends_on_task_id)


def list_by_project(project_id):
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")
    return development_task_repo.list_by_project(project_id)


def list_by_project_with_gate(project_id):
    """List serialization for the task list page: each task's dict plus
    ``claim_ready`` / ``waiting_on_feature_numbers`` / ``feature_number``
    (display only — the authority is the live check inside ``claim``). Batches
    the Feature-graph read into a handful of project-wide queries (see
    feature_service.project_gate_context) instead of one per task.

    ``feature_number`` is read off the same context rather than walking
    task -> design -> feature per row: "等待上游 Feature 完成" only means
    something next to the task's own Feature, and resolving it lazily would
    reintroduce the N+1 the context exists to avoid."""
    tasks = list_by_project(project_id)
    ctx = feature_service.project_gate_context(project_id)
    stale = suspect_service.stale_edges_by_downstream(project_id)
    result = []
    for task in tasks:
        claim_ready, waiting_on = feature_service.claim_readiness(task, ctx)
        feature_id = task.feature_id or ctx["feature_id_by_design_id"].get(task.design_case_id)
        payload = task.to_dict() | {
            "change_requests": [
                row.change_request_number for row in change_request_repo.list_by_task(task.id)
            ],
        } | {
                    "claim_ready": claim_ready,
                    "waiting_on_feature_numbers": waiting_on,
                    "feature_number": ctx["feature_number_by_id"].get(feature_id),
                    "dependencies": [
                        {
                            "task_number": row.depends_on_task.task_number,
                            "status": row.depends_on_task.status,
                            "completed": row.depends_on_task.status == "completed",
                        }
                        for row in development_task_repo.list_dependencies(task.id)
                    ],
                    "waiting_on_task_numbers": [
                        row.depends_on_task.task_number
                        for row in development_task_repo.list_dependencies(task.id)
                        if row.depends_on_task.status != "completed"
                    ],
                }
        result.append(
            suspect_service.annotate(
                payload,
                suspect_service.DEVELOPMENT_TASK,
                task.id,
                stale,
            )
        )
    return result


def list_deleted(project_id):
    """Soft-deleted development tasks for this project (its own recycle bin)."""
    return development_task_repo.list_deleted_by_project(project_id)


def get(task_id):
    task = development_task_repo.get(task_id)
    if task is None:
        raise NotFoundError(f"task {task_id} not found")
    return task


def get_by_number(project_id, task_number):
    task = development_task_repo.get_by_number(project_id, task_number)
    if task is None:
        raise NotFoundError(f"task {task_number} not found")
    return task


_EDITABLE_TASK_FIELDS = (
    "title",
    "description",
    "task_type",
    "priority",
    "complexity",
    "risk_level",
    "acceptance_criteria",
    "constraints",
    "non_goals",
)


def update(task_id, data):
    """Update editable metadata fields. Status is never changed via PATCH.

    The freeze point moved from generate-time to claim-time (see docs/cn/design-evolution.md#objects): an
    unclaimed ``claimable`` task is still an editable draft of its own content,
    and ``claim`` is what locks its definition — a coder must build against the
    text they claimed, not one that shifted under them mid-flight.
    """
    task = get(task_id)
    if "status" in data:
        raise ValidationError("status cannot be changed via PATCH")
    source_revision = task.status == "needs_revision" and task.source_kind != "design"
    if task.status != "claimable" and not source_revision:
        raise ConflictError(
            f"cannot edit task {task.task_number}: it is '{task.status}'; only an "
            "unclaimed task or a source task in needs_revision may be edited"
        )
    for field in _EDITABLE_TASK_FIELDS:
        if field in data:
            setattr(task, field, data[field] if data[field] is not None else "")
    return development_task_repo.save(task)


def _save_transition(task, previous_status, note=""):
    """Save a task and append its status change to the timeline.

    Every status write goes through here so the history cannot silently miss a
    step: a transition recorded in twelve separate places is a transition that
    eventually gets added in a thirteenth without its event. Recording happens
    AFTER the save, so a failed write leaves no phantom event.

    A no-op status write records nothing — save() is also used for edits that
    do not move the task.
    """
    from app.services import task_owner_service
    with database_proxy.atomic():
        saved = development_task_repo.save(task)
        if saved.status != previous_status:
            task_event_repo.record(saved.id, previous_status, saved.status, note)
            task_owner_service.record_transition(saved, previous_status)
    return saved


def realign_basis(task_id, data=None):
    """Digest a suspect edge: the design moved, someone looked, the task as
    written still stands.

    For a task that has NOT shipped, that judgement is about a description —
    cheap to make, cheap to revisit, and nothing is asserted about code. For a
    ``completed`` task it is a different claim entirely: that code already
    merged still satisfies a specification that has since changed. Nothing
    re-runs, no qa verdict is recorded, and the act takes one call — which is
    how a live project cleared a suspect edge on delivered work 38 seconds
    after the design changed, leaving a record in which "we checked" and "we
    clicked" are indistinguishable.

    So a completed task requires a ``reason``, and it is written to the
    timeline. That does not make the claim true; it makes it attributable, and
    it puts the cost of asserting it above zero. If the reason cannot be
    written, the honest actions are ``reopen_completed`` (the delivery no
    longer satisfies the design) or leaving the edge suspect.
    """
    task = get(task_id)
    reason = ((data or {}).get("reason") or "").strip()
    if task.status == "completed":
        if not reason:
            raise ValidationError(
                "reason is required to realign a completed task: say what was "
                "checked that shows the merged delivery still satisfies the "
                "revised design — if it no longer does, reopen_completed "
                "instead of clearing the flag"
            )
        task_event_repo.record(
            task.id, task.status, task.status, f"基线对齐：{reason}"
        )
    suspect_service.realign(suspect_service.DEVELOPMENT_TASK, task.id)
    return task


def _require_upstream_satisfied(task, action):
    """Refuse ``action`` while an upstream Feature is no longer satisfied.

    Claim time was the only place this was checked, so a task already in
    flight when its upstream got reopened could still be marked done. That
    records a completion standing on a foundation that has been withdrawn —
    the exact false record this system exists to prevent, and the reason a
    downstream task went on believing an upstream capability existed.

    Deliberately gates COMPLETION, not coding: the work in hand is usually
    still mostly right, so stopping the coder buys nothing, while letting it
    claim done buys a lie. Computed live from the Feature graph like every
    other ordering question here — no stored blocked state, nothing to wake.
    """
    if feature_service._feature_upstream_unsatisfied(task):
        raise ConflictError(
            f"cannot {action}: an upstream feature is no longer satisfied"
        )


def _require_confirmed_design(task, action):
    """Refuse ``action`` while the task's design is not confirmed.

    A design being edited has no settled contract to work against; a task
    whose design is in draft must wait for the revision to land.
    """
    if task.design_case_id is None:
        return
    design_case = design_case_repo.get(task.design_case_id)
    if design_case is None or design_case.status != lifecycle.CONFIRMED:
        raise ConflictError(
            f"cannot {action}: linked design case is not confirmed"
        )


def _require_task_dependencies_completed(task):
    pending = [
        row.depends_on_task.task_number
        for row in development_task_repo.list_dependencies(task.id)
        if row.depends_on_task.status != "completed"
    ]
    if pending:
        raise ConflictError(
            f"cannot claim {task.task_number}: dependencies are incomplete: "
            + ", ".join(pending)
        )


def _require_state(task, expected):
    """Refuse unless the task is in ``expected`` (one state, or several)."""
    allowed = (expected,) if isinstance(expected, str) else tuple(expected)
    if task.status in allowed:
        return
    if len(allowed) == 1:
        raise ConflictError(
            f"task {task.id} is '{task.status}', expected '{allowed[0]}'"
        )
    raise ConflictError(
        f"task {task.id} is '{task.status}', expected one of {sorted(allowed)}"
    )


#: Where a pre-review verdict may be recorded from.
#:
#: ``pre_reviewing`` used to be the only one, which made ``start_pre_review`` a
#: mandatory step — and it is a step that records NOTHING: no actor, no
#: timestamp, no payload. ``pre_reviewer``/``pre_reviewed_at``/
#: ``pre_review_comment`` are all written at approve/reject time, not at start.
#: So the only thing it gated was "did the same manager, moments earlier, call
#: a free no-argument endpoint on itself?" — a door the party being gated
#: opens for itself at zero cost is not a door. What it did reliably produce
#: was a 409 on the first development round of every task, because the
#: sequence is stated in the prompt's overview and the manager acts on it 130
#: lines later.
#:
#: ``start_pre_review`` stays, as an optional marker: a human scanning the list
#: can still see that review has begun. It is no longer load-bearing.
_PRE_REVIEW_STATES = ("claimed", "pre_reviewing")


def claim(task_id, data):
    from peewee import SqliteDatabase
    # Serialize the read/check/bind transition so competing claims cannot steal ownership.
    transaction = database_proxy.atomic("IMMEDIATE") if isinstance(database_proxy.obj, SqliteDatabase) else database_proxy.atomic()
    with transaction:
        if not isinstance(database_proxy.obj, SqliteDatabase):
            from app.models.development_task import DevelopmentTask
            locked = DevelopmentTask.select().where(DevelopmentTask.id == task_id).for_update().get_or_none()
            if locked is None:
                raise NotFoundError("task not found")
        return _claim(task_id, data)


def _claim(task_id, data):
    task = get(task_id)
    _require_state(task, "claimable")
    previous = task.status
    # "Dev must follow dependency order" is enforced HERE, the single authority:
    # a task can be claimed only once every upstream Feature is satisfied.
    # Computed live from the Feature graph — never stored, so nothing to wake
    # or expire.
    if feature_service._feature_upstream_unsatisfied(task):
        raise ConflictError("cannot claim: an upstream feature is not yet complete")
    _require_task_dependencies_completed(task)
    # The design is the binding spec, so claiming against one that is mid-edit
    # means coding to a version about to change. reopen_rejected_to_design
    # already refused that; claim never checked, so reopening a design to
    # revise it left its tasks freely claimable against the stale text.
    _require_confirmed_design(task, "claim")
    from app.services import task_owner_service
    user = task_owner_service.claim_identity(task)
    assignee = user.display_name if user else data.get("assignee")
    if not isinstance(assignee, str) or not assignee.strip():
        raise ValidationError("assignee is required")
    task.assignee = assignee.strip()
    task.claimed_at = utcnow()
    task.status = "claimed"
    return _save_transition(task, previous, f"认领人：{task.assignee}")


_RELEASABLE_STATUSES = ("claimed", "pre_reviewing", "ready_for_development")


def release(task_id):
    """Give a task back to the pool before coding starts: -> ``claimable``.

    Allowed while NO CODE EXISTS YET — up to and including
    ``ready_for_development``. Whoever holds the task may be the wrong person,
    may be unavailable, or may simply have clicked the wrong row, and none of
    that is a statement about the work.

    Without this the only exit from ``pre_reviewing`` was
    ``reject_pre_review``, which records "the design that produced this task is
    wrong" and parks the task until that design is revised and re-confirmed.
    Handing a task to someone else is not a statement about the design, and
    forcing it through that branch writes a false one -- the exact kind of
    untrue record this system exists to prevent.

    Releasing from ``ready_for_development`` DISCARDS the pre-review approval
    (``pre_reviewer`` / ``pre_reviewed_at`` are cleared) and the next holder
    must pre-review again. That is the honest of the two options: the approval
    records a specific person judging a specific task fit to build, and
    carrying it silently into a claimable task would let the next holder start
    coding on someone else's verdict without knowing whose it was or what it
    was given for. The cost is a repeated pre-review round; the alternative is
    a stale approval nobody can attribute.

    Refused from ``in_progress`` onward: work exists by then, and a status that
    says "unclaimed, nothing started" would be false while a branch is open.
    Handing over in-flight work is an edit of ``assignee``, not a release.
    """
    task = get(task_id)
    if task.status not in _RELEASABLE_STATUSES:
        raise ConflictError(
            f"task {task.id} is '{task.status}'; only "
            f"{', '.join(_RELEASABLE_STATUSES)} can be released (once coding "
            "has started there is work to hand over, not a claim to drop)"
        )
    previous = task.status
    former = task.assignee
    approved = task.status == "ready_for_development"
    task.status = "claimable"
    task.assignee = None
    task.claimed_at = None
    # Dropped with the claim, not carried into the pool — see docstring.
    task.pre_reviewer = None
    task.pre_reviewed_at = None
    note = f"放弃认领（原认领人：{former or '未记录'}）"
    if approved:
        note += "；预审结论一并作废，需重新预审"
    return _save_transition(task, previous, note)


def reopen_needs_revision(task_id):
    """Return a revision-needed task to the claimable queue.

    ``rejected_to_design`` remains readable for old rows, but new transitions
    use the source-neutral ``needs_revision`` state.  Design-backed tasks still
    require a re-confirmed design; direct CR tasks have no
    design gate and can be reopened after their source task is corrected.
    """
    task = get(task_id)
    _require_state(task, ("needs_revision", "rejected_to_design"))
    previous = task.status
    if task.design_case_id is not None:
        design_case = design_case_repo.get(task.design_case_id)
        if design_case is None or design_case.status != lifecycle.CONFIRMED:
            raise ConflictError("cannot reopen: linked design case is not confirmed")
    task.status = "claimable"
    task.pre_reviewer = None
    task.pre_reviewed_at = None
    task.pre_review_comment = ""
    return _save_transition(task, previous, "修订已完成")


def reopen_rejected_to_design(task_id):
    """Compatibility alias for the pre-``needs_revision`` endpoint."""
    return reopen_needs_revision(task_id)


def start_pre_review(task_id):
    task = get(task_id)
    _require_state(task, "claimed")
    previous = task.status
    task.status = "pre_reviewing"
    return _save_transition(task, previous)


def approve_pre_review(task_id, data):
    """Record the applicable pre-review approval.

    Accepted from ``claimed`` as well as ``pre_reviewing`` — see
    ``_PRE_REVIEW_STATES``. Design-backed tasks require the human decision
    described by the stage method; approved-CR source tasks
    are approved by the manager after its technical contract/source check.
    The approval itself is what this records.
    """
    task = get(task_id)
    _require_state(task, _PRE_REVIEW_STATES)
    previous = task.status
    from app.services.auth_service import current_user
    actor = current_user()
    task.pre_reviewer = actor.display_name if actor else data.get("reviewer") or task.assignee
    task.pre_reviewed_at = utcnow()
    task.status = "ready_for_development"
    return _save_transition(task, previous, f"预审通过（{task.pre_reviewer}）")


def reject_pre_review(task_id, data):
    """Send a task back for source-neutral revision before coding starts.

    Accepted from ``claimed`` as well as ``pre_reviewing`` — see
    ``_PRE_REVIEW_STATES``. A rejection is no more dependent on the marker
    call than an approval is.
    """
    task = get(task_id)
    _require_state(task, _PRE_REVIEW_STATES)
    previous = task.status
    comment = data.get("comment") or ""
    if not comment.strip():
        raise ValidationError("comment is required when rejecting a task")
    from app.services.auth_service import current_user
    actor = current_user()
    task.pre_reviewer = actor.display_name if actor else data.get("reviewer") or task.assignee
    task.pre_reviewed_at = utcnow()
    task.pre_review_comment = comment
    # Design-backed tasks revise their design; source-backed tasks revise their
    # task contract and direct source obligation.
    task.status = "needs_revision"
    return _save_transition(task, previous, comment)


def start_coding(task_id):
    """Open the build unit: ``ready_for_development`` -> ``in_progress``.

    Called ONCE after the applicable task review, when the task starts being
    worked. Design-backed tasks have the human approval required by their
    stage; source-backed tasks have the manager's technical approval. It is not
    a per-round action: coder and qa rounds inside the unit record their runs
    without moving the task, so there is nothing to re-enter. A withdrawal from
    ``ready_for_pr`` returns straight to ``in_progress`` for the same reason.
    """
    task = get(task_id)
    _require_state(task, "ready_for_development")
    previous = task.status
    task.status = "in_progress"
    task.blocking_issues = ""
    return _save_transition(task, previous)


def reject_to_design(task_id, data):
    """Send an ``in_progress`` task back for source-neutral revision.

    Design-backed tasks return to their confirmed DESIGN; source-backed tasks
    return to revision of the task contract and its direct obligations. The
    compatibility endpoint keeps its historical name but always writes
    ``needs_revision``.

    The counterpart to ``reject_pre_review``, for a design defect discovered
    only once coding is under way. ``pre_reviewing`` stops being reachable the
    moment ``start_coding`` runs, so a task whose design turns out to be wrong
    mid-build previously had no recorded destination at all —
    ``reopen_rejected_to_design`` was a way home that nothing led to from
    there.

    Allowed from ``in_progress`` ONLY — deliberately not from ``ready_for_pr``.
    That narrower reach is a decision, not an oversight: ``withdraw_from_pr``
    already pulls a ``ready_for_pr`` task back to ``in_progress`` and records
    its own reason, so the path from there is two calls instead of one, and
    the withdrawal already leaves its own entry in the timeline. A second,
    direct entrance from ``ready_for_pr`` would just shortcut a step that
    already exists and already records something.

    ``reason`` is required and must name the unmet design clause for a
    design-backed task, or the unmet TASK acceptance/source obligation for a
    source-backed task.

    ``pre_reviewer`` / ``pre_reviewed_at`` / ``pre_review_comment`` are left
    untouched. Nothing pre-reviewed anything on this path, so writing a
    mid-coding rejection into columns named for a review step that never ran
    would be a false record of who reviewed what and when. The reason is
    recorded the same place every other transition's note already lives — the
    TaskEvent ``_save_transition`` writes below, which the timeline renders —
    rather than in a new column invented for this one case.

    ``blocking_issues`` IS cleared. That field answers "why is the quality
    gate shut", and once a task leaves the build unit for the design layer it
    has left the quality gate's jurisdiction entirely; carrying an old qa
    summary forward would misstate why the task is sitting still. Already
    recorded AgentRuns are left untouched — a design defect does not erase the
    fact that a coder or qa round ran and reported what it reported, and the
    timeline still renders them.

    Harnoq never touches the git host: whatever branch and commits exist for
    this attempt simply stay where they are. Nothing here retracts or cleans
    them up. ``reopen_rejected_to_design`` returns a design-backed task to
    ``claimable`` after its DESIGN is re-confirmed; a source-backed task
    returns after its TASK contract/source obligations are revised. A fresh
    claim starts a fresh attempt against that corrected binding contract.
    """
    task = get(task_id)
    _require_state(task, "in_progress")
    reason = (data or {}).get("reason") or ""
    if not reason.strip():
        raise ValidationError(
            "reason is required: name the unmet design clause or TASK acceptance/source obligation"
        )
    previous = task.status
    task.status = "needs_revision"
    task.blocking_issues = ""
    return _save_transition(task, previous, reason.strip())


#: test_run's required shape: the exact command, plus five integers read off
#: the suite's own final summary line (not the collection line — "collected"
#: and "passed" are different questions).
_TEST_RUN_INT_FIELDS = ("exit_code", "collected", "passed", "failed", "skipped")


def _qa_test_run(data, status):
    """Validate a qa run's test-suite record; return it as the stored column.

    Structured fields exist because a sentence does not contradict itself: a
    qa run once reported "58 passed" — the agent had read pytest's COLLECTED
    count off the collection line and reported it as PASSED, while the suite
    actually ran 2 failed / 56 passed on that exact commit — and nothing in
    the record caught it, because prose is not compared against itself. Five
    separate integers can be: a run that reports collected == passed with a
    nonzero failed count is visibly not distinguishing the two questions, and
    ``status="passed"`` with any failed count or a nonzero exit code is
    rejected outright below. This does not make lying impossible — the agent
    still writes every one of these numbers itself — it makes a false claim
    visible in the record instead of absorbed by it.

    ``skipped`` was the number the first four could not express. A suite that
    skips a test says nothing about it, yet a record of collected/passed/failed
    alone reads identically whether the expensive tests ran or were skipped to
    the last one: failed is 0 and the exit code is 0 either way. Requiring the
    count does two things. It puts the absence in the record, where a reader
    comparing a verdict against the run can see which evidence was not
    produced. And it makes the four numbers reconcile — ``passed + failed +
    skipped`` may not exceed ``collected`` — so the original confusion becomes
    arithmetically impossible to report rather than merely implausible.

    A nonzero ``skipped`` is NOT refused. Suites legitimately skip on platform,
    on missing optional dependencies, on an opt-in flag for slow work. Whether
    a particular skip leaves a criterion unproven is a judgement about that
    criterion, and it belongs in that criterion's verdict — this gate's job is
    to make sure the judgement is being made against a visible number.
    """
    test_run = data.get("test_run")
    if not isinstance(test_run, dict):
        raise ValidationError(
            f"qa {status} must report test_run: {{command, exit_code, "
            "collected, passed, failed, skipped} read from the test suite's "
            "own final summary line — a passed/needs_fix verdict with no "
            "test-suite record is unverifiable"
        )
    command = (test_run.get("command") or "").strip()
    if not command:
        raise ValidationError(
            "test_run.command is required: the exact command that was run"
        )
    numeric = {}
    for field in _TEST_RUN_INT_FIELDS:
        value = test_run.get(field)
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValidationError(
                f"test_run.{field} must be an integer; got {value!r}"
            )
        numeric[field] = value
    accounted = numeric["passed"] + numeric["failed"] + numeric["skipped"]
    if accounted > numeric["collected"]:
        raise ValidationError(
            f"test_run does not reconcile: passed={numeric['passed']} + "
            f"failed={numeric['failed']} + skipped={numeric['skipped']} = "
            f"{accounted}, which exceeds collected={numeric['collected']}. "
            "Every count comes off the suite's own final summary line; "
            "collected is the number of tests found, not the number that ran"
        )
    if status == "passed" and (numeric["failed"] > 0 or numeric["exit_code"] != 0):
        raise ValidationError(
            "qa passed requires a green suite: test_run reports "
            f"failed={numeric['failed']} exit_code={numeric['exit_code']}; a "
            "passing verdict needs failed=0 and exit_code=0, otherwise report "
            "status=needs_fix"
        )
    return json.dumps({"command": command, **numeric}, ensure_ascii=False)


def _qa_evidence_value(raw, index):
    """Normalize one criterion's ``evidence`` into what gets stored.

    Two shapes are accepted. A bare string is the legacy form — free prose,
    kept exactly as written. It is never migrated into the structured shape:
    200+ historical rows are strings, and prose cannot be split back into a
    tests/code breakdown without inventing data that was never there.

    A dict is the structured form: ``{tests: [...], code: [...], note:
    "..."}``. It exists so "what already proves this criterion, and where" is
    a queryable fact instead of prose a future qa run has to re-derive from
    scratch. All three keys are required. ``tests``/``code`` may each be an
    EMPTY list — an empty ``tests`` is a real, useful answer ("nothing
    automated is watching this criterion"), not a defect to reject on sight.
    But when both lists are empty, ``note`` must say something, or the entry
    carries no evidence of any kind under a different name.
    """
    if isinstance(raw, str):
        evidence = raw.strip()
        if not evidence:
            raise ValidationError(
                f"criteria_verdicts[{index}].evidence is empty: state what was "
                "run or read that establishes this verdict"
            )
        return evidence
    if isinstance(raw, dict):
        missing = [key for key in ("tests", "code", "note") if key not in raw]
        if missing:
            raise ValidationError(
                f"criteria_verdicts[{index}].evidence is missing {missing}: a "
                "structured evidence object must carry all three of tests, "
                "code, note (tests/code may be empty lists, but the keys must "
                "be present)"
            )
        tests, code, note = raw["tests"], raw["code"], raw["note"]
        for key, value in (("tests", tests), ("code", code)):
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                raise ValidationError(
                    f"criteria_verdicts[{index}].evidence.{key} must be a list "
                    "of strings (an empty list is a legitimate answer)"
                )
        if not isinstance(note, str):
            raise ValidationError(
                f"criteria_verdicts[{index}].evidence.note must be a string"
            )
        if not tests and not code and not note.strip():
            raise ValidationError(
                f"criteria_verdicts[{index}].evidence has empty tests, empty "
                "code, and an empty note: an empty tests list is fine on its "
                "own, but something must be in code or note saying what was "
                "actually checked"
            )
        return {"tests": tests, "code": code, "note": note}
    raise ValidationError(
        f"criteria_verdicts[{index}].evidence must be a string, or an object "
        "{tests, code, note} — state what was run or read that establishes "
        "this verdict"
    )


def _qa_evidence(task, data, status):
    """Validate a qa verdict's evidence; return it as stored columns.

    A qa agent proves the binding contract by returning one verdict per
    acceptance criterion, quoting each criterion. Design-backed tasks quote
    the linked DESIGN-*; source-backed tasks quote their TASK acceptance
    criteria and direct source obligations. Restating text it possesses is
    trivial; producing it without the binding handoff is not. That asymmetry
    converts "did this run actually have its judgement standard?" from
    something we trust the agent to volunteer into something the record either
    contains or does not.

    ``test_run`` is validated unconditionally (see ``_qa_test_run``) — it does
    not depend on there being a design to quote, so it is checked before the
    design lookup below, not folded into the same early-return.

    The criteria gate can only demand what exists: a task with no linked
    design, or a design with an empty ``acceptance_criteria``, has no criteria
    to quote and is let through with no per-criterion evidence. That is a real
    hole, and the honest place for it is here rather than in a check that
    pretends to verify nothing.

    Each entry's ``evidence`` is normalized by ``_qa_evidence_value`` above,
    which accepts either a free-text string (the historical shape, kept
    verbatim) or a structured ``{tests, code, note}`` object. The structured
    shape is what turns "which tests cover this, and where in the code is it
    satisfied" from a question re-answered by prose on every visit into a
    field a later run — or ``uncovered_criteria`` below — can read directly.
    """
    test_run = _qa_test_run(data, status)

    design = (
        design_case_repo.get(task.design_case_id) if task.design_case_id else None
    )
    # Source-backed execution tasks have no DESIGN by construction.  Their
    # own acceptance_criteria is the binding QA contract; silently returning
    # empty evidence here would make a completed CR task impossible to
    # substantiate against its own acceptance criteria.
    criteria_text = design.acceptance_criteria if design is not None else task.acceptance_criteria
    criteria = acceptance_criteria.split(criteria_text)
    if not criteria:
        return None, "", test_run

    if design is not None and (data.get("design_number") or "").strip() != design.design_number:
        raise ValidationError(
            f"qa {status} must report design_number={design.design_number} "
            "(the design this task implements)"
        )

    submitted = data.get("criteria_verdicts")
    if not isinstance(submitted, list) or not submitted:
        raise ValidationError(
            f"qa {status} must report criteria_verdicts: one entry per "
            f"acceptance criterion of {design.design_number if design is not None else task.task_number} "
            f"({len(criteria)} of them), each "
            "{criterion, verdict: pass|fail|deviates, evidence}"
        )

    remaining = {}
    for criterion in criteria:
        remaining.setdefault(acceptance_criteria.normalize(criterion), criterion)

    entries = []
    for index, item in enumerate(submitted, start=1):
        if not isinstance(item, dict):
            raise ValidationError(
                f"criteria_verdicts[{index}] must be an object with "
                "criterion, verdict and evidence"
            )
        verdict = (item.get("verdict") or "").strip()
        if verdict not in CRITERION_VERDICTS:
            raise ValidationError(
                f"criteria_verdicts[{index}].verdict is '{verdict}', expected "
                f"one of {list(CRITERION_VERDICTS)}"
            )
        evidence = _qa_evidence_value(item.get("evidence"), index)
        key = acceptance_criteria.normalize(item.get("criterion"))
        if key not in remaining:
            raise ValidationError(
                f"criteria_verdicts[{index}].criterion is not one of "
                f"{design.design_number if design is not None else task.task_number}'s {len(criteria)} binding acceptance criteria "
                "(or it was already submitted in this same call). For a "
                "design-backed task these are the DESIGN criteria; for a "
                "source-backed task they are the TASK criteria and direct "
                "source obligations. New scope must go through an approved "
                "CR source task. Delivery findings belong to stage items. Nothing here is 'claimed' by an "
                "earlier run — every call is matched against the current binding contract."
            )
        entries.append({
            "criterion": remaining.pop(key),
            "verdict": verdict,
            "evidence": evidence,
        })

    if remaining:
        raise ValidationError(
            f"qa {status} left {len(remaining)} of {len(criteria)} "
            f"{design.design_number if design is not None else task.task_number} acceptance criteria unjudged; every "
            "criterion needs a verdict"
        )

    failed = [entry for entry in entries if entry["verdict"] == "fail"]
    deviated = [entry for entry in entries if entry["verdict"] == "deviates"]
    if status == "passed" and failed:
        raise ValidationError(
            f"qa passed contradicts {len(failed)} failing criteria; report "
            "status=needs_fix instead"
        )
    if status == "passed" and deviated:
        raise ValidationError(
            f"qa passed contradicts {len(deviated)} criteria marked deviates "
            "(deviates means the implementation works but does not do what "
            "the design says — a different return shape, error channel, or "
            "condition — which is not something a passing verdict can carry, "
            "even when the divergent path looks unreachable today); report "
            "status=needs_fix instead"
        )
    if status == "needs_fix" and not failed and not deviated:
        raise ValidationError(
            "qa needs_fix requires at least one criterion with verdict=fail "
            "or verdict=deviates"
        )
    return design.design_number if design is not None else None, json.dumps(entries, ensure_ascii=False), test_run


def record_agent_run(task_id, data):
    """Record an AgentRun and apply the loop state machine for terminal statuses.

    Non-terminal statuses (pending/running/cancelled) are recorded without
    transitioning the task.

    ``blocked`` reports that the run could not begin — most often because the
    Harnoq MCP surface was unreachable when the subagent's session started, so
    it holds no design to work from. It is recorded and it clears no gate, but
    it moves nothing: routing it to ``fixing`` like a real ``needs_fix`` sends
    coder back to fix an opinion nobody formed, and spends a qa round on a
    verification that never ran. The task stays where it was so the manager can
    fix the infrastructure and dispatch the same role again.

    A qa ``passed``/``needs_fix`` must carry per-criterion evidence and a
    structured ``test_run`` record — see ``_qa_evidence`` and
    ``_qa_test_run``. Nothing is written when it does not: a rejected run
    leaves no record suggesting verification occurred.
    """
    task = get(task_id)
    role = data.get("role")
    status = data.get("status")
    if role not in AGENT_ROLES:
        raise ValidationError(f"invalid agent role: {role}")
    if status not in AGENT_RUN_STATUSES:
        raise ValidationError(f"invalid agent run status: {status}")
    if status in _TERMINAL_STATUSES and status not in _ROLE_ALLOWED_TERMINAL_STATUSES[role]:
        raise ValidationError(f"invalid terminal status for {role}: {status}")

    if task.status not in _ROLE_ALLOWED_STATES[role]:
        raise ConflictError(
            f"task {task.id} is '{task.status}', expected one of "
            f"{sorted(_ROLE_ALLOWED_STATES[role])}"
        )

    summary = data.get("summary") or ""
    if status == _BLOCKED and not summary.strip():
        raise ValidationError(
            "blocked requires a summary naming what was unreachable — it is the "
            "only record of why this run produced no verdict"
        )

    design_number, criteria_verdicts, test_run = (None, "", "")
    if role == "qa" and status in _QA_VERDICT_STATUSES:
        design_number, criteria_verdicts, test_run = _qa_evidence(task, data, status)

    run = agent_run_repo.create(
        task_id=task_id,
        role=role,
        status=status,
        summary=summary,
        design_number=design_number,
        criteria_verdicts=criteria_verdicts,
        test_run=test_run,
        started_at=data.get("started_at"),
        ended_at=data.get("ended_at"),
    )

    if status == _BLOCKED:
        # No transition: the run is the record, and the timeline surfaces it.
        # blocking_issues carries the reason forward so the quality gate stays
        # shut until it is resolved.
        task.blocking_issues = summary
        return run, _save_transition(task, task.status)

    if status not in _TERMINAL_STATUSES:
        return run, task

    # No status movement: every round happens inside the one in-progress state.
    # What a run changes is the EVIDENCE the quality gate reads — the run row
    # itself, plus blocking_issues below.
    if status == "passed":
        # The problem this role last reported is resolved. Safe for either
        # role: a coder pass cannot open the gate on its own, because the gate
        # additionally requires a qa run newer than the latest coder run.
        task.blocking_issues = ""
    else:
        task.blocking_issues = summary

    return run, _save_transition(task, task.status)


def mark_ready_for_pr(task_id):
    """Explicit quality gate: latest run per role must be passed and no blockers.

    Reading the LATEST run per role is what makes a ``blocked`` qa run close
    this gate rather than merely fail to open it: a run that reached no verdict
    is the current state of that role's verification until a real one replaces
    it, so the gate stays shut until qa is dispatched again and passes.
    """
    task = get(task_id)
    _require_state(task, "in_progress")

    missing = []
    latest = {
        role: agent_run_repo.latest_for_role(task.id, role) for role in AGENT_ROLES
    }
    for role, run in latest.items():
        if run is None or run.status != "passed":
            missing.append(f"passed {role} run")
    # A passing qa run only vouches for the code it actually saw. Once coder
    # has run again — after a withdrawal, or any rework round — the older qa
    # pass is evidence about a previous revision, and reading "latest per role"
    # alone would let it clear the gate for code nobody verified.
    if not missing and latest["qa"].id < latest["coder"].id:
        missing.append(
            "a qa run that judged the current code (the passing qa run "
            "predates the last coder run)"
        )
    if task.blocking_issues.strip():
        missing.append("blocking_issues cleared")

    if missing:
        raise ConflictError("quality gate not met: missing " + ", ".join(missing))
    _require_upstream_satisfied(task, "mark ready for PR")

    previous = task.status
    task.status = "ready_for_pr"
    return _save_transition(task, previous, "质量门通过")


# --- Manual PR record-keeping (docs/cn/design-evolution.md#quality) ---
#
# Harnoq never calls a git-hosting-provider API itself. Agents create branches
# and PRs via the `gh`/`git` CLI (outside Harnoq) and report the resulting
# branch/number/url through link_pr. A human confirms the merge happened on
# the git host; the agent then calls mark_pr_merged to record that fact.
# mark_completed only trusts that manual merged record — there is no polling.


def _current_attempt_pr(task):
    """The PR opened during the task's CURRENT attempt, or None.

    ``pull_request_repo.get_by_task`` returns the newest row, and its docstring
    already says gating asks about "the attempt in flight, not about a PR merged
    before an earlier reopen" — but newest-row cannot deliver that. After a
    reopen the newest row IS the previous attempt's merged PR, right up until
    ``link_pr`` runs again, and every gate reading it during that window is
    answering a question about the wrong attempt.

    Two of them answered wrongly. ``mark_completed`` accepted a PR merged seven
    hours and one reopen earlier as proof that the rework had shipped, and
    recorded a task as complete whose new code was never even branched
    (TASK-2, 2026-07-22). ``withdraw_from_pr`` refused for the mirror-image
    reason — it saw that stale merged PR and concluded the work was already in
    the default branch — which locked withdrawal out of exactly the tasks that
    need it, the ones already reworked once.

    ``claimed_at`` is the boundary: claim is per-attempt and a reopened task
    must be re-claimed before it can move again, so a PR belongs to this
    attempt only if it was linked after the current claim.
    """
    record = pull_request_repo.get_by_task(task.id)
    if record is None:
        return None
    if task.claimed_at and record.created_at and record.created_at <= task.claimed_at:
        return None
    return record


def link_pr(task_id, data):
    """Associate the task's single PR record.

    Only allowed from ``ready_for_pr``, which the task stays in through the
    whole PR phase now (see model docstring — pr_created/ci_* are not
    separate statuses). Re-running this task (after a fix cycle sends it back
    through coding) starts a fresh ``ready_for_pr`` cycle, so this is also
    where a resubmitted PR gets relinked. branch_name/pr_number/pr_url are
    reported by the agent from `gh pr create` output — Harnoq never talks to
    the git host itself.
    """
    task = get(task_id)
    _require_state(task, "ready_for_pr")

    pr_number = data.get("pr_number")
    if pr_number is None:
        raise ValidationError("pr_number is required")

    record = pull_request_repo.link(
        task_id=task.id,
        branch_name=data.get("branch_name") or "",
        pr_number=pr_number,
        pr_url=data.get("pr_url") or "",
        status="pr_created",
    )
    return record, task


def mark_pr_merged(task_id):
    """Record that a human confirmed the linked PR merged on the git host.

    Harnoq never polls or calls the git host — this only persists the
    manually-confirmed outcome so ``mark_completed``'s merged-PR requirement
    can be satisfied.
    """
    task = get(task_id)
    _require_state(task, "ready_for_pr")

    record = _current_attempt_pr(task)
    if record is None:
        raise ConflictError(
            f"task {task.id} has no pull request linked for this attempt; "
            "a PR from an earlier attempt does not count"
        )

    record.status = "merged"
    record.merged_at = utcnow()
    record.save()
    return task, record


def mark_completed(task_id, data=None):
    """Move a ``ready_for_pr`` task with a merged PR to ``completed``.

    The linked PullRequestRecord showing ``merged`` is the source of truth
    (task.status no longer mirrors a separate "merged" state — see model
    docstring). CI is not a per-task gate (see docs/cn/design-evolution.md#quality): coder+qa already
    passed to reach ``ready_for_pr``, and the merged PR is the only remaining
    requirement here.
    """
    task = get(task_id)
    _require_state(task, "ready_for_pr")

    record = _current_attempt_pr(task)
    if record is None or record.status != "merged":
        raise ConflictError(
            f"task {task.id} has no merged pull request for this attempt; "
            "link and merge this attempt's PR before completing it"
        )
    _require_upstream_satisfied(task, "complete")

    previous = task.status
    task.status = "completed"
    saved = _save_transition(task, previous, f"PR #{record.pr_number} 已合并")
    # No downstream wake-up: a dependent task's claimability is computed live
    # from the upstream Feature graph (feature_service.feature_satisfied), so
    # completing this task simply moves its feature closer to satisfied.
    return saved


def withdraw_from_pr(task_id, data=None):
    """Send a ``ready_for_pr`` task back for rework, before anything merged.

    ``ready_for_pr`` was a one-way door: the only exits were forward through
    link_pr/mark_pr_merged/mark_completed. So a problem noticed AFTER the
    quality gate closed — by the human reading the diff, or by anyone who
    looks at the delivery as a whole rather than criterion by criterion — had
    no expressible response. The available moves were to merge work believed
    wrong and then ``reopen_completed`` it, or to leave the task parked
    forever. Both are worse than the honest one.

    Refuses once the PR has merged: the code is in the default branch by then,
    and pretending it is back in rework hides that. Complete the task and use
    ``reopen_completed``, which withdraws the Feature's satisfied status so
    downstream work learns it is standing on something retracted.

    Returns straight to ``in_progress``: the build unit simply is not finished.
    There is no re-entry action, because a coder round inside the unit never
    needed one.
    """
    task = get(task_id)
    _require_state(task, "ready_for_pr")

    reason = ((data or {}).get("reason") or "").strip()
    if not reason:
        raise ValidationError(
            "reason is required: name what the delivery must change, so the "
            "next coder round has something to act on"
        )

    record = _current_attempt_pr(task)
    if record is not None and record.status == "merged":
        raise ConflictError(
            f"task {task.id} has a merged pull request; complete it and use "
            "reopen_completed instead of withdrawing it from the PR phase"
        )

    previous = task.status
    task.status = "in_progress"
    # The gate has to be re-earned from scratch: a fresh qa run must judge every
    # criterion again. Nothing to reset for that — the gate compares run rows,
    # and the old qa run is now older than the coder run that follows.
    task.blocking_issues = reason
    return _save_transition(task, previous, f"撤回待 PR 交付：{reason}")


def timeline(task_id):
    """One chronological history of a task, merged from three stored sources.

    Status transitions (TaskEvent) are only recorded from migration 047
    onward, so a task that ran before it has no status rows. Merging in the
    agent runs and PR records that WERE always stored means such a task still
    shows everything genuinely known about it, rather than looking as if
    nothing happened. Nothing here is reconstructed or guessed — a gap in the
    timeline is a real gap in what was recorded.

    Each entry: kind (status/agent_run/pull_request), at, title, detail.
    """
    task = get(task_id)
    entries = []
    cleared = cleared_failures(task_id)
    untested = {
        acceptance_criteria.normalize(item["criterion"])
        for item in cleared if item["how"] == "no_new_test"
    }
    withdrawn = [
        item for item in cleared if item["how"] == "criterion_removed"
    ]

    for event in task_event_repo.list_by_task(task_id):
        entries.append({
            "kind": "status",
            "at": event.created_at.isoformat() if event.created_at else None,
            "title": (
                f"{event.from_status} → {event.to_status}"
                if event.from_status else event.to_status
            ),
            "detail": event.note,
        })

    runs = agent_run_repo.list_by_task(task_id)
    for run in runs:
        # A qa verdict's coverage belongs in the human's one view of the task:
        # "qa passed" and "qa passed, 13/13 criteria judged" answer different
        # questions, and the second is the one worth asking. Runs from before
        # the evidence gate carry none and say so by staying silent.
        verdicts = json.loads(run.criteria_verdicts or "[]")
        coverage = ""
        if verdicts:
            failed = sum(1 for entry in verdicts if entry.get("verdict") == "fail")
            # deviates gets its own count rather than folding into "passed":
            # it is a design/code divergence, not a clean pass, and a reader
            # scanning only the failed count must not read this run as clean.
            deviated = sum(1 for entry in verdicts if entry.get("verdict") == "deviates")
            coverage_target = run.design_number or task.task_number
            coverage = gettext('{0} {1} 条验收标准逐条判定', coverage_target, len(verdicts))
            if failed:
                coverage += gettext('，{0} 条未通过', failed)
            if deviated:
                coverage += gettext('，{0} 条设计/实现不一致', deviated)
            # Where a repair landed with nothing added to the suite — see
            # uncovered_repairs. Attached to the run that declared the pass,
            # because that is the moment a reader is deciding whether to
            # believe it.
            healed = sum(
                1 for entry in verdicts
                if entry.get("verdict") == "pass"
                and acceptance_criteria.normalize(entry.get("criterion")) in untested
            )
            if healed:
                coverage += (
                    gettext('，其中 {0} 条从未通过转为通过，而测试套件没有新增用例（当初判不过时套件是绿的，说明没有测试覆盖它，现在仍然没有）', healed)
                )
            # The passing run that never had to answer for them: the criteria
            # that failed earlier and left the design before anyone judged them
            # again. Only on the last run, which is the one whose all-pass
            # verdict a reader is weighing.
            if withdrawn and run is runs[-1]:
                coverage += (
                    gettext('；另有 {0} 条先前判不过的判据已不在设计里，本次未被判定——它们是被删掉而不是被解决的', len(withdrawn))
                )
        entries.append({
            "kind": "agent_run",
            "at": run.created_at.isoformat() if run.created_at else None,
            "title": f"{run.role} {run.status}",
            "detail": "；".join(filter(None, [run.summary or "", coverage])),
        })

    for record in pull_request_repo.list_by_task(task_id):
        entries.append({
            "kind": "pull_request",
            "at": record.created_at.isoformat() if record.created_at else None,
            "title": f"PR #{record.pr_number} {record.status}",
            "detail": " ".join(filter(None, [record.branch_name, record.pr_url])),
        })
        if record.merged_at:
            entries.append({
                "kind": "pull_request",
                "at": record.merged_at.isoformat(),
                "title": gettext('PR #{0} 合并', record.pr_number),
                "detail": record.branch_name or "",
            })

    entries.sort(key=lambda entry: entry["at"] or "")
    return entries


def cleared_failures(task_id):
    """qa said these criteria were broken. How did they stop being broken?

    A task that ends with every criterion passing has, somewhere behind it, a
    story for each criterion that once did not. Two of the possible stories
    leave the project no better off than before, and neither is visible in a
    record that shows only "qa needs_fix → coder → qa passed":

    ``no_new_test`` — the criterion was judged ``fail``/``deviates`` while the
    suite was GREEN, which proves no test was watching it (a suite with nothing
    red had nothing that noticed), and a later run judged it ``pass`` without
    the collected count going up, which means none was added. The repair rests
    on one qa agent's reading and nothing will catch it regressing.

    ``criterion_removed`` — the criterion is simply not in the design any more.
    A later qa run passed the task without judging it, and it is absent from the
    design's current acceptance criteria. Deleting the standard also clears the
    failure, and the record of the two events sits in two different objects: the
    verdict on the run, the deletion in the design's version history. A live task
    cleared four criteria this way, all four naming a capability the design was
    simultaneously being rewritten to drop. That rewrite may be intentional,
    but nothing in the task said the failures went away
    because the question was withdrawn rather than answered, and the two readings
    are indistinguishable from the task alone.

    Derived, never stored, and it blocks nothing. The obvious harder version —
    refuse the run until a test appears — buys a number rather than a guarantee,
    because the cheapest way to satisfy it is a test written to match whatever
    the code now does, which is the failure the coder rules already spend a
    paragraph warning against. Naming the criteria leaves the judgement where it
    belongs.

    Only a green suite counts as proof of no coverage. When the suite was red the
    failure might well have been the criterion's own test, and nothing here can
    tell from outside which test failed, so those are left alone rather than
    guessed at.
    """
    runs = [r for r in agent_run_repo.list_by_task(task_id) if r.role == "qa"]
    broken, out = {}, []
    for index, run in enumerate(runs):
        test_run = json.loads(run.test_run) if run.test_run else {}
        collected = test_run.get("collected")
        suite_green = test_run.get("failed") == 0
        for entry in json.loads(run.criteria_verdicts or "[]"):
            criterion = entry.get("criterion") or ""
            key = acceptance_criteria.normalize(criterion)
            verdict = entry.get("verdict")
            if verdict in ("fail", "deviates"):
                if suite_green and collected is not None:
                    broken[key] = {"verdict": verdict, "collected": collected,
                                   "criterion": criterion, "at": index}
                else:
                    # A red suite may well have been red BECAUSE of this
                    # criterion. Unknowable from here, so not asserted.
                    broken.pop(key, None)
            elif verdict == "pass" and key in broken:
                before = broken.pop(key)
                if collected is not None and collected <= before["collected"]:
                    out.append({
                        "criterion": criterion,
                        "was": before["verdict"],
                        "how": "no_new_test",
                        "collected_when_broken": before["collected"],
                        "collected_when_fixed": collected,
                    })

    # Whatever is still in ``broken`` was never judged again. That is only
    # worth reporting once a later qa run has been and gone without asking —
    # while the task is still mid-round the answer is simply not in yet.
    last = len(runs) - 1
    current = set()
    task = get(task_id)
    if task.design_case_id:
        design = design_case_repo.get(task.design_case_id)
        if design is not None:
            current = {
                acceptance_criteria.normalize(c)
                for c in acceptance_criteria.split(design.acceptance_criteria)
            }
    for key, before in broken.items():
        if before["at"] < last and key not in current:
            out.append({
                "criterion": before["criterion"],
                "was": before["verdict"],
                "how": "criterion_removed",
                "collected_when_broken": before["collected"],
                "collected_when_fixed": None,
            })
    return out


def uncovered_criteria(task_id):
    """From the task's most recent qa verdict, the criteria no test covers.

    Derived, never stored. Re-verifying an unchanged design costs qa most of
    its time not in running the suite but in relocating, for every criterion,
    where it is satisfied and what tests watch it — work already done the
    previous round and recorded as prose that could not be reused. Structured
    ``evidence`` (see ``_qa_evidence_value``) makes one part of that answer,
    "does any test cover this at all", a fact this function can just read off
    instead of a question the next qa run has to re-derive from scratch.

    Only the LATEST qa run that reached a verdict is read — a ``blocked`` run
    carries no ``criteria_verdicts`` and is skipped; an earlier round answers
    for a criterion that may have been reworked since. A criterion counts as
    uncovered only when its evidence is the STRUCTURED shape and its
    ``tests`` list is empty: that is qa stating, in a form this can query,
    "nothing automated watches this" — a real and useful signal, not a
    default to apologize for.

    A criterion whose evidence is a plain string (the historical, and still
    accepted, free-text shape) is left OUT of this list entirely rather than
    counted as uncovered. Prose cannot be parsed back into a tests/code
    split without inventing data nobody wrote, so the honest answer for it is
    "cannot tell" — reporting it as zero-coverage would manufacture an alarm
    the record never actually raised. Absent data is not a bad signal; it is
    the absence of one, and the two must not be run together.
    """
    runs = [r for r in agent_run_repo.list_by_task(task_id) if r.role == "qa"]
    verdicted = [r for r in runs if json.loads(r.criteria_verdicts or "[]")]
    if not verdicted:
        return []
    latest = verdicted[-1]
    out = []
    for entry in json.loads(latest.criteria_verdicts or "[]"):
        evidence = entry.get("evidence")
        if isinstance(evidence, dict) and not evidence.get("tests"):
            out.append({
                "criterion": entry.get("criterion"),
                "verdict": entry.get("verdict"),
            })
    return out


def prior_evidence(task_id):
    """Where each of this design's criteria was last found to be satisfied.

    The index, not the answer. Re-verifying a design costs qa most of its time
    in relocating, criterion by criterion, which tests watch it and where in
    the code it lives — and in a live project that relocation was redone from
    scratch every round: several later tasks argued criteria between
    them, 59 of which the change had not touched, and no two rounds wrote the
    same evidence text twice. The answer was already on record each time, as
    prose nobody could query back. Structured ``evidence`` made it queryable;
    this is what hands it forward.

    Keyed by DESIGN and its single design-backed task. Criteria are matched the way the gate
    matches them (``acceptance_criteria.normalize``), so a criterion whose text
    was rewritten simply has no prior entry — correct, since the old locations
    were found against wording that no longer stands.

    WHAT IS DELIBERATELY LEFT OUT: the previous verdict, and the previous
    ``note``. "It passed last time" is the single most anchoring fact available
    and it does nothing for relocation, which is the whole purpose here; prose
    notes carry the same claim in sentence form. A qa run that knows where to
    look still has to decide for itself what it finds, and the risk this trades
    against is real — a live criterion broke while the file its old evidence
    named stayed unchanged and correct, because new code had been placed in
    front of it. Manager still sees the judgement side through
    ``cleared_failures`` and ``uncovered_criteria``, which is the level it
    belongs at.
    """
    task = get(task_id)
    if not task.design_case_id:
        return []
    latest = {}
    for sibling in development_task_repo.list_by_design_case(task.design_case_id):
        for run in agent_run_repo.list_by_task(sibling.id):
            if run.role != "qa":
                continue
            for entry in json.loads(run.criteria_verdicts or "[]"):
                evidence = entry.get("evidence")
                if not isinstance(evidence, dict):
                    # Legacy free text. Not an index — it may name a location or
                    # may just restate the verdict, and there is no way to tell
                    # from here which one it is.
                    continue
                key = acceptance_criteria.normalize(entry.get("criterion"))
                latest[key] = {
                    "criterion": entry.get("criterion"),
                    "tests": evidence.get("tests") or [],
                    "code": evidence.get("code") or [],
                    "last_checked_by": sibling.task_number,
                }
    return list(latest.values())


def list_pull_requests(task_id):
    """Every PR this task has had, oldest first.

    A task can be reopened more than once, so its PRs are a history, not a
    single current value. ``link_pr``/``mark_pr_merged`` only ever return the
    record they just touched, which makes earlier attempts invisible even
    though they are stored — this is the read that surfaces them.

    Deliberately NOT folded into ``DevelopmentTask.to_dict()``: that runs for
    every row of the task list, so joining PRs there would add a query per
    task for data the list does not show.
    """
    get(task_id)  # 404s for an unknown task rather than returning []
    return pull_request_repo.list_by_task(task_id)


def reopen_completed(task_id, data=None):
    """Send a ``completed`` task back for rework.

    The motivating case is integration testing: a task passes its own coder/qa
    cycle, its PR merges, and only later does exercising it against the rest
    of the system expose that it did not actually satisfy its contract. Before
    this, ``completed`` was terminal, so the only recorded options were to
    leave a known-wrong task marked done or to invent an unrelated task.

    A ``reason`` is required. For a design-backed task it cites the unmet DESIGN
    acceptance clause; for a source-backed task it cites the unmet TASK
    acceptance criterion or direct source obligation. Retracting a completion
    is a claim that a recorded fact was wrong, and an unexplained retraction is indistinguishable from a misclick;
    it lands in ``blocking_issues`` so the next attempt's ``start_coding``
    shows the coder what it must address (and clears it).

    The citation is also the test for whether this is the right action at all.
    If a design-backed task can identify its clause, the design was right and
    the task under-delivered. If a source-backed task can identify its criterion
    or source obligation, the same applies. If neither can be identified, this
    is scope growth and belongs to an approved change request and its
    source-backed TASK; Integration findings are batched into one stage repair
    TASK, while System findings remain in their stage run. A DESIGN never
    receives a second live task.

    Two consequences are deliberate rather than smoothed over:

    * The merged PR record is KEPT. The reopened task re-enters the cycle and
      its retry gets a NEW PullRequestRecord (see ``pull_request_repo.link``),
      so "PR #1 merged" stays true -- that merge really happened, and the code
      really is on the main branch.
    * The task's Feature stops being satisfied, because
      ``feature_service.feature_satisfied`` requires every task completed. That
      re-blocks downstream tasks at CLAIM time, which is the point: work built
      on a retracted foundation should not start. It does NOT retract
      downstream tasks that were already claimed -- nothing here can un-write
      code that is already merged, and pretending otherwise would be worse
      than leaving the human to deal with it.

    Returns to ``claimable`` rather than straight to coding: the rework needs a
    fresh claim and pre-review, which is where a human and the manager decide
    whether the failure is a task problem at all. Design-backed tasks may still
    require a re-confirmed design; source-backed tasks revise their own task
    contract and source obligations.
    """
    task = get(task_id)
    _require_state(task, "completed")
    _require_confirmed_design(task, "reopen")

    reason = (data or {}).get("reason") or ""
    if not reason.strip():
        raise ValidationError("reason is required when reopening a completed task")

    previous = task.status
    task.status = "claimable"
    task.assignee = None
    task.claimed_at = None
    task.pre_reviewer = None
    task.pre_reviewed_at = None
    task.pre_review_comment = ""
    task.blocking_issues = reason.strip()
    return _save_transition(task, previous, reason.strip())


def delete(task_id):
    task = get(task_id)
    with database_proxy.atomic():
        change_request_repo.clear_task(task.id)
        return _softdel.delete(development_task_repo, task_id, _LABEL)


def restore(task_id):
    deleted = development_task_repo.get_deleted(task_id)
    if deleted is not None and deleted.source_kind != "design":
        raise ConflictError(
            "a deleted source-backed task cannot be restored after its sources "
            "were released; create a new task from the open sources"
        )
    return _softdel.restore(development_task_repo, task_id, _LABEL)


def purge(task_id):
    suspect_service.forget(suspect_service.DEVELOPMENT_TASK, task_id)
    _softdel.purge(development_task_repo, task_id, _LABEL)
