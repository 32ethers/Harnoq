"""Suspect links: "the thing I was settled against has moved since"
(see docs/cn/design-evolution.md#changes).

Two halves, deliberately kept apart:

- **Record** the basis. Confirming a DesignCase writes down which version of
  the FEATURE-* it sits in it was confirmed against; creating a task writes
  down the design version it came from.
- **Derive** suspect at read time: upstream's current version number > the
  recorded basis. Nothing is stored, no status is touched, and no cascade
  decides how far to travel — which is exactly what went wrong with the
  ``needs_review`` flag this replaces (see app/services/lifecycle.py).

A suspect edge is a PROMPT, not a gate: it says "a human should look", and by
default blocks nothing (see docs/cn/design-evolution.md#changes). Digesting it is
:func:`realign` — re-record the basis at today's versions, which is the
"I looked, it still holds" answer. If it does NOT still hold, the answer is to
reopen and revise the downstream object, and confirming it re-records the basis
anyway.
"""
from app.models.basis_link import BasisLink
from app.models.design_case import DesignCase, DesignCaseVersion
from app.models.feature import Feature, FeatureVersion
from app.repositories import basis_link_repo

DESIGN_CASE = "design_case"
DEVELOPMENT_TASK = "development_task"
FEATURE = "feature"

#: version model per upstream type, for the bulk "current version" lookup.
_VERSION_MODELS = {
    FEATURE: (FeatureVersion, FeatureVersion.feature),
    DESIGN_CASE: (DesignCaseVersion, DesignCaseVersion.design_case),
}

#: live entity model per upstream type — soft delete never cascades to version
#: rows (see module docstring), so "does a version row exist" alone cannot
#: tell a withdrawn upstream from one that simply had no edits. Whether the
#: entity itself is still live has to be checked separately, against its own
#: table.
_ENTITY_MODELS = {
    FEATURE: Feature,
    DESIGN_CASE: DesignCase,
}


# --- recording -------------------------------------------------------------

def record_design_basis(design_case):
    """Called when a design is confirmed: snapshot everything it answers to.

    The FEATURE-* edge replaces the former feature->design cascade: changing what a feature IS can still invalidate the designs
    under it, and this is where that now shows up. It is the design's only
    upstream since the REQ-* layer was removed.
    """
    edges = []
    if design_case.feature_id:
        version = current_version_number(FEATURE, design_case.feature_id)
        if version is not None:
            edges.append((FEATURE, design_case.feature_id, version))
    return basis_link_repo.replace_for_downstream(
        design_case.project_id, DESIGN_CASE, design_case.id, edges
    )


def record_task_basis(task, design_case):
    """Called when a task is created: snapshot the design version it came from."""
    version = current_version_number(DESIGN_CASE, design_case.id)
    edges = [] if version is None else [(DESIGN_CASE, design_case.id, version)]
    return basis_link_repo.replace_for_downstream(
        task.project_id, DEVELOPMENT_TASK, task.id, edges
    )


def forget(downstream_type, downstream_id):
    """Drop a downstream object's basis (it was deleted, or reopened back into
    an unsettled state — there is nothing to be stale against any more)."""
    return basis_link_repo.delete_for_downstream(downstream_type, downstream_id)


def realign(downstream_type, downstream_id):
    """Digest the suspicion: re-record every existing edge at today's version.

    This is "I reviewed the upstream change and this object still holds". It
    only refreshes edges that already exist — it never invents coverage, so a
    changed upstream association needs a newly recorded basis.
    """
    links = basis_link_repo.list_for_downstream(downstream_type, downstream_id)
    if not links:
        return []
    edges = []
    for link in links:
        version = current_version_number(link.upstream_type, link.upstream_id)
        edges.append(
            (
                link.upstream_type,
                link.upstream_id,
                link.basis_version_number if version is None else version,
            )
        )
    return basis_link_repo.replace_for_downstream(
        links[0].project_id, downstream_type, downstream_id, edges
    )


# --- deriving --------------------------------------------------------------

def current_version_number(object_type, object_id):
    entry = _VERSION_MODELS.get(object_type)
    if entry is None:
        return None
    model, fk = entry
    row = (
        model.select(model.version_number)
        .where((fk == object_id) & (model.deleted_at.is_null()))
        .order_by(model.version_number.desc())
        .first()
    )
    return row.version_number if row is not None else None


def is_upstream_live(object_type, object_id):
    """Is the upstream entity itself still there (not soft-deleted, not purged)?

    Separate from :func:`current_version_number` on purpose: version rows
    outlive a soft delete (nothing cascades to them), so their mere existence
    says nothing about whether the entity that owns them is still live.
    """
    model = _ENTITY_MODELS.get(object_type)
    if model is None:
        return True
    return (
        model.select(model.id)
        .where((model.id == object_id) & (model.deleted_at.is_null()))
        .exists()
    )


