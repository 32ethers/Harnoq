"""The durable server audit which closes a System inventory round."""
from peewee import BooleanField, CharField, DateTimeField, ForeignKeyField, IntegerField, TextField

from app.core.time import utcnow
from app.db.base import BaseModel, database_proxy
from app.models.project import Project


class SystemInventoryValidation(BaseModel):
    project = ForeignKeyField(Project, backref="system_inventory_validations", on_delete="CASCADE")
    validation_id = CharField()
    found_in_round = IntegerField()
    basis_fingerprint = CharField()
    manifest_json = TextField(default="{}")
    proposed_items_json = TextField(default="[]")
    created_items_json = TextField(default="{}")
    active = BooleanField(default=True)
    created_at = DateTimeField(default=utcnow)

    class Meta:
        database = database_proxy
        table_name = "system_inventory_validation"
        indexes = ((('project', 'validation_id'), True),)
