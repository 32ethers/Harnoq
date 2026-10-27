"""Database access for ProjectConstitution (1:1 with Project)."""
from app.models.project_constitution import ProjectConstitution
from app.repositories import soft_delete as _soft


def create(project_id, **fields):
    return ProjectConstitution.create(project=project_id, **fields)


def get(constitution_id):
    return ProjectConstitution.get_or_none(
        (ProjectConstitution.id == constitution_id)
        & ProjectConstitution.deleted_at.is_null()
    )


def get_for_project(project_id):
    return ProjectConstitution.get_or_none(
        (ProjectConstitution.project_id == project_id)
        & ProjectConstitution.deleted_at.is_null()
    )


def save(constitution):
    constitution.save()
    return constitution


def soft_delete(constitution):
    return _soft.soft_delete(constitution)


def restore(constitution):
    return _soft.restore(constitution)


def purge(constitution):
    _soft.purge(constitution)
