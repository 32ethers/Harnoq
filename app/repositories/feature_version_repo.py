"""Database access for FeatureVersion snapshots."""
from app.models.feature import FeatureVersion


def create(**data):
    return FeatureVersion.create(**data)


def get(feature_id, version_number):
    return FeatureVersion.get_or_none(
        (FeatureVersion.feature_id == feature_id)
        & (FeatureVersion.version_number == version_number)
        & FeatureVersion.deleted_at.is_null()
    )


def list_by_feature(feature_id):
    return list(
        FeatureVersion.select()
        .where(
            (FeatureVersion.feature_id == feature_id)
            & FeatureVersion.deleted_at.is_null()
        )
        .order_by(FeatureVersion.version_number.desc())
    )


def latest_for_feature(feature_id):
    return (
        FeatureVersion.select()
        .where(
            (FeatureVersion.feature_id == feature_id)
            & FeatureVersion.deleted_at.is_null()
        )
        .order_by(FeatureVersion.version_number.desc())
        .first()
    )
