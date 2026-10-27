"""Database access for DesignCaseVersion."""
from app.models.design_case import DesignCaseVersion


def create(**data):
    return DesignCaseVersion.create(**data)


def get(design_case_id, version_number):
    return DesignCaseVersion.get_or_none(
        (DesignCaseVersion.design_case_id == design_case_id)
        & (DesignCaseVersion.version_number == version_number)
        & (DesignCaseVersion.deleted_at.is_null())
    )


def list_by_design(design_case_id):
    return list(
        DesignCaseVersion.select()
        .where(
            (DesignCaseVersion.design_case_id == design_case_id)
            & (DesignCaseVersion.deleted_at.is_null())
        )
        .order_by(DesignCaseVersion.version_number.desc())
    )


def latest_for_design(design_case_id):
    return (
        DesignCaseVersion.select()
        .where(
            (DesignCaseVersion.design_case_id == design_case_id)
            & (DesignCaseVersion.deleted_at.is_null())
        )
        .order_by(DesignCaseVersion.version_number.desc())
        .first()
    )
