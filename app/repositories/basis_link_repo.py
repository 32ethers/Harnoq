"""Database access for BasisLink."""
from app.models.basis_link import BasisLink


def replace_for_downstream(project_id, downstream_type, downstream_id, edges):
    """Rewrite a downstream object's whole basis in one shot.

    ``edges`` is an iterable of ``(upstream_type, upstream_id, version_number)``.
    Replacing wholesale (rather than upserting one edge at a time) is what makes
    a re-confirm correct when the set of upstreams itself changed — a design
    that stopped covering a REQ item must not keep that item's stale edge.
    """
    delete_for_downstream(downstream_type, downstream_id)
    rows = [
        {
            "project": project_id,
            "downstream_type": downstream_type,
            "downstream_id": downstream_id,
            "upstream_type": upstream_type,
            "upstream_id": upstream_id,
            "basis_version_number": version_number,
        }
        for upstream_type, upstream_id, version_number in edges
    ]
    if rows:
        BasisLink.insert_many(rows).execute()
    return list_for_downstream(downstream_type, downstream_id)


def delete_for_downstream(downstream_type, downstream_id):
    return (
        BasisLink.delete()
        .where(
            (BasisLink.downstream_type == downstream_type)
            & (BasisLink.downstream_id == downstream_id)
        )
        .execute()
    )


def list_for_downstream(downstream_type, downstream_id):
    return list(
        BasisLink.select()
        .where(
            (BasisLink.downstream_type == downstream_type)
            & (BasisLink.downstream_id == downstream_id)
        )
        .order_by(BasisLink.upstream_type, BasisLink.upstream_id)
    )


def list_by_project(project_id):
    """Every basis edge in a project — one query, so a list page or
    project-context can compute suspect for all of them without a query per
    object."""
    return list(
        BasisLink.select()
        .where(BasisLink.project == project_id)
        .order_by(BasisLink.downstream_type, BasisLink.downstream_id)
    )
