from peewee import SqliteDatabase

from app.core.errors import ApiError, ConflictError, ValidationError
from app.db.base import database_proxy
from app.models.development_task import DevelopmentTask
from app.models.task_owner import TaskOwner, TaskWorkEvent
from app.models.task_event import TaskEvent
from app.models.user import User
from app.services import auth_service, user_service


def owner(task_id):
    return TaskOwner.get_or_none(TaskOwner.task == task_id)


def identity(task_id):
    row = owner(task_id)
    return row.user.to_dict() if row else None


def _event(task, action, user=None, previous=None):
    TaskWorkEvent.create(task=task, actor=auth_service.current_user(), owner=user,
                         previous_owner=previous, action=action)


def bind(task, user):
    previous = owner(task.id)
    if previous and previous.user_id == user.id:
        return
    TaskOwner.insert(task=task, user=user).on_conflict(
        conflict_target=[TaskOwner.task], update={TaskOwner.user: user.id}
    ).execute()
    _event(task, "assigned", user, previous.user if previous else None)


def assign(task, number):
    auth_service.require_admin()
    with database_proxy.atomic():
        previous = owner(task.id)
        if task.status == "completed" and previous and previous.user.user_number != number:
            raise ConflictError("completed work cannot be reassigned")
        if number is None:
            if task.status not in ("claimable", "needs_revision"):
                raise ConflictError("release an active claim before clearing its owner")
            clear(task)
            task.assignee = None
        else:
            user = user_service.get(number)
            if not user.is_active:
                raise ValidationError("cannot assign work to an inactive user")
            if task.status in ("claimed", "pre_reviewing", "ready_for_development") and (previous is None or previous.user_id != user.id):
                from app.services import development_task_service
                # Transfer before coding must re-earn the new holder's admission/review.
                task = development_task_service.release(task.id)
            bind(task, user)
            task.assignee = user.display_name
        task.save()
    return task


def claim_identity(task):
    user = auth_service.current_user()
    if user is None:
        return None
    existing = owner(task.id)
    if existing and existing.user_id != user.id:
        if user.role != "admin":
            raise ApiError("task is assigned to another user", 403)
        # An administrator can advance the assigned person's task on their behalf.
        user = existing.user
    if not user.is_active or user.deleted_at is not None:
        raise ConflictError("task owner is inactive; reassign the task first")
    bind(task, user)
    return user


def clear(task):
    previous = owner(task.id)
    if previous:
        _event(task, "unassigned", previous=previous.user)
        previous.delete_instance()


def record_transition(task, previous_status):
    row = owner(task.id)
    if task.status == "completed" and previous_status != "completed":
        _event(task, "completed", row.user if row else None)
    elif previous_status == "completed" and task.status != "completed":
        _event(task, "reopened", row.user if row else None)
        clear(task)
    elif task.status == "claimable" and previous_status in ("claimed", "pre_reviewing", "ready_for_development"):
        clear(task)


def check_mutation(task):
    user = auth_service.current_user()
    if user is None or user.role == "admin":
        return
    row = owner(task.id)
    if row and row.user_id != user.id:
        raise ApiError("only the task owner or an administrator can modify this task", 403)
    if not row and task.status != "claimable":
        raise ApiError("a legacy task must be assigned to a user by an administrator", 403)


def link_legacy_tasks(user_number, *, project_id=None):
    # Explicit operator attribution only; existing user ownership/history is never inferred or overwritten.
    transaction = database_proxy.atomic("IMMEDIATE") if isinstance(database_proxy.obj, SqliteDatabase) else database_proxy.atomic()
    with transaction:
        user = user_service.get(user_number)
        if not user.is_active:
            raise ValidationError("cannot link historical work to an inactive user")
        tracked = TaskWorkEvent.select(TaskWorkEvent.task).where(
            TaskWorkEvent.owner.is_null(False) | TaskWorkEvent.previous_owner.is_null(False)
        )
        tasks = DevelopmentTask.select().where(
            ~DevelopmentTask.id.in_(TaskOwner.select(TaskOwner.task))
            & ~DevelopmentTask.id.in_(tracked)
        ).order_by(DevelopmentTask.id)
        if project_id is not None:
            tasks = tasks.where(DevelopmentTask.project_id == project_id)
        if not isinstance(database_proxy.obj, SqliteDatabase):
            tasks = tasks.for_update()
        linked, completions = 0, 0
        for task in tasks:
            TaskOwner.create(task=task, user=user)
            _event(task, "legacy_assigned", user)
            # Keep source timestamps and unknown historical operators; migration is not a new completion.
            for event in TaskEvent.select().where(
                (TaskEvent.task == task) & (TaskEvent.to_status == "completed")
            ).order_by(TaskEvent.id):
                TaskWorkEvent.create(task=task, owner=user, action="completed",
                                     created_at=event.created_at, updated_at=event.created_at)
                completions += 1
            linked += 1
    return {"user_number": user.user_number, "linked_tasks": linked, "completion_events": completions}


def workload(*, project_id=None, since=None, until=None):
    tasks = (TaskOwner.select(TaskOwner, DevelopmentTask, User)
             .join(DevelopmentTask).switch(TaskOwner).join(User)
             .where(DevelopmentTask.deleted_at.is_null()))
    events = TaskWorkEvent.select().where(TaskWorkEvent.action == "completed")
    if project_id is not None:
        tasks = tasks.where(DevelopmentTask.project_id == project_id)
        events = events.join(DevelopmentTask).where(DevelopmentTask.project_id == project_id)
    if since:
        events = events.where(TaskWorkEvent.created_at >= since)
    if until:
        events = events.where(TaskWorkEvent.created_at < until)
    result = {}
    for user in User.select().order_by(User.id):
        result[user.id] = user.to_dict() | {"pending": 0, "active": 0, "completed": 0,
                                          "completed_in_period": 0, "complexity": {}, "tasks": []}
    for row in tasks:
        item, task = result[row.user_id], row.task
        key = "completed" if task.status == "completed" else "pending" if task.status in ("claimable", "needs_revision", "rejected_to_design") else "active"
        item[key] += 1
        item["complexity"][task.complexity] = item["complexity"].get(task.complexity, 0) + 1
        item["tasks"].append({"id": task.id, "task_number": task.task_number, "title": task.title,
                              "status": task.status, "complexity": task.complexity, "project_number": task.project.project_number})
    completed = {}
    for event in events:
        if event.owner_id:
            completed.setdefault(event.owner_id, set()).add(event.task_id)
    for user_id, task_ids in completed.items():
        result[user_id]["completed_in_period"] = len(task_ids)
    # Unlinked historical names are deliberately excluded rather than guessed.
    unlinked = DevelopmentTask.select().where(DevelopmentTask.deleted_at.is_null() & ~DevelopmentTask.id.in_(TaskOwner.select(TaskOwner.task)))
    if project_id is not None:
        unlinked = unlinked.where(DevelopmentTask.project_id == project_id)
    return {"users": list(result.values()), "unassigned_tasks": unlinked.count(),
            "completion_basis": "distinct task completions recorded for the owner at completion; elapsed time is not work hours"}
