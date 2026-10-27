"""A server-issued receipt binding a validated Round-1 batch to INTEG creates."""

from peewee import (
    BooleanField,
    CharField,
    DateTimeField,
    ForeignKeyField,
    IntegerField,
    TextField,
)

from app.core.time import utcnow
from app.db.base import BaseModel, database_proxy
from app.models.project import Project


class IntegrationInventoryValidation(BaseModel):
    project = ForeignKeyField(
        Project, backref="integration_inventory_validations", on_delete="CASCADE"
    )
    validation_id = CharField()
    found_in_round = IntegerField()
    proposed_items_json = TextField(default="[]")
    created_items_json = TextField(default="{}")
    active = BooleanField(default=True)
    created_at = DateTimeField(default=utcnow)

    class Meta:
        database = database_proxy
        table_name = "integration_inventory_validation"
        indexes = ((("project", "validation_id"), True),)
