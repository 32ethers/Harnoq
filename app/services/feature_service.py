"""Feature business logic."""
import json

from app.core.errors import NotFoundError, ValidationError
from app.db.base import database_proxy
from app.models.feature import (
    FEATURE_VERSIONED_FIELDS,
    FOUNDATION_KINDS,
    ORIENTATIONS,
    SUBDOMAIN_TYPES,
    Feature,
)
from app.repositories import (
    design_case_repo,
    development_task_repo,
    feature_dependency_repo,
    feature_repo,
    feature_version_repo,
    project_repo,
)
from app.services import feature_dependency_service, feature_owner_service, lifecycle, numbering, soft_delete as _softdel

_LABEL = "feature"


def _require_name(data):
    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("name is required")
    return name.strip()


def _ensure_project(project_id):
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")


def _normalize_orientation(data):
    orientation = data.get("orientation", "vertical")
    if orientation not in ORIENTATIONS:
        raise ValidationError(
            f"invalid orientation: {orientation}; valid values: {', '.join(ORIENTATIONS)}"
        )
    return orientation


def _normalize_subdomain_type(data):
    subdomain_type = data.get("subdomain_type", "supporting")
    if subdomain_type not in SUBDOMAIN_TYPES:
        raise ValidationError(
            f"invalid subdomain_type: {subdomain_type}; "
            f"valid values: {', '.join(SUBDOMAIN_TYPES)}"
        )
    return subdomain_type


def _normalize_is_spike(data):
    is_spike = data.get("is_spike", False)
    if not isinstance(is_spike, bool):
        raise ValidationError("is_spike must be a boolean")
    return is_spike


def _normalize_foundation_kind(data, *, orientation, feature_id=None, project_id=None):
    """Validate the optional identity of a project-wide foundation design.

    The architecture and UI foundations remain ordinary ``foundation``
    Features so existing foreign keys and the dependency graph continue to
    work.  Naming their role explicitly makes their required ordering visible
    to API/UI/skill clients and prevents two competing project foundations of
    the same kind.
    """
    foundation_kind = data.get("foundation_kind")
    if foundation_kind is None:
        return None
    if foundation_kind not in FOUNDATION_KINDS:
        raise ValidationError(
            "invalid foundation_kind: "
            f"{foundation_kind}; valid values: {', '.join(FOUNDATION_KINDS)}"
        )
    if orientation != "foundation":
        raise ValidationError("foundation_kind requires orientation='foundation'")
    existing = feature_repo.get_foundation_kind(
        project_id, foundation_kind, exclude_feature_id=feature_id
    )
    if existing is not None:
        raise ValidationError(
            f"project already has {foundation_kind} foundation "
            f"{existing.feature_number or existing.id}"
        )
    return foundation_kind


def create(project_id, data):
    with database_proxy.atomic():
        return _create(project_id, data)


def _create(project_id, data):
    _ensure_project(project_id)
    orientation = _normalize_orientation(data)
    subdomain_type = _normalize_subdomain_type(data)
    is_spike = _normalize_is_spike(data)
    foundation_kind = _normalize_foundation_kind(
        data, orientation=orientation, project_id=project_id
    )
    feature = feature_repo.create(
        project_id=project_id,
        name=_require_name(data),
        description=data.get("description") or "",
        assignee=None,
        orientation=orientation,
        foundation_kind=foundation_kind,
        subdomain_type=subdomain_type,
        is_spike=is_spike,
    )
    feature = numbering.assign_number(
        feature, "FEATURE", "feature_number", project_id=project_id
    )
    feature_owner_service.apply(feature, data)
    feature_repo.save(feature)
    if "depends_on_feature_ids" in data:
        feature_dependency_service.replace_dependencies(
            feature, data.get("depends_on_feature_ids") or []
        )
    _create_version(feature, change_type="create")
    return feature


def get(feature_id):
    feature = feature_repo.get(feature_id)
    if feature is None:
        raise NotFoundError(f"feature {feature_id} not found")
    return feature


