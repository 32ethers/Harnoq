"""Current-baseline completion evidence for an Integration inventory pass."""
from peewee import BooleanField, CharField, DateTimeField, ForeignKeyField, IntegerField, TextField

from app.core.time import utcnow
from app.db.base import BaseModel, database_proxy
from app.models.project import Project


class IntegrationCompletionAudit(BaseModel):
    project = ForeignKeyField(Project, backref="integration_completion_audits", on_delete="CASCADE")
    validation_id = CharField()
    found_in_round = IntegerField()
    basis_fingerprint = CharField()
    manifest_json = TextField(default="{}")
    proposed_items_json = TextField(default="[]")
    created_items_json = TextField(default="{}")
    active = BooleanField(default=True)
    completed_at = DateTimeField(null=True)
    created_at = DateTimeField(default=utcnow)

    class Meta:
        database = database_proxy
        table_name = "integration_completion_audit"
        indexes = ((('project', 'validation_id'), True),)
