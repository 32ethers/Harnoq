from peewee import CharField, ForeignKeyField

from app.db.base import TimestampedModel
from app.models.development_task import DevelopmentTask
from app.models.user import User


class TaskOwner(TimestampedModel):
    # A separate relation adds identity without altering existing task tables.
    task = ForeignKeyField(DevelopmentTask, unique=True, backref="ownership", on_delete="CASCADE")
    user = ForeignKeyField(User, backref="task_ownership", on_delete="RESTRICT")

    class Meta:
        table_name = "task_owner"


class TaskWorkEvent(TimestampedModel):
    task = ForeignKeyField(DevelopmentTask, backref="work_events", on_delete="CASCADE")
    actor = ForeignKeyField(User, null=True, on_delete="RESTRICT")
    owner = ForeignKeyField(User, null=True, on_delete="RESTRICT")
    previous_owner = ForeignKeyField(User, null=True, on_delete="RESTRICT")
    action = CharField()

    class Meta:
        table_name = "task_work_event"