def get_by_number(project_id, feature_number):
    feature = feature_repo.get_by_number(project_id, feature_number)
    if feature is None:
        raise NotFoundError(f"feature {feature_number} not found")
    return feature


def get_deleted_by_number(project_id, feature_number):
    feature = feature_repo.get_deleted_by_number(project_id, feature_number)
    if feature is None:
        raise NotFoundError(f"feature {feature_number} not in recycle bin")
    return feature


def list_by_project(project_id):
    _ensure_project(project_id)
    return feature_repo.list_by_project(project_id)


def dict_ctx(project_id):
    """Pre-fetch, in 2 queries, the maps Feature.to_dict(...) needs so a LIST
    of features serializes without 2 queries PER feature (dependencies +
    design_cases — the N+1 that made the features list slow to load)."""
    from app.models.design_case import DesignCase
    from app.models.feature import Feature, FeatureDependency

    Target = Feature.alias()
    dependencies_by_feature_id = {}
    for edge in (
        FeatureDependency.select(FeatureDependency, Target)
        .join(Feature, on=(FeatureDependency.feature_id == Feature.id))
        .switch(FeatureDependency)
        .join(Target, on=(FeatureDependency.depends_on_feature == Target.id))
        .where(
            (Feature.project_id == project_id)
            & Feature.deleted_at.is_null()
            & Target.deleted_at.is_null()
            & FeatureDependency.deleted_at.is_null()
        )
        .order_by(FeatureDependency.id)
    ):
        dependencies_by_feature_id.setdefault(edge.feature_id, []).append(edge)

    design_cases_by_feature_id = {}
    for design_case in (
        DesignCase.select()
        .where((DesignCase.project_id == project_id) & DesignCase.deleted_at.is_null())
        .order_by(DesignCase.id)
    ):
        if design_case.feature_id is not None:
            design_cases_by_feature_id.setdefault(design_case.feature_id, []).append(design_case)

    return {
        "dependencies_by_feature_id": dependencies_by_feature_id,
        "design_cases_by_feature_id": design_cases_by_feature_id,
    }


def list_deleted(project_id):
    _ensure_project(project_id)
    return feature_repo.list_deleted_by_project(project_id)


def compute_progress(feature):
    return feature.to_dict()["progress"]


def update(feature_id, data):
    with database_proxy.atomic():
        return _update(feature_id, data)


def _update(feature_id, data):
    feature = get(feature_id)
    before = _version_payload(feature)
    previous_owner = feature_owner_service.identity(feature.id)
    target_orientation = (
        _normalize_orientation(data) if "orientation" in data else feature.orientation
    )
    if "foundation_kind" in data:
        target_foundation_kind = _normalize_foundation_kind(
            data,
            orientation=target_orientation,
            feature_id=feature.id,
            project_id=feature.project_id,
        )
    else:
        target_foundation_kind = feature.foundation_kind
        if target_foundation_kind is not None and target_orientation != "foundation":
            raise ValidationError("foundation_kind requires orientation='foundation'")
    if "name" in data:
        feature.name = _require_name(data)
    if "description" in data:
        feature.description = data["description"] or ""
    feature_owner_service.apply(feature, data)
    if "orientation" in data:
        feature.orientation = target_orientation
    if "foundation_kind" in data:
        feature.foundation_kind = target_foundation_kind
    if "subdomain_type" in data:
        feature.subdomain_type = _normalize_subdomain_type(data)
    if "is_spike" in data:
        feature.is_spike = _normalize_is_spike(data)

    feature = feature_repo.save(feature)
    if "depends_on_feature_ids" in data:
        feature_dependency_service.replace_dependencies(
            feature, data.get("depends_on_feature_ids") or []
        )
    # No cascade onto the designs made under this feature. Editing what the
    # feature IS can certainly invalidate them, but that is a fact about the
    # feature->design EDGE, and stamping it onto the design's own status (the
    # old needs_review) made the design's state stop describing the design.
    # Derived on read from version snapshots instead — the change model.
    current_owner = feature_owner_service.identity(feature.id)
    if (_version_payload(feature) != before
            or (current_owner or {}).get("user_number") != (previous_owner or {}).get("user_number")):
        _create_version(feature, change_type="update")
    return feature


