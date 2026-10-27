"""Database access for DevelopmentTask."""
from peewee import JOIN

from app.models.design_case import DesignCase
from app.models.development_task import DevelopmentTask
from app.models.task_dependency import TaskDependency
from app.repositories import soft_delete as _soft
from app.services.numbering import normalize_number


def create(**fields):
    return DevelopmentTask.create(**fields)


def get(task_id):
    return DevelopmentTask.get_or_none(
        (DevelopmentTask.id == task_id) & (DevelopmentTask.deleted_at.is_null())
    )


def get_by_number(project_id, task_number):
    task_number = normalize_number(project_id, task_number)
    return DevelopmentTask.get_or_none(
        (DevelopmentTask.project_id == project_id)
        & (DevelopmentTask.task_number == task_number)
        & (DevelopmentTask.deleted_at.is_null())
    )


def list_by_project(project_id):
    # Select the FK target so task.to_dict() reads design_number off a cached
    # relation instead of a lazy load per task (N+1 in the task list). LEFT
    # join because the FK is nullable.
    return list(
        DevelopmentTask.select(DevelopmentTask, DesignCase)
        .join(
            DesignCase,
            JOIN.LEFT_OUTER,
            on=(DevelopmentTask.design_case == DesignCase.id),
        )
        .where(
            (DevelopmentTask.project_id == project_id)
            & (DevelopmentTask.deleted_at.is_null())
        )
        .order_by(DevelopmentTask.id)
    )


def list_by_design_case(design_case_id):
    return list(
        DevelopmentTask.select()
        .where(
            (DevelopmentTask.design_case_id == design_case_id)
            & (DevelopmentTask.deleted_at.is_null())
        )
        .order_by(DevelopmentTask.id)
    )


def list_by_feature(feature_id):
    """Live (non-deleted) tasks whose design belongs to this feature. Joins
    DesignCase so a task with no design, or one under a deleted / other-feature
    design, is excluded — feature_satisfied reads off this."""
    return list(
        DevelopmentTask.select()
        .join(
            DesignCase,
            JOIN.LEFT_OUTER,
            on=(DevelopmentTask.design_case == DesignCase.id),
        )
        .where(
            ((DevelopmentTask.feature == feature_id) | (
                (DesignCase.feature_id == feature_id)
                & (DesignCase.deleted_at.is_null())
            ))
            & (DevelopmentTask.deleted_at.is_null())
        )
        .order_by(DevelopmentTask.id)
    )


def list_dependencies(task_id):
    return list(
        TaskDependency.select(TaskDependency, DevelopmentTask)
        .join(
            DevelopmentTask,
            on=(TaskDependency.depends_on_task == DevelopmentTask.id),
        )
        .where(TaskDependency.task == task_id)
        .order_by(TaskDependency.id)
    )


def list_dependents(task_id):
    return list(
        TaskDependency.select(TaskDependency, DevelopmentTask)
        .join(DevelopmentTask, on=(TaskDependency.task == DevelopmentTask.id))
        .where(TaskDependency.depends_on_task == task_id)
        .order_by(TaskDependency.id)
    )


def dependency_exists(task_id, depends_on_task_id):
    return TaskDependency.select().where(
        (TaskDependency.task == task_id)
        & (TaskDependency.depends_on_task == depends_on_task_id)
    ).exists()


def add_dependency(task_id, depends_on_task_id):
    return TaskDependency.create(
        task=task_id, depends_on_task=depends_on_task_id
    )


def remove_dependency(task_id, depends_on_task_id):
    return TaskDependency.delete().where(
        (TaskDependency.task == task_id)
        & (TaskDependency.depends_on_task == depends_on_task_id)
    ).execute()


def save(task):
    task.save()
    return task


def soft_delete(task):
    return _soft.soft_delete(task)


def restore(task):
    return _soft.restore(task)


def purge(task):
    _soft.purge(task)


def get_deleted(task_id):
    return _soft.get_deleted(DevelopmentTask, task_id)


def list_deleted_by_project(project_id):
    return _soft.list_deleted_by_project(DevelopmentTask, project_id)
