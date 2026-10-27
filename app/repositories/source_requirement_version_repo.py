"""Database access for SourceRequirementVersion."""
from app.models.source_requirement import SourceRequirementVersion


def create(**data):
    return SourceRequirementVersion.create(**data)


def get(source_requirement_id, version_number):
    return SourceRequirementVersion.get_or_none(
        (SourceRequirementVersion.source_requirement_id == source_requirement_id)
        & (SourceRequirementVersion.version_number == version_number)
        & (SourceRequirementVersion.deleted_at.is_null())
    )


def list_by_input(source_requirement_id):
    return list(
        SourceRequirementVersion.select()
        .where(
            (SourceRequirementVersion.source_requirement_id == source_requirement_id)
            & (SourceRequirementVersion.deleted_at.is_null())
        )
        .order_by(SourceRequirementVersion.version_number.desc())
    )


def latest_for_input(source_requirement_id):
    return (
        SourceRequirementVersion.select()
        .where(
            (SourceRequirementVersion.source_requirement_id == source_requirement_id)
            & (SourceRequirementVersion.deleted_at.is_null())
        )
        .order_by(SourceRequirementVersion.version_number.desc())
        .first()
    )
