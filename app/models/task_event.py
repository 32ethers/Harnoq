"""TaskEvent — one row per status transition of a DevelopmentTask.

A task's status field only ever holds where it is NOW, and ``updated_at`` only
survives until the next write, so "when did this become ready_for_pr" was
unanswerable: the only durable timestamps were claimed_at and pre_reviewed_at.
This is the append-only record of how a task actually moved, including the
ones that matter most for judging a delivery -- a reopen after completion, a
qa run pushing it back to fixing.

Never updated or deleted. A transition that turned out to be wrong is
corrected by a later transition, not by rewriting this history.
"""
from peewee import CharField, ForeignKeyField, TextField

from app.db.base import TimestampedModel, database_proxy
from app.models.development_task import DevelopmentTask


class TaskEvent(TimestampedModel):
    task = ForeignKeyField(DevelopmentTask, backref="events", on_delete="CASCADE")
    #: Status before the move. Empty for the row that records generation.
    from_status = CharField(default="")
    to_status = CharField()
    #: Why, when the transition carries a human-supplied reason (a reopen's
    #: reason, a pre-review rejection's comment). Empty otherwise.
    note = TextField(default="")

    class Meta:
        database = database_proxy
        table_name = "task_event"

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "from_status": self.from_status,
            "to_status": self.to_status,
            "note": self.note,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