def set_assignee(feature_id, assignee):
    return update(feature_id, {"assignee": assignee})


def list_versions(feature_id):
    get(feature_id)
    return feature_version_repo.list_by_feature(feature_id)


def current_version_number(feature_id):
    latest = feature_version_repo.latest_for_feature(feature_id)
    return latest.version_number if latest is not None else None


def restore_version(feature_id, version_number):
    feature = get(feature_id)
    version = feature_version_repo.get(feature_id, version_number)
    if version is None:
        raise NotFoundError(f"feature {feature_id} version {version_number} not found")

    # A snapshot can retain a foundation identity that has since been claimed
    # by another live feature. Validate before changing either the row or its
    # dependency graph.
    _normalize_foundation_kind(
        {"foundation_kind": version.foundation_kind},
        orientation=version.orientation,
        feature_id=feature.id,
        project_id=feature.project_id,
    )
    dependency_ids = _version_dependency_ids(version)
    with database_proxy.atomic():
        for field in FEATURE_VERSIONED_FIELDS:
            setattr(feature, field, getattr(version, field))
        feature_owner_service.restore(feature, version)
        feature = feature_repo.save(feature)
        feature_dependency_service.replace_dependencies(feature, dependency_ids)
        _create_version(
            feature,
            change_type="restore",
            restored_from_version_number=version.version_number,
        )
    return feature


def _version_dependency_ids(version):
    try:
        dependency_ids = json.loads(version.depends_on_feature_ids or "[]")
    except (TypeError, ValueError):
        dependency_ids = []
    if not isinstance(dependency_ids, list) or not all(
        isinstance(feature_id, int) for feature_id in dependency_ids
    ):
        raise ValidationError("feature version has invalid dependency snapshot")
    return dependency_ids


def _version_payload(feature):
    dependency_ids = feature_dependency_repo.depends_on_feature_ids(feature.id)
    dependency_numbers_by_id = {
        dependency.id: dependency.feature_number
        for dependency in Feature.select(Feature.id, Feature.feature_number).where(
            Feature.id.in_(dependency_ids)
        )
    }
    return {
        **{field: getattr(feature, field) for field in FEATURE_VERSIONED_FIELDS},
        "depends_on_feature_ids": json.dumps(dependency_ids),
        "depends_on_feature_numbers": json.dumps([
            dependency_numbers_by_id.get(feature_id)
            for feature_id in dependency_ids
        ]),
    }


def _create_version(feature, *, change_type, restored_from_version_number=None):
    latest = feature_version_repo.latest_for_feature(feature.id)
    next_number = 1 if latest is None else latest.version_number + 1
    version = feature_version_repo.create(
        feature=feature.id,
        version_number=next_number,
        change_type=change_type,
        restored_from_version_number=restored_from_version_number,
        **_version_payload(feature),
    )
    feature_owner_service.snapshot(feature, version)
    return version


def delete(feature_id):
    return _softdel.delete(feature_repo, feature_id, _LABEL)


def restore(feature_id):
    # ``_softdel.restore`` intentionally knows nothing about entity-specific
    # live-row invariants. Re-check this one before clearing deleted_at: a
    # replacement foundation may have been created while this Feature was in
    # the recycle bin.
    feature = feature_repo.get_deleted(feature_id)
    if feature is not None and feature.foundation_kind is not None:
        existing = feature_repo.get_foundation_kind(
            feature.project_id,
            feature.foundation_kind,
            exclude_feature_id=feature.id,
        )
        if existing is not None:
            raise ValidationError(
                "cannot restore: project already has "
                f"{feature.foundation_kind} foundation "
                f"{existing.feature_number or existing.id}"
            )
    return _softdel.restore(feature_repo, feature_id, _LABEL)


