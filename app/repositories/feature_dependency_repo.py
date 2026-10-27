"""Database access for Feature -> Feature dependency edges (the block Context
Map; see docs/cn/design-evolution.md#dependencies). Soft-deleted rows
are toggled rather than re-inserted, so an ordered (feature, depends_on)
pair has at most one row across its whole history — see
app.models.feature.FeatureDependency's docstring.
"""
from app.models.feature import Feature, FeatureDependency
from app.repositories import soft_delete as _soft


def get_edge(feature_id, depends_on_feature_id):
    return FeatureDependency.get_or_none(
        (FeatureDependency.feature_id == feature_id)
        & (FeatureDependency.depends_on_feature_id == depends_on_feature_id)
    )


def depends_on_feature_ids(feature_id):
    """"Live" means both the edge row and its *target* Feature are not
    soft-deleted — matches app.models.feature._live_feature_dependency_query
    (the existing convention for a Feature's own displayed dependency list).
    An edge left dangling by deleting its target is inert everywhere (Kahn
    order, the claim-time gate) rather than permanently blocking a downstream
    Feature on something that no longer exists to be built."""
    return [
        link.depends_on_feature_id
        for link in FeatureDependency.select()
        .join(Feature, on=(FeatureDependency.depends_on_feature == Feature.id))
        .where(
            (FeatureDependency.feature_id == feature_id)
            & FeatureDependency.deleted_at.is_null()
            & Feature.deleted_at.is_null()
        )
        .order_by(FeatureDependency.id)
    ]


def list_live_edges_by_project(project_id):
    """Every live dependency edge whose *source* feature belongs to
    ``project_id`` (features only depend on other features in the same
    project, so this is the whole project's edge set). "Live" requires the
    *target* Feature to still exist too — see depends_on_feature_ids."""
    Target = Feature.alias()
    return list(
        FeatureDependency.select(FeatureDependency)
        .join(Feature, on=(FeatureDependency.feature_id == Feature.id))
        .join(Target, on=(FeatureDependency.depends_on_feature == Target.id))
        .where(
            (Feature.project_id == project_id)
            & Feature.deleted_at.is_null()
            & Target.deleted_at.is_null()
            & FeatureDependency.deleted_at.is_null()
        )
    )


def add_edge(feature_id, depends_on_feature_id):
    existing = get_edge(feature_id, depends_on_feature_id)
    if existing is None:
        return FeatureDependency.create(
            feature=feature_id, depends_on_feature=depends_on_feature_id
        )
    return _soft.restore(existing)


def remove_edge(feature_id, depends_on_feature_id):
    existing = get_edge(feature_id, depends_on_feature_id)
    if existing is not None:
        _soft.soft_delete(existing)


def replace_edges(feature_id, depends_on_feature_ids_to_use):
    current = set(depends_on_feature_ids(feature_id))
    target = set(depends_on_feature_ids_to_use)
    for depends_on_feature_id in current - target:
        remove_edge(feature_id, depends_on_feature_id)
    for depends_on_feature_id in target - current:
        add_edge(feature_id, depends_on_feature_id)
