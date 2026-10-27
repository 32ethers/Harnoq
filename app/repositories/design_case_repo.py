"""Database access for DesignCase."""
from app.models.design_case import DesignCase
from app.repositories import soft_delete as _soft
from app.services.numbering import normalize_number


def create(project_id, **fields):
    return DesignCase.create(project=project_id, **fields)


def get(design_case_id):
    return DesignCase.get_or_none(
        (DesignCase.id == design_case_id) & (DesignCase.deleted_at.is_null())
    )


def get_including_deleted(design_case_id):
    """Fetch regardless of soft-delete state. A task's Feature affiliation is
    a structural fact recorded at generation time; it must not disappear just
    because the design was later soft-deleted (see
    feature_service._feature_id_of_task — used by the claim-time gate, which
    must keep enforcing dependency order even for tasks whose design row is
    now in the recycle bin)."""
    return DesignCase.get_or_none(DesignCase.id == design_case_id)


def get_by_number(project_id, design_number):
    design_number = normalize_number(project_id, design_number)
    return DesignCase.get_or_none(
        (DesignCase.project_id == project_id)
        & (DesignCase.design_number == design_number)
        & (DesignCase.deleted_at.is_null())
    )


def list_by_project(project_id):
    return list(
        DesignCase.select()
        .where(
            (DesignCase.project_id == project_id)
            & (DesignCase.deleted_at.is_null())
        )
        .order_by(DesignCase.id)
    )


def list_by_feature(feature_id):
    return list(
        DesignCase.select()
        .where(
            (DesignCase.feature_id == feature_id)
            & (DesignCase.deleted_at.is_null())
        )
        .order_by(DesignCase.id)
    )


def save(design_case):
    design_case.save()
    return design_case


def soft_delete(design_case):
    return _soft.soft_delete(design_case)


def restore(design_case):
    return _soft.restore(design_case)


def purge(design_case):
    _soft.purge(design_case)


def get_deleted(design_case_id):
    return _soft.get_deleted(DesignCase, design_case_id)


def get_deleted_by_number(project_id, design_number):
    """Resolve a business number to a soft-deleted row (used by the restore
    skill action — restore's business number can't be looked up via the
    live-only get_by_number)."""
    design_number = normalize_number(project_id, design_number)
    return DesignCase.get_or_none(
        (DesignCase.project_id == project_id)
        & (DesignCase.design_number == design_number)
        & (DesignCase.deleted_at.is_null(False))
    )


def list_deleted_by_project(project_id):
    return _soft.list_deleted_by_project(DesignCase, project_id)
