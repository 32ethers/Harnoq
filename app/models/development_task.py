"""DevelopmentTask — the real execution unit.

DevelopmentTask carries a claim -> pre-review -> coding/qa loop ->
PR/CI -> completed lifecycle. Each state has a distinct meaning tied to a
dedicated action (claim, start-pre-review, approve/reject-pre-review,
start-coding, mark-ready-for-pr, ...); unlike REQ/DESIGN/PLAN this was never
a confirm-lock pattern, so it keeps its own vocabulary rather than the shared
draft/needs_clarification/confirmed lifecycle.

What WAS removed (state-machine redesign, same principle as REQ's old mirror
states): ``pr_created``/``ci_running``/``ci_failed``/``ci_passed``/``merged``
duplicated data that already lived in PullRequestRecord.status — the task now
just stays ``ready_for_pr`` through the whole PR phase, and the
PullRequestRecord is the source of truth for "was it merged" (see
link_pr/mark_pr_merged/mark_completed in development_task_service).
``cancelled`` was never written anywhere — dropping something is deletion
(recycle bin), not a status, matching every other entity in this redesign.

``blocked`` is gone too: a generated task is stored as ``claimable`` and
whether it can actually be claimed is computed live from the upstream
Feature graph (see app/services/feature_service.feature_satisfied), never
stored. Cross-task ordering is represented by the small TaskDependency graph;
feature dependencies still gate claimability across feature boundaries.

The removal of the CI gate: ``ci_passed``/``ci_exemption_reason`` were dropped. CI is
project-level infrastructure, not a per-task completion gate — a task is
"done" once coder+qa have passed and its PR is merged (see mark_completed /
mark_pr_merged in development_task_service).

``coding``/``agent_qa``/``fixing`` collapsed into ``in_progress``. From claim
to merge, one manager session owns the task as a single processing unit; how
many coder and qa rounds happen inside it is that session's business. Those
three states modelled exactly that churn and nothing else — their only job was
to say which role could record a run next — while the information they encoded
("how many rounds, in what order") was already stored, more precisely, as the
AgentRun rows the timeline renders. The gates that actually decide anything are
all outside the loop and none of them read these states: human pre-review
approval, the quality gate (latest coder and qa both passed, the qa run newer
than the coder run, per-criterion evidence present), and a merged PR. A task
whose unit does not finish simply never merges, which needs no state of its
own.

``pre_review_status``/``pre_review_decision`` are gone for that same reason
(migration 052): both moved in lockstep with ``status`` on every transition and
nothing ever read them back.

``rejection_target`` and ``rejected_to_task_planning`` went the same way
(migration 054). The target's five values only ever chose between two statuses,
and the four non-design ones routed into a branch NOTHING could transition out
of — the exact dead end ``reopen_rejected_to_design`` was written to fix, left
unfixed on the other side. Pre-review rejection now has one destination:
``needs_revision``. Design-backed tasks revise and re-confirm their DESIGN;
source-backed tasks revise their TASK contract and source obligations. A task
whose wording alone is wrong is edited, not routed to a second planning object.
"""
from peewee import (
    CharField,
    DateTimeField,
    ForeignKeyField,
    TextField,
)

from app.db.base import SoftDeleteModel, database_proxy
from app.models.design_case import DesignCase
from app.models.feature import Feature
from app.models.project import Project
from app.models.task_enums import (
    TASK_COMPLEXITIES,
    TASK_PRIORITIES,
    TASK_RISK_LEVELS,
    TASK_SOURCE_TYPES,
    TASK_TYPES,
    TASK_SOURCE_CATEGORIES,
)

DEVELOPMENT_TASK_STATUSES = (
    "claimable",
    "claimed",
    "pre_reviewing",
    "rejected_to_design",  # legacy spelling; new writes use needs_revision
    "needs_revision",
    "ready_for_development",
    "in_progress",
    "ready_for_pr",
    "completed",
)


class DevelopmentTask(SoftDeleteModel):
    project = ForeignKeyField(Project, backref="development_tasks", on_delete="CASCADE")
    task_number = CharField(null=True)  # external id, per-project e.g. TASK-7
    title = CharField(null=False)
    description = TextField(default="")
    task_type = CharField(choices=[(v, v) for v in TASK_TYPES])
    priority = CharField(default="normal", choices=[(v, v) for v in TASK_PRIORITIES])
    complexity = CharField(default="medium", choices=[(v, v) for v in TASK_COMPLEXITIES])
    risk_level = CharField(default="low", choices=[(v, v) for v in TASK_RISK_LEVELS])
    source_type = CharField(
        default="requirement", choices=[(v, v) for v in TASK_SOURCE_TYPES]
    )
    # Source ownership: confirmed design or approved change request.
    source_kind = CharField(
        default="design", choices=[(v, v) for v in TASK_SOURCE_CATEGORIES]
    )
    # CR tasks have an explicit Feature owner.
    feature = ForeignKeyField(
        Feature, null=True, backref="development_tasks", on_delete="SET NULL"
    )
    design_case = ForeignKeyField(
        DesignCase, null=True, backref="development_tasks", on_delete="SET NULL"
    )
    status = CharField(
        default="claimable", choices=[(v, v) for v in DEVELOPMENT_TASK_STATUSES]
    )
    assignee = CharField(null=True)
    claimed_at = DateTimeField(null=True)
    # Pre-development review (populated in P1.4). Who / when / why only: the
    # outcome is the status itself (see migration 052).
    pre_reviewer = CharField(null=True)
    pre_reviewed_at = DateTimeField(null=True)
    pre_review_comment = TextField(default="")
    # Task contract.
    acceptance_criteria = TextField(default="")
    constraints = TextField(default="")
    non_goals = TextField(default="")
    #: Why the quality gate is shut, in prose. Content, not a cached verdict —
    #: the gate itself reads the AgentRun rows (see migration 053).
    blocking_issues = TextField(default="")

    class Meta:
        database = database_proxy
        table_name = "development_task"
        indexes = (
            (("project", "task_number"), True),
        )

    def to_dict(self):
        from app.services import task_owner_service
        assigned_user = task_owner_service.identity(self.id)
        return {
            "id": self.id,
            "project_id": self.project_id,
            "task_number": self.task_number,
            "title": self.title,
            "description": self.description,
            "task_type": self.task_type,
            "priority": self.priority,
            "complexity": self.complexity,
            "risk_level": self.risk_level,
            "source_kind": self.source_kind,
            "feature_number": (
                self.feature.feature_number
                if self.feature_id and self.feature is not None
                else None
            ),
            "design_case_id": self.design_case_id,
            "design_number": (
                self.design_case.design_number
                if self.design_case_id and self.design_case is not None
                else None
            ),
            "status": self.status,
            "assignee": assigned_user["display_name"] if assigned_user else self.assignee,
            "assignee_number": assigned_user["user_number"] if assigned_user else None,
            "assignee_user": assigned_user,
            "claimed_at": self.claimed_at.isoformat() if self.claimed_at else None,
            "pre_reviewer": self.pre_reviewer,
            "pre_reviewed_at": self.pre_reviewed_at.isoformat() if self.pre_reviewed_at else None,
            "pre_review_comment": self.pre_review_comment,
            "acceptance_criteria": self.acceptance_criteria,
            "constraints": self.constraints,
            "non_goals": self.non_goals,
            "blocking_issues": self.blocking_issues,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }
