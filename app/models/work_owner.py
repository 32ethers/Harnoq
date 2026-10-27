from peewee import CharField, Check, ForeignKeyField

from app.db.base import TimestampedModel
from app.models.delivery_item import DeliveryItem
from app.models.delivery_stage_run import DeliveryStageRun
from app.models.source_requirement import SourceRequirement
from app.models.user import User

_ONE_TARGET = Check("(CASE WHEN source_requirement_id IS NULL THEN 0 ELSE 1 END + CASE WHEN delivery_item_id IS NULL THEN 0 ELSE 1 END + CASE WHEN stage_run_id IS NULL THEN 0 ELSE 1 END) = 1")


class WorkOwner(TimestampedModel):
    source_requirement = ForeignKeyField(SourceRequirement, null=True, unique=True, on_delete="CASCADE")
    delivery_item = ForeignKeyField(DeliveryItem, null=True, unique=True, on_delete="CASCADE")
    stage_run = ForeignKeyField(DeliveryStageRun, null=True, unique=True, on_delete="CASCADE")
    user = ForeignKeyField(User, on_delete="RESTRICT")

    class Meta:
        table_name = "work_owner"
        constraints = [_ONE_TARGET]


class WorkAssignment(TimestampedModel):
    source_requirement = ForeignKeyField(SourceRequirement, null=True, on_delete="CASCADE")
    delivery_item = ForeignKeyField(DeliveryItem, null=True, on_delete="CASCADE")
    stage_run = ForeignKeyField(DeliveryStageRun, null=True, on_delete="CASCADE")
    actor = ForeignKeyField(User, null=True, on_delete="RESTRICT")
    owner = ForeignKeyField(User, null=True, on_delete="RESTRICT")
    previous_owner = ForeignKeyField(User, null=True, on_delete="RESTRICT")
    action = CharField()

    class Meta:
        table_name = "work_assignment"
        constraints = [_ONE_TARGET]
