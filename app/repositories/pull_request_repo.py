"""Database access for PullRequestRecord (append-only history per task)."""
from app.models.pull_request_record import PullRequestRecord


def get_by_task(task_id):
    """The task's CURRENT PR — the newest row, or None.

    Gating (``mark_pr_merged`` / ``mark_completed``) asks about the attempt in
    flight, not about a PR merged before an earlier reopen, so "current" means
    newest rather than first.
    """
    return (
        PullRequestRecord.select()
        .where(PullRequestRecord.task_id == task_id)
        .order_by(PullRequestRecord.id.desc())
        .first()
    )


def list_by_task(task_id):
    """Every PR the task has had, oldest first — the attempt history."""
    return list(
        PullRequestRecord.select()
        .where(PullRequestRecord.task_id == task_id)
        .order_by(PullRequestRecord.id.asc())
    )


def link(task_id, **fields):
    """Record the task's current PR.

    Updates the newest record while that attempt is still open, so an agent
    correcting a mistyped pr_number does not leave a stray row. Once the
    newest record reached ``merged`` the task must have been reopened since,
    so this is a fresh attempt and gets a new row — overwriting there would
    erase the merge that already happened.

    ``fields`` is expected to be some subset of branch_name/pr_number/pr_url/
    status/merged_at.
    """
    record = get_by_task(task_id)
    if record is None or record.status == "merged":
        return PullRequestRecord.create(task_id=task_id, **fields)
    for key, value in fields.items():
        setattr(record, key, value)
    record.save()
    return record
