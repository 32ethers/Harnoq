"""Database access for TaskEvent (append-only, never updated or deleted)."""
from app.models.task_event import TaskEvent


def record(task_id, from_status, to_status, note=""):
    return TaskEvent.create(
        task_id=task_id,
        from_status=from_status or "",
        to_status=to_status,
        note=note or "",
    )


def list_by_task(task_id):
    """Every transition, oldest first."""
    return list(
        TaskEvent.select()
        .where(TaskEvent.task_id == task_id)
        .order_by(TaskEvent.id.asc())
    )


def latest_completed_at_by_task(task_ids):
    """For each of ``task_ids``, the ``created_at`` of its MOST RECENT REAL
    transition into ``completed`` — one query, not one per task.

    "Real" excludes ``from_status == to_status == "completed"`` rows on
    purpose: ``development_task_service.realign_basis`` writes exactly such a
    self-loop event when a completed task's suspect edge is cleared, to
    attribute the judgement without asserting anything changed. If this
    query counted that row, realigning a completed task would push its
    perceived completion time forward with no new work having landed —
    reproducing, inside task_event, the very same false "fresh delivery"
    signal that ``updated_at`` produces (see ``delivery_gate``'s docstring
    for why ``updated_at`` alone is not used).

    A task can carry more than one genuine such row (reopen after
    completion, then complete again later), so this keeps only the latest
    per task rather than the first. Returns a ``{task_id: datetime}`` dict; a
    task with no such row (never completed, or completed before
    ``task_event`` started being recorded) is simply absent — the caller
    decides the fallback.
    """
    if not task_ids:
        return {}
    latest = {}
    rows = (
        TaskEvent.select(TaskEvent.task_id, TaskEvent.created_at)
        .where(
            (TaskEvent.task_id.in_(list(task_ids)))
            & (TaskEvent.to_status == "completed")
            & (TaskEvent.from_status != "completed")
        )
        .order_by(TaskEvent.created_at.asc())
    )
    for row in rows:
        # Ascending order means the last write per task_id wins, i.e. latest.
        latest[row.task_id] = row.created_at
    return latest
