"""Feature block dependency graph (Context Map): edge validation + acyclic
topological order.

Shared by ``feature_service`` (which owns writing the edges on create/update)
and task ordering and the UI's dependency display
(see docs/cn/design-evolution.md#dependencies). Uses Kahn's
algorithm: a feature with zero unresolved dependencies is emitted first, so
foundation blocks (few/no dependencies) naturally sort ahead of the vertical
slices built on them; any features left over once the queue drains are stuck
in a cycle.
"""
from app.core.errors import ValidationError
from app.repositories import feature_dependency_repo, feature_repo


def _normalize_depends_on_feature_ids(feature, raw_ids):
    if not isinstance(raw_ids, list):
        raise ValidationError("depends_on_feature_ids must be a list")
    normalized = []
    seen = set()
    for depends_on_feature_id in raw_ids:
        if not isinstance(depends_on_feature_id, int):
            raise ValidationError(
                f"invalid depends_on_feature_id: {depends_on_feature_id}"
            )
        if depends_on_feature_id == feature.id:
            raise ValidationError("a feature cannot depend on itself")
        target = feature_repo.get(depends_on_feature_id)
        if target is None or target.project_id != feature.project_id:
            raise ValidationError(
                f"invalid depends_on_feature_id: {depends_on_feature_id}"
            )
        if depends_on_feature_id in seen:
            continue
        seen.add(depends_on_feature_id)
        normalized.append(depends_on_feature_id)
    return normalized


def _project_adjacency(project_id):
    adjacency = {}
    for edge in feature_dependency_repo.list_live_edges_by_project(project_id):
        adjacency.setdefault(edge.feature_id, set()).add(edge.depends_on_feature_id)
    return adjacency


def _kahn_order(node_ids, adjacency):
    """Topologically sort ``node_ids`` given ``{node: set(depends_on)}``.
    Returns ``(order, cyclic_remainder)`` — ``remainder`` is non-empty iff the
    (sub)graph induced by ``node_ids`` has a cycle."""
    node_ids = list(node_ids)
    node_set = set(node_ids)
    indegree = {node: 0 for node in node_ids}
    dependents = {node: [] for node in node_ids}
    for node in node_ids:
        for dependency in adjacency.get(node, ()):
            if dependency not in node_set:
                continue  # edge points outside this node set; not our concern
            indegree[node] += 1
            dependents[dependency].append(node)

    queue = [node for node in node_ids if indegree[node] == 0]
    order = []
    while queue:
        node = queue.pop(0)
        order.append(node)
        for dependent in dependents[node]:
            indegree[dependent] -= 1
            if indegree[dependent] == 0:
                queue.append(dependent)

    remainder = [node for node in node_ids if node not in order]
    return order, remainder


def replace_dependencies(feature, raw_depends_on_feature_ids):
    """Validate and persist ``feature``'s outgoing dependency edges.

    Raises ValidationError if any target id is invalid, or if applying the
    change would make the project's block dependency graph cyclic.
    """
    depends_on_feature_ids = _normalize_depends_on_feature_ids(
        feature, raw_depends_on_feature_ids
    )
    features = feature_repo.list_by_project(feature.project_id)
    adjacency = _project_adjacency(feature.project_id)
    adjacency[feature.id] = set(depends_on_feature_ids)
    _, remainder = _kahn_order([f.id for f in features], adjacency)
    if remainder:
        numbers_by_id = {f.id: f.feature_number for f in features}
        involved = sorted(
            numbers_by_id.get(feature_id) or f"id:{feature_id}"
            for feature_id in remainder
        )
        raise ValidationError(
            "feature dependency graph must stay acyclic; "
            f"cycle involves: {', '.join(involved)}"
        )
    feature_dependency_repo.replace_edges(feature.id, depends_on_feature_ids)
    return depends_on_feature_ids


def topological_order(project_id):
    """Live feature ids for ``project_id`` sorted so each feature comes after
    everything it depends on (core/generic foundation blocks first, vertical
    slices last). Raises ValidationError if the stored graph is somehow
    cyclic (should not happen — every write goes through
    :func:`replace_dependencies`, which rejects cycles up front)."""
    features = feature_repo.list_by_project(project_id)
    adjacency = _project_adjacency(project_id)
    order, remainder = _kahn_order([f.id for f in features], adjacency)
    if remainder:
        numbers_by_id = {f.id: f.feature_number for f in features}
        involved = sorted(
            numbers_by_id.get(feature_id) or f"id:{feature_id}"
            for feature_id in remainder
        )
        raise ValidationError(
            f"feature dependency graph is not acyclic; cycle involves: {', '.join(involved)}"
        )
    return order
