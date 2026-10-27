"""SkillInvocation — a record of one OpenWebUI skill call.

OpenWebUI skills are the only controlled write entry point for the design-time
AI. Every skill call (read or write, succeeded/failed/rejected) leaves one row
here so the design discussion stays traceable. Phase 1 stores summaries only,
not full conversation context (per detailed_design "SkillInvocation" section).
"""
from peewee import (
    CharField,
    DateTimeField,
    ForeignKeyField,
    IntegerField,
    TextField,
)

from app.core.time import utcnow
from app.db.base import BaseModel, database_proxy
from app.models.project import Project

SKILL_INVOCATION_STATUSES = ("succeeded", "failed")


class SkillInvocation(BaseModel):
    project = ForeignKeyField(Project, backref="skill_invocations", on_delete="CASCADE")
    skill_name = CharField()
    actor = CharField()
    session_id = CharField()
    idempotency_key = CharField(default="")
    target_type = CharField(default="")
    target_id = IntegerField(null=True)
    request_summary = TextField(default="")
    response_summary = TextField(default="")
    status = CharField(choices=[(v, v) for v in SKILL_INVOCATION_STATUSES])
    created_at = DateTimeField(default=utcnow)

    class Meta:
        database = database_proxy
        table_name = "skill_invocation"

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "skill_name": self.skill_name,
            "actor": self.actor,
            "session_id": self.session_id,
            "idempotency_key": self.idempotency_key,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "request_summary": self.request_summary,
            "response_summary": self.response_summary,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