def purge(feature_id):
    _softdel.purge(feature_repo, feature_id, _LABEL)


# --- Dependency-order gate (docs/cn/design-evolution.md#dependencies) ---
#
# Feature is the only dependency graph left in the system. Whether a task can
# be developed is never stored (no "blocked" status) — it is always computed
# live from this graph, at claim time (the sole authority, see
# development_task_service.claim) and at list-render time (display only).


def _is_satisfied(designs, tasks):
    """Pure predicate shared by the live, per-feature check (feature_satisfied)
    and the batched, project-wide check (project_gate_context) so the two
    call sites can never define "satisfied" differently. ``designs``/``tasks``
    must already be filtered to this one feature's LIVE (non-deleted) rows.

    "has generated tasks" (the old ``tasks_generated_at`` stamp) is now read
    live: a design counts as decomposed once at least one task exists under
    it (the direct-task model dropped the DRAFT layer and its freeze timestamp — task
    creation is the generation step now, one call at a time)."""
    if not designs:
        return False
    tasks_by_design = {}
    for task in tasks:
        tasks_by_design.setdefault(task.design_case_id, []).append(task)
    if any(
        design.status != lifecycle.CONFIRMED or not tasks_by_design.get(design.id)
        for design in designs
    ):
        return False
    if not tasks:
        return False
    return all(task.status == "completed" for task in tasks)


def feature_satisfied(feature_id):
    """True iff Feature ``feature_id`` is done: it has at least one design,
    every one of its (live) designs is confirmed and has generated tasks, it
    has at least one (live) task, and every one of those tasks is completed.
    Any of these being false means the feature is not satisfied, so its
    downstream features stay blocked — including while it has no design yet."""
    designs = design_case_repo.list_by_feature(feature_id)
    tasks = development_task_repo.list_by_feature(feature_id)
    return _is_satisfied(designs, tasks)


def _feature_id_of_task(task):
    """A task's Feature affiliation, resolved even if its DesignCase was later
    soft-deleted (get_including_deleted) — that affiliation is a structural
    fact set at generation time, not something that should silently vanish
    and bypass the claim-time dependency-order gate (see
    _feature_upstream_unsatisfied, the sole authority in development_task_
    service.claim)."""
    if task.feature_id:
        return task.feature_id
    if not task.design_case_id:
        return None
    design_case = design_case_repo.get_including_deleted(task.design_case_id)
    return design_case.feature_id if design_case else None


def blocking_upstream_ids(feature_id, upstream_of, satisfied):
    """Every Feature upstream of ``feature_id``, AT ANY DEPTH, not satisfied.

    Pure and parameterised over its two lookups so the live per-task check and
    the batched list-render check cannot define "blocked" differently — the
    same reason ``_is_satisfied`` is shared.

    Depth is the whole point, and the reason this replaced a direct-upstreams
    check. That check reasoned: an upstream which itself depends on something
    unfinished cannot be satisfied either, so blocking propagates on its own.
    True while a chain is being built for the first time — and false the
    moment a FINISHED upstream is reopened, because ``_is_satisfied`` asks
    only about a Feature's OWN designs and tasks. In a live project with
    F1 <- F4 <- F9 all delivered, adding one task back onto F1 left F4
    satisfied (its own work was still done) and F9 therefore unblocked, so a
    downstream task could be claimed against a foundation that had just been
    reopened. That is exactly the false record the gate exists to prevent.

    Unsatisfied nodes are walked THROUGH, not stopped at, so the report names
    the root cause and not just the nearest symptom. ``seen`` makes a cyclic
    graph terminate rather than trusting that none exists.
    """
    blocking, seen = [], {feature_id}
    stack = list(upstream_of(feature_id))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        if not satisfied(current):
            blocking.append(current)
        stack.extend(upstream_of(current))
    return blocking