def _live_ids_bulk(object_type, object_ids):
    model = _ENTITY_MODELS.get(object_type)
    if model is None or not object_ids:
        return set(object_ids)
    rows = (
        model.select(model.id)
        .where(model.id.in_(list(set(object_ids))) & model.deleted_at.is_null())
        .tuples()
    )
    return {row[0] for row in rows}


def stale_edges_for(downstream_type, downstream_id):
    """The suspect edges of ONE object, as dicts. Use for a detail page."""
    links = basis_link_repo.list_for_downstream(downstream_type, downstream_id)
    stale = []
    for link in links:
        if not is_upstream_live(link.upstream_type, link.upstream_id):
            stale.append(_edge_dict(link, None, reason="upstream_deleted"))
            continue
        current = current_version_number(link.upstream_type, link.upstream_id)
        if current is not None and current > link.basis_version_number:
            stale.append(_edge_dict(link, current, reason="upstream_revised"))
    return stale


def stale_edges_by_downstream(project_id):
    """Every suspect edge in a project, keyed by ``(type, id)``.

    Bulk on purpose: one query for the edges plus one per upstream KIND (three,
    not three per object) for versions, and the same again for liveness — never
    a query per object, so a list page or project-context can mark every row
    without an N+1.
    """
    links = basis_link_repo.list_by_project(project_id)
    if not links:
        return {}
    current_by_type = {}
    live_by_type = {}
    for object_type in {link.upstream_type for link in links}:
        ids = [l.upstream_id for l in links if l.upstream_type == object_type]
        current_by_type[object_type] = _current_versions_bulk(object_type, ids)
        live_by_type[object_type] = _live_ids_bulk(object_type, ids)
    stale = {}
    for link in links:
        if link.upstream_id not in live_by_type.get(link.upstream_type, set()):
            edge = _edge_dict(link, None, reason="upstream_deleted")
        else:
            current = current_by_type.get(link.upstream_type, {}).get(link.upstream_id)
            if current is not None and current > link.basis_version_number:
                edge = _edge_dict(link, current, reason="upstream_revised")
            else:
                continue
        key = (link.downstream_type, link.downstream_id)
        stale.setdefault(key, []).append(edge)
    return stale


def _current_versions_bulk(object_type, object_ids):
    entry = _VERSION_MODELS.get(object_type)
    if entry is None or not object_ids:
        return {}
    model, fk = entry
    from peewee import fn

    rows = (
        model.select(fk.alias("object_id"), fn.MAX(model.version_number).alias("current"))
        .where(fk.in_(list(set(object_ids))) & model.deleted_at.is_null())
        .group_by(fk)
        .tuples()
    )
    return {object_id: current for object_id, current in rows}


def _edge_dict(link, current_version_number_, *, reason):
    return {
        "upstream_type": link.upstream_type,
        "upstream_id": link.upstream_id,
        "basis_version_number": link.basis_version_number,
        "current_version_number": current_version_number_,
        "reason": reason,
    }


def annotate(payload, downstream_type, downstream_id, stale_by_downstream=None):
    """Attach ``suspect`` / ``suspect_upstream`` to a serialized object.

    ``stale_by_downstream`` is the bulk map from
    :func:`stale_edges_by_downstream`; pass it in a list, omit it for a single
    object (then the edges are queried directly).
    """
    if stale_by_downstream is None:
        edges = stale_edges_for(downstream_type, downstream_id)
    else:
        edges = stale_by_downstream.get((downstream_type, downstream_id), [])
    return payload | {"suspect": bool(edges), "suspect_upstream": edges}


def describe(edges, number_lookup):
    """Human-readable upstream refs for an edge list, e.g. ``["REQ-3", "FEATURE-1"]``.

    ``number_lookup`` maps ``(type, id) -> business number`` so this module
    never has to know how each entity numbers itself.
    """
    return [
        number_lookup((edge["upstream_type"], edge["upstream_id"]))
        or f"{edge['upstream_type']}#{edge['upstream_id']}"
        for edge in edges
    ]


__all__ = [
    "BasisLink",
    "DESIGN_CASE",
    "DEVELOPMENT_TASK",
    "FEATURE",
    "record_design_basis",
    "record_task_basis",
    "forget",
    "realign",
    "current_version_number",
    "is_upstream_live",
    "stale_edges_for",
    "stale_edges_by_downstream",
    "annotate",
    "describe",
]
