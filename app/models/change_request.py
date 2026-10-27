"""ChangeRequest (CR-*): the governance record for changing settled work.

Editing a draft needs no ceremony — you just edit it. A ChangeRequest exists for
the case where something DOWNSTREAM has already been built on the thing you want
to change, so the question "what does this break, and is it worth it" has to be
asked before the edit, not discovered after (see docs/cn/design-evolution.md#changes).

It is deliberately NOT an editing engine. Applying a change request does not
rewrite any object: a human or an agent still authors the new wording through
the normal object APIs. What the record does is make the intent, the analysed
blast radius and the approval explicit and traceable.

The five states are the change workflow, and each earns its place:

    proposed → analyzing → approved → applied → closed

- ``proposed`` — somebody wants a change; the reason is written down.
- ``analyzing`` — the downstream chain has been walked; ``impact`` holds it.
  Separate from proposed because the analysis is the work, and a proposal
  nobody analysed must not look like one that was reviewed.
- ``approved`` — a human accepted that impact. This records approval before
  the content edit is made through its normal API.
- ``applied`` — the change was actually made upstream; ``applied_at`` records
  when. Downstream suspect edges light up on their own from the real content edit — the
  governance half and the staleness half are deliberately decoupled.
- ``closed`` — terminal. ``resolution`` says which ending it was, because
  "closed after applying" and "closed because we decided not to" are the two
  facts anyone reading history actually needs to tell apart.

There is no version history: a ChangeRequest is a record OF a change, and its
own status trail is its history. Content-bearing objects (INPUT/FEATURE/DESIGN)
are the ones that need snapshots.
"""
import json

from peewee import (
    CharField,
    DateTimeField,
    ForeignKeyField,
    IntegerField,
    TextField,
)

from app.db.base import SoftDeleteModel, database_proxy
from app.models.project import Project
from app.models.development_task import DevelopmentTask

PROPOSED = "proposed"
ANALYZING = "analyzing"
APPROVED = "approved"
APPLIED = "applied"
CLOSED = "closed"

CHANGE_REQUEST_STATUSES = (PROPOSED, ANALYZING, APPROVED, APPLIED, CLOSED)

#: Forward-only, one step at a time, except that anything not yet applied can
#: be closed (dropped) and an applied one can be closed (done).
CHANGE_REQUEST_TRANSITIONS = {
    PROPOSED: {ANALYZING, CLOSED},
    ANALYZING: {APPROVED, CLOSED},
    APPROVED: {APPLIED, CLOSED},
    APPLIED: {CLOSED},
    CLOSED: set(),
}

#: What a change request can point at. ``new`` is a request for something that
#: does not exist yet (a new requirement) — an increment, which per the change policy is the
#: DEFAULT answer once a task has shipped: add, do not rewrite.
#:
#: ``development_task`` is for a change to work already DELIVERED that alters
#: no specification — the merged code needs fixing but the design still reads
#: correctly. Without it such a request had to be filed against a design it
#: never touched, which is what happened in live use: the record pointed at a
#: DESIGN-* whose version never moved, and its own resolution said the design
#: was unaffected. A target the change does not change is a false trail.
CHANGE_TARGET_TYPES = ("design_case", "development_task", "new")


def _loads(raw, fallback):
    if not raw:
        return fallback
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return fallback


class ChangeRequest(SoftDeleteModel):
    project = ForeignKeyField(Project, backref="change_requests", on_delete="CASCADE")
    change_request_number = CharField(null=True)  # per-project, e.g. CR-3
    target_type = CharField(choices=[(v, v) for v in CHANGE_TARGET_TYPES])
    #: null when target_type is ``new`` — there is nothing to point at yet.
    target_id = IntegerField(null=True)
    reason = TextField(default="")
    status = CharField(default=PROPOSED, choices=[(v, v) for v in CHANGE_REQUEST_STATUSES])
    #: Every OTHER object this same change also rewrites, as a JSON list of
    #: ``{target_type, target_id}``.
    #:
    #: One target was never enough. A single decision routinely lands in two
    #: designs plus the constitution, and ``impact`` is DERIVED from targets —
    #: so an unrecorded second target does not merely go unmentioned, it makes
    #: that design's tasks invisible to the analysis. Live case: a request
    #: revised two designs, recorded one, and the second design's task — by
    #: then already completed — never appeared in the blast radius at all.
    also_changes = TextField(default="[]")
    #: The version each target read at when this request was WRITTEN, as a
    #: JSON list of ``{target_type, target_id, version_number}``.
    #:
    #: ``apply`` means "the change was actually made upstream" — and nothing
    #: checked that. A live request named a DESIGN-* as its target, was applied
    #: and closed, and that design's version never moved once (its own
    #: resolution admitted the spec was unaffected). Comparing these recorded
    #: versions against today's makes the claim falsifiable instead of
    #: ceremonial. Targets with no version model (a delivered task) contribute
    #: nothing and are reported as unverifiable rather than as unchanged.
    target_versions = TextField(default="[]")
    #: JSON list of downstream objects found by the analysis, each
    #: ``{object_type, object_id, object_number, status}``. Derived over the
    #: target AND ``also_changes``; never authored by hand.
    impact = TextField(default="[]")
    applied_at = DateTimeField(null=True)
    resolution = TextField(default="")
    # An approved CR can enter the execution queue directly.  Nullable keeps
    # historical governance records readable until the assignment migration.
    task = ForeignKeyField(
        DevelopmentTask, null=True, backref="change_requests", on_delete="SET NULL"
    )

    class Meta:
        database = database_proxy
        table_name = "change_request"
        indexes = ((("project", "change_request_number"), True),)

    def to_dict(self, *, target_number=None):
        return {
            "id": self.id,
            "change_request_number": self.change_request_number,
            "project_id": self.project_id,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "target_number": target_number,
            "also_changes": _loads(self.also_changes, []),
            "target_versions": _loads(self.target_versions, []),
            "reason": self.reason,
            "status": self.status,
            "impact": _loads(self.impact, []),
            "applied_at": self.applied_at.isoformat() if self.applied_at else None,
            "resolution": self.resolution,
            "task_number": (
                self.task.task_number if self.task_id and self.task is not None else None
            ),
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
