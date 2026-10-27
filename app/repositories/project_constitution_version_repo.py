"""Database access for ProjectConstitutionVersion."""
from app.models.project_constitution import ProjectConstitutionVersion


def create(**data):
    return ProjectConstitutionVersion.create(**data)


def get(constitution_id, version_number):
    return ProjectConstitutionVersion.get_or_none(
        (ProjectConstitutionVersion.project_constitution_id == constitution_id)
        & (ProjectConstitutionVersion.version_number == version_number)
        & (ProjectConstitutionVersion.deleted_at.is_null())
    )


def list_by_constitution(constitution_id):
    return list(
        ProjectConstitutionVersion.select()
        .where(
            (ProjectConstitutionVersion.project_constitution_id == constitution_id)
            & (ProjectConstitutionVersion.deleted_at.is_null())
        )
        .order_by(ProjectConstitutionVersion.version_number.desc())
    )


def latest_for_constitution(constitution_id):
    return (
        ProjectConstitutionVersion.select()
        .where(
            (ProjectConstitutionVersion.project_constitution_id == constitution_id)
            & (ProjectConstitutionVersion.deleted_at.is_null())
        )
        .order_by(ProjectConstitutionVersion.version_number.desc())
        .first()
    )
