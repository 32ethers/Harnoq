"""One project-wide Integration or System execution round.

Unlike product tasks, a delivery round is operated by one person on one branch.
Its evidence is deliberately separate from Feature/DESIGN/TASK ownership.
"""
from peewee import CharField, DateTimeField, ForeignKeyField, TextField

from app.core.time import utcnow
from app.db.base import BaseModel, database_proxy
from app.models.project import Project

STAGES = ("integration", "system")
STATUSES = ("active", "blocked", "closed", "abandoned")


class DeliveryStageRun(BaseModel):
    project = ForeignKeyField(Project, backref="delivery_stage_runs", on_delete="CASCADE")
    run_number = CharField(null=True)
    stage = CharField(choices=[(value, value) for value in STAGES])
    status = CharField(default="active", choices=[(value, value) for value in STATUSES])
    branch = CharField(default="")
    code_revision = CharField(default="")
    design_revision = CharField(default="")
    basis_fingerprint = CharField(default="")
    merge_reference = CharField(default="")
    evidence = TextField(default="")
    integration_rerun_evidence = TextField(default="")
    blocked_reason = TextField(default="")
    abandon_reason = TextField(default="")
    closed_at = DateTimeField(null=True)
    created_at = DateTimeField(default=utcnow)

    class Meta:
        database = database_proxy
        table_name = "delivery_stage_run"

    def to_dict(self):
        from app.services import work_owner_service
        return {
            "run_number": self.run_number, "stage": self.stage,
            "status": self.status, "branch": self.branch,
            "code_revision": self.code_revision, "design_revision": self.design_revision,
            "basis_fingerprint": self.basis_fingerprint,
            "merge_reference": self.merge_reference, "evidence": self.evidence,
            "integration_rerun_evidence": self.integration_rerun_evidence,
            "blocked_reason": self.blocked_reason,
            "abandon_reason": self.abandon_reason,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            **work_owner_service.metadata(self),
        }
