"""BasisLink: what version of its upstream a downstream object was settled on.

This is the storage half of *suspect links* (see docs/cn/design-evolution.md#changes). When a DesignCase
is confirmed we record the version number of the FEATURE-* it sits in — its
only upstream since the REQ-* layer was removed — and when a task is
created we record the version of the design it was created from. Nothing else
is written: whether an edge is *suspect* is then a comparison done at read time
(upstream's current version > the recorded basis), never a stored flag.

Why an edge table rather than columns: an edge is what actually goes stale.
Putting the fact on the edge is also what lets the
downstream object's own ``status`` go back to describing only itself — the
mistake ``needs_review`` made (see app/services/lifecycle.py).

Rows are hard-deleted and rewritten when a downstream object is re-confirmed or
realigned: this table is the CURRENT basis, not a history. The history of the
objects themselves already lives in their version tables.
"""
from peewee import CharField, ForeignKeyField, IntegerField

from app.db.base import TimestampedModel, database_proxy
from app.models.project import Project

#: Object types that can sit at either end of a basis edge. Plain strings
#: rather than FKs: the table is deliberately generic so a new edge (say
#: CHANGE REQUEST -> anything) needs no schema change.
BASIS_OBJECT_TYPES = (
    "feature",
    "design_case",
    "development_task",
)


class BasisLink(TimestampedModel):
    project = ForeignKeyField(Project, backref="basis_links", on_delete="CASCADE")
    downstream_type = CharField(choices=[(v, v) for v in BASIS_OBJECT_TYPES])
    downstream_id = IntegerField()
    upstream_type = CharField(choices=[(v, v) for v in BASIS_OBJECT_TYPES])
    upstream_id = IntegerField()
    #: The upstream's version number at the moment the downstream was settled.
    basis_version_number = IntegerField()

    class Meta:
        database = database_proxy
        table_name = "basis_link"
        indexes = (
            (("downstream_type", "downstream_id", "upstream_type", "upstream_id"), True),
            (("project", "downstream_type"), False),
        )

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "downstream_type": self.downstream_type,
            "downstream_id": self.downstream_id,
            "upstream_type": self.upstream_type,
            "upstream_id": self.upstream_id,
            "basis_version_number": self.basis_version_number,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
