"""Database access for Feature."""
from app.models.feature import Feature
from app.repositories import soft_delete as _soft
from app.services.numbering import normalize_number


def create(project_id, **fields):
    return Feature.create(project=project_id, **fields)


def get(feature_id):
    return Feature.get_or_none(
        (Feature.id == feature_id) & Feature.deleted_at.is_null()
    )


def get_by_number(project_id, feature_number):
    feature_number = normalize_number(project_id, feature_number)
    return Feature.get_or_none(
        (Feature.project_id == project_id)
        & (Feature.feature_number == feature_number)
        & Feature.deleted_at.is_null()
    )


def list_by_project(project_id):
    return list(
        Feature.select()
        .where((Feature.project_id == project_id) & Feature.deleted_at.is_null())
        .order_by(Feature.id)
    )


def get_foundation_kind(project_id, foundation_kind, *, exclude_feature_id=None):
    query = Feature.select().where(
        (Feature.project_id == project_id)
        & (Feature.foundation_kind == foundation_kind)
        & Feature.deleted_at.is_null()
    )
    if exclude_feature_id is not None:
        query = query.where(Feature.id != exclude_feature_id)
    return query.first()


def save(feature):
    feature.save()
    return feature


def soft_delete(feature):
    return _soft.soft_delete(feature)


def restore(feature):
    return _soft.restore(feature)


def purge(feature):
    _soft.purge(feature)


def get_deleted(feature_id):
    return _soft.get_deleted(Feature, feature_id)


def get_deleted_by_number(project_id, feature_number):
    feature_number = normalize_number(project_id, feature_number)
    return Feature.get_or_none(
        (Feature.project_id == project_id)
        & (Feature.feature_number == feature_number)
        & Feature.deleted_at.is_null(False)
    )


def list_deleted_by_project(project_id):
    return _soft.list_deleted_by_project(Feature, project_id)
