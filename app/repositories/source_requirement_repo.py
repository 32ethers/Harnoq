"""Database access for SourceRequirement."""
from app.models.source_requirement import SourceRequirement
from app.repositories import soft_delete as _soft
from app.services.numbering import normalize_number


def create(project_id, title, source_type, raw_content, attachments_json, source, submitted_by):
    return SourceRequirement.create(
        project=project_id,
        title=title,
        source_type=source_type,
        raw_content=raw_content,
        attachments=attachments_json,
        source=source,
        submitted_by=submitted_by,
    )


def get(source_requirement_id):
    return SourceRequirement.get_or_none(
        (SourceRequirement.id == source_requirement_id) & (SourceRequirement.deleted_at.is_null())
    )


def get_by_number(project_id, source_requirement_number):
    source_requirement_number = normalize_number(project_id, source_requirement_number)
    return SourceRequirement.get_or_none(
        (SourceRequirement.project_id == project_id)
        & (SourceRequirement.source_requirement_number == source_requirement_number)
        & (SourceRequirement.deleted_at.is_null())
    )


def list_by_project(project_id):
    return list(
        SourceRequirement.select()
        .where(
            (SourceRequirement.project_id == project_id)
            & (SourceRequirement.deleted_at.is_null())
        )
        .order_by(SourceRequirement.id)
    )


def save(source_requirement):
    source_requirement.save()
    return source_requirement


def soft_delete(source_requirement):
    return _soft.soft_delete(source_requirement)


def restore(source_requirement):
    return _soft.restore(source_requirement)


def purge(source_requirement):
    _soft.purge(source_requirement)


def get_deleted(source_requirement_id):
    return _soft.get_deleted(SourceRequirement, source_requirement_id)


def list_deleted_by_project(project_id):
    return _soft.list_deleted_by_project(SourceRequirement, project_id)
