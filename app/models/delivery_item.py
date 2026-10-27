"""A thing that must have an owner before a project can be called delivered.

See docs/cn/design-evolution.md#delivery for the model and its rationale. Splitting work into Feature -> DESIGN-* lets each
build happen without seeing the whole project, and that necessarily leaves the
space BETWEEN designs, and the space ABOVE any single design, unowned. Five
kinds of thing fall in that gap, and they share one lifecycle:

- ``assembly``  -- a part the product's entry point must wire together.
- ``seam``      -- a declared adjacency between two designs that needs its own
  test (or a written reason it does not).
- ``duplicate`` -- the same contract implemented more than once, found by a
  structural scan.
- ``journey``   -- a user-facing scenario nobody's design claims as its own.
- ``nfr``       -- a constitution NFR commitment no design has claimed to verify.

The first three are discovered during the integration stage (scanning code and
each confirmed design's ``design_scope``); the last two are derived during the
system stage (from INPUT-* raw requirements and the constitution's
``nfr_baseline``). None of the five belongs to any single DESIGN-*, which is
exactly why each needs a row here instead of living as a line inside one.

Every item starts ``open`` and ends at exactly one of two honest destinations:
``covered`` (exact executable evidence tied to the active stage run and code
revision) or ``waived`` (a person decided, in writing, not to do it). Nothing
else is a valid rest state — an item that is neither is,
truthfully, still open. The two destination fields are deliberately mandatory
where they apply (see the service layer): a status change with no reason and
no pointer is indistinguishable from having forgotten about it, and this
entity exists to make that forgetting visible instead of silent.
"""
from peewee import CharField, ForeignKeyField, IntegerField, TextField

from app.db.base import SoftDeleteModel, database_proxy
from app.models.project import Project

KINDS = ("assembly", "seam", "duplicate", "journey", "nfr")
STATUSES = ("open", "covered", "waived")


class DeliveryItem(SoftDeleteModel):
    project = ForeignKeyField(Project, backref="delivery_items", on_delete="CASCADE")
    item_number = CharField(null=True)  # P*-INTEG-* / P*-SYS-*
    kind = CharField(choices=[(v, v) for v in KINDS])
    subject = TextField()  # what it is. Multi-line: a journey's ordered steps go here.
    source = TextField(default="")  # one line: where it came from
    # Only meaningful for journey/nfr: how it gets driven (browser automation,
    # a load tool, a subprocess, ...). Left null for assembly/seam/duplicate.
    driver = CharField(null=True)
    status = CharField(choices=[(v, v) for v in STATUSES], default="open")
    # Exact executable command/result for this obligation, owned by the stage
    # run rather than by a development task.
    covered_by = TextField(default="")
    verified_revision = CharField(default="")
    repair_record = TextField(default="")
    blocked_reason = TextField(default="")
    stage_run_number = CharField(default="")
    evidence_command = TextField(default="")
    evidence_exit_code = IntegerField(null=True)
    evidence_output = TextField(default="")
    waive_reason = TextField(default="")
    found_in_round = IntegerField(default=1)

    class Meta:
        database = database_proxy
        table_name = "delivery_item"

    def to_dict(self):
        from app.services import work_owner_service
        return {
            "id": self.id,
            "project_id": self.project_id,
            "item_number": self.item_number,
            "kind": self.kind,
            "subject": self.subject,
            "source": self.source,
            "driver": self.driver,
            "status": self.status,
            "covered_by": self.covered_by,
            "verified_revision": self.verified_revision,
            "repair_record": self.repair_record,
            "blocked_reason": self.blocked_reason,
            "stage_run_number": self.stage_run_number,
            "evidence_command": self.evidence_command,
            "evidence_exit_code": self.evidence_exit_code,
            "evidence_output": self.evidence_output,
            "waive_reason": self.waive_reason,
            "found_in_round": self.found_in_round,
            **work_owner_service.metadata(self),
        }