def _feature_upstream_unsatisfied(task):
    """True iff ``task`` should NOT be claimable right now: some Feature
    upstream of its own — at any depth — is not yet satisfied."""
    feature_id = _feature_id_of_task(task)
    if feature_id is None:
        return False  # no Feature (foundation design / manual task): unconstrained
    return bool(
        blocking_upstream_ids(
            feature_id,
            feature_dependency_repo.depends_on_feature_ids,
            feature_satisfied,
        )
    )


def project_gate_context(project_id):
    """Batch, read-only inputs for rendering a project's whole task list
    without one query per task (the display counterpart to the live,
    per-task ``feature_satisfied`` / ``_feature_upstream_unsatisfied`` used as
    the claim-time authority). A handful of project-wide queries, then
    everything below is computed in memory. Returns a dict with:

    - ``feature_id_by_design_id``: ``{design_case_id: feature_id_or_None}``
    - ``satisfied_by_feature_id``: ``{feature_id: bool}``
    - ``upstream_ids_by_feature_id``: ``{feature_id: [depends_on_feature_id, ...]}``
    - ``feature_number_by_id``: ``{feature_id: "FEATURE-N"}``
    """
    designs = design_case_repo.list_by_project(project_id)
    # feature_id_by_design_id routes tasks to their Feature and must cover
    # deleted designs too (get_including_deleted's rationale applies here the
    # same way — a task's Feature affiliation must not vanish because its
    # design was later soft-deleted); satisfaction below is computed from
    # `designs` (live only) same as feature_satisfied.
    feature_id_by_design_id = {
        design.id: design.feature_id
        for design in designs + design_case_repo.list_deleted_by_project(project_id)
    }

    designs_by_feature_id = {}
    for design in designs:
        if design.feature_id is not None:
            designs_by_feature_id.setdefault(design.feature_id, []).append(design)

    tasks = development_task_repo.list_by_project(project_id)
    tasks_by_feature_id = {}
    for task in tasks:
        feature_id = task.feature_id or feature_id_by_design_id.get(task.design_case_id)
        if feature_id is not None:
            tasks_by_feature_id.setdefault(feature_id, []).append(task)

    feature_ids = set(designs_by_feature_id) | set(tasks_by_feature_id)
    satisfied_by_feature_id = {
        feature_id: _is_satisfied(
            designs_by_feature_id.get(feature_id, []), tasks_by_feature_id.get(feature_id, [])
        )
        for feature_id in feature_ids
    }

    upstream_ids_by_feature_id = {}
    for edge in feature_dependency_repo.list_live_edges_by_project(project_id):
        upstream_ids_by_feature_id.setdefault(edge.feature_id, []).append(
            edge.depends_on_feature_id
        )

    feature_number_by_id = {
        feature.id: feature.feature_number for feature in feature_repo.list_by_project(project_id)
    }

    return {
        "feature_id_by_design_id": feature_id_by_design_id,
        "satisfied_by_feature_id": satisfied_by_feature_id,
        "upstream_ids_by_feature_id": upstream_ids_by_feature_id,
        "feature_number_by_id": feature_number_by_id,
    }


def claim_readiness(task, ctx):
    """Given one task and a ``project_gate_context(...)`` result, return
    ``(claim_ready, waiting_on_feature_numbers)`` for display. Mirrors
    ``_feature_upstream_unsatisfied`` but reads the batched maps instead of
    querying — O(1) per task."""
    feature_id = task.feature_id or ctx["feature_id_by_design_id"].get(task.design_case_id)
    if feature_id is None:
        return True, []
    blocking = blocking_upstream_ids(
        feature_id,
        lambda fid: ctx["upstream_ids_by_feature_id"].get(fid, []),
        lambda fid: ctx["satisfied_by_feature_id"].get(fid, False),
    )
    waiting_on = [ctx["feature_number_by_id"].get(fid) for fid in blocking]
    return not waiting_on, waiting_on
