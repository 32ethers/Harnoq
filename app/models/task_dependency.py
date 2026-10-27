"""Ordering edges between DevelopmentTask execution units."""

from peewee import ForeignKeyField

from app.db.base import TimestampedModel, database_proxy
from app.models.development_task import DevelopmentTask


class TaskDependency(TimestampedModel):
    task = ForeignKeyField(
        DevelopmentTask, backref="dependencies", on_delete="CASCADE"
    )
    depends_on_task = ForeignKeyField(
        DevelopmentTask, backref="dependents", on_delete="CASCADE"
    )

    class Meta:
        database = database_proxy
        table_name = "task_dependency"
        indexes = ((('task', 'depends_on_task'), True),)

    def to_dict(self):
        return {
            "task_number": self.task.task_number if self.task is not None else None,
            "depends_on_task_number": (
                self.depends_on_task.task_number
                if self.depends_on_task is not None
                else None
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
