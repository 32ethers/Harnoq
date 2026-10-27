"""Database access for Project."""
from app.models.project import Project
from app.repositories import soft_delete as _soft


def create(name, description="", repo_url=""):
    return Project.create(name=name, description=description, repo_url=repo_url)


def get(project_id):
    return Project.get_or_none(
        (Project.id == project_id) & (Project.deleted_at.is_null())
    )


def get_by_number(project_number):
    return Project.get_or_none(
        (Project.project_number == project_number) & (Project.deleted_at.is_null())
    )


def list_all():
    return list(
        Project.select().where(Project.deleted_at.is_null()).order_by(Project.id)
    )


def save(project):
    project.save()
    return project


def soft_delete(project):
    return _soft.soft_delete(project)


def restore(project):
    return _soft.restore(project)


def purge(project):
    _soft.purge(project)


def get_deleted(project_id):
    return _soft.get_deleted(Project, project_id)


def list_deleted():
    return list(
        Project.select()
        .where(Project.deleted_at.is_null(False))
        .order_by(Project.id)
    )
