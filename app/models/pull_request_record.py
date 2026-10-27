"""PullRequestRecord — a task's pull requests, append-only, newest last.

A task has one PR per attempt, not one PR for all time: a completed task can
be sent back for rework, and that second attempt gets its own PR. Keeping the
earlier rows means "PR #1 was merged on 2026-07-17" survives the reopen
instead of being overwritten by the retry (migration 046 lifted the unique
index that used to force a single row).

Gating decisions read the LATEST record (``get_by_task``); the older ones are
history. Nothing enforces a cap on attempts.
"""
from peewee import CharField, DateTimeField, ForeignKeyField, IntegerField

from app.db.base import TimestampedModel, database_proxy
from app.models.development_task import DevelopmentTask

#: The two things a human can tell Harnoq about a PR. ``none``/
#: ``branch_created``/``closed`` were written by nothing: they are states you
#: can only learn by WATCHING a git host, and Harnoq is deliberately not a git
#: host client (see README, 范围边界) — a record is created when someone
#: reports the PR exists and updated when someone reports it merged.
PULL_REQUEST_STATUSES = ("pr_created", "merged")


class PullRequestRecord(TimestampedModel):
    task = ForeignKeyField(
        DevelopmentTask, backref="pull_request_records", on_delete="CASCADE"
    )
    branch_name = CharField(default="")
    pr_number = IntegerField(null=True)
    pr_url = CharField(default="")
    status = CharField(
        default="pr_created", choices=[(v, v) for v in PULL_REQUEST_STATUSES]
    )
    merged_at = DateTimeField(null=True)

    class Meta:
        database = database_proxy
        table_name = "pull_request_record"

    def to_dict(self):
        return {
            "id": self.id,
            "task_id": self.task_id,
            "branch_name": self.branch_name,
            "pr_number": self.pr_number,
            "pr_url": self.pr_url,
            "status": self.status,
            "merged_at": self.merged_at.isoformat() if self.merged_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
