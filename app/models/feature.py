"""Feature models: Feature slices and project foundation design carriers.

Each Feature is one block, carved by information hiding / bounded context
(see docs/cn/design-evolution.md#boundaries). Two orthogonal tags describe an
already-carved block — they are labels, not the cutting tool:

- ``orientation``: ``foundation`` (a project-level platform layer, not used
  directly by end users) vs ``vertical`` (a complete user-visible slice).
- ``subdomain_type`` (DDD subdomain type): ``core`` (system's raison d'etre,
  hardest, framework-agnostic, must be spiked first) / ``supporting``
  (needed but not differentiating) / ``generic`` (industry-solved,
  buy/reuse/thin-wrap).

A Feature carries no formal link to INPUT-*: INPUT-* is a holistic
requirement document (not an atomic item), so "which INPUT does this feature
own" would be either vacuous (every feature in the project trivially
"covers" the same document) or force back the REQ-item granularity this
pipeline moved away from. A Feature's traceability to the requirement is
prose (name/description), established by reading the INPUT-* material at the
design stage — not a DB relationship or a partition/coverage gate.

``foundation_kind`` identifies project-wide architecture and UI design
foundations. Integration and System are project-wide delivery stages, not
Features. The field is nullable for ordinary foundations and vertical slices.

``is_spike`` flags a ``core`` block whose first development task must be a walking
skeleton. ``FeatureDependency`` records the block dependency graph ("who
depends on whom") consumed by task-planning topological ordering and by the UI's
dependency-spine display.
"""
import json

from peewee import BooleanField, CharField, ForeignKeyField, IntegerField, TextField

from app.db.base import SoftDeleteModel, database_proxy
from app.models.project import Project

ORIENTATIONS = ("foundation", "vertical")
SUBDOMAIN_TYPES = ("core", "supporting", "generic")
FOUNDATION_KINDS = ("architecture", "ui")


class Feature(SoftDeleteModel):
    project = ForeignKeyField(Project, backref="features", on_delete="CASCADE")
    feature_number = CharField(null=True)  # external id, per-project e.g. FEATURE-12
    name = CharField(null=False)
    description = TextField(default="")
    assignee = CharField(null=True)
    orientation = CharField(default="vertical", choices=[(v, v) for v in ORIENTATIONS])
    foundation_kind = CharField(
        null=True, choices=[(v, v) for v in FOUNDATION_KINDS]
    )
    subdomain_type = CharField(
        default="supporting", choices=[(v, v) for v in SUBDOMAIN_TYPES]
    )
    is_spike = BooleanField(default=False)

    class Meta:
        database = database_proxy
        table_name = "feature"
        indexes = ((("project", "feature_number"), True),)

    def to_dict(self, dependencies=None, design_cases=None):
        from app.services import feature_owner_service
        assigned_user = feature_owner_service.identity(self.id)
        # ``dependencies`` / ``design_cases`` may be preloaded by a batch
        # serializer (dict_ctx, one query each for the whole list) to avoid
        # an N+1 — a single call self-queries both as before.
        if dependencies is None:
            dependencies = _live_feature_dependencies_for(self.id)
        if design_cases is None:
            from app.models.design_case import DesignCase

            design_cases = list(
                DesignCase.select()
                .where(
                    (DesignCase.feature_id == self.id)
                    & DesignCase.deleted_at.is_null()
                )
                .order_by(DesignCase.id)
            )
        design_numbers = [design_case.design_number for design_case in design_cases]
        if not design_cases:
            design_status = "planned"
        elif all(design_case.status == "confirmed" for design_case in design_cases):
            design_status = "confirmed"
        else:
            design_status = "in_design"
        # Feature completion only says its own planned work is built. Project
        # delivery is reported separately by delivery_gate; calling both
        # states "done" was misleading.
        from app.repositories import development_task_repo
        # Use the same Feature membership as the claim gate: a task generated
        # from one of this Feature's designs may not have a direct Feature FK.
        tasks = development_task_repo.list_by_feature(self.id)
        task_count = len(tasks)
        completed_count = sum(task.status == "completed" for task in tasks)
        implementation_status = (
            "not_started" if not task_count else "implemented" if task_count == completed_count else "in_progress"
        )
        # Kept for older UI consumers, but never call a merely designed Feature
        # done. Consumers should use the explicit pair below.
        progress = "implemented" if implementation_status == "implemented" else design_status
        return {
            "id": self.id,
            "project_id": self.project_id,
            "feature_number": self.feature_number,
            "name": self.name,
            "description": self.description,
            "assignee": assigned_user["display_name"] if assigned_user else self.assignee,
            "assignee_number": assigned_user["user_number"] if assigned_user else None,
            "assignee_user": assigned_user,
            "orientation": self.orientation,
            "foundation_kind": self.foundation_kind,
            "subdomain_type": self.subdomain_type,
            "is_spike": self.is_spike,
            "design_case_ids": [design_case.id for design_case in design_cases],
            "design_numbers": design_numbers,
            "depends_on_feature_ids": [
                dependency.depends_on_feature_id for dependency in dependencies
            ],
            "depends_on_feature_numbers": [
                dependency.depends_on_feature.feature_number for dependency in dependencies
            ],
            "progress": progress,
            "design_status": design_status,
            "implementation_status": implementation_status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }


FEATURE_VERSIONED_FIELDS = (
    "name",
    "description",
    "assignee",
    "orientation",
    "foundation_kind",
    "subdomain_type",
    "is_spike",
)


def _loads_list(value):
    try:
        parsed = json.loads(value) if value else []
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


class FeatureVersion(SoftDeleteModel):
    """Immutable content/dependency snapshot of a FEATURE-*.

    Dependency ids stay internal so a restored snapshot can rebuild the graph.
    Their FEATURE-* numbers are captured alongside them for the user-facing
    history and front-end comparison, including after a dependency is deleted.
    """

    feature = ForeignKeyField(Feature, backref="versions", on_delete="CASCADE")
    version_number = IntegerField()
    change_type = CharField()
    restored_from_version_number = IntegerField(null=True)
    name = CharField(null=False)
    description = TextField(default="")
    assignee = CharField(null=True)
    orientation = CharField(choices=[(v, v) for v in ORIENTATIONS])
    foundation_kind = CharField(null=True, choices=[(v, v) for v in FOUNDATION_KINDS])
    subdomain_type = CharField(choices=[(v, v) for v in SUBDOMAIN_TYPES])
    is_spike = BooleanField(default=False)
    depends_on_feature_ids = TextField(default="[]")
    depends_on_feature_numbers = TextField(default="[]")

    class Meta:
        database = database_proxy
        table_name = "feature_version"
        indexes = ((("feature", "version_number"), True),)

    def to_dict(self):
        from app.models.feature_owner import FeatureVersionOwner
        row = FeatureVersionOwner.get_or_none(FeatureVersionOwner.version == self)
        return {
            "version_number": self.version_number,
            "change_type": self.change_type,
            "restored_from_version_number": self.restored_from_version_number,
            "name": self.name,
            "description": self.description,
            "assignee": self.assignee,
            "assignee_number": row.user.user_number if row else None,
            "orientation": self.orientation,
            "foundation_kind": self.foundation_kind,
            "subdomain_type": self.subdomain_type,
            "is_spike": self.is_spike,
            "depends_on_feature_numbers": _loads_list(
                self.depends_on_feature_numbers
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class FeatureDependency(SoftDeleteModel):
    """One edge of the block Context Map: ``feature`` depends on
    ``depends_on_feature``. Soft-deleted
    so an edge toggled off and back on later leaves a trace — there is at
    most one row per ordered pair, ever; removing/re-adding an edge flips
    ``deleted_at`` on that same row instead of inserting a duplicate, which
    is what keeps the (feature, depends_on_feature) uniqueness below
    meaningful across the edge's whole history.
    """

    feature = ForeignKeyField(Feature, backref="dependency_links", on_delete="CASCADE")
    depends_on_feature = ForeignKeyField(
        Feature, backref="dependent_links", on_delete="CASCADE"
    )

    class Meta:
        database = database_proxy
        table_name = "feature_dependency"
        indexes = ((("feature", "depends_on_feature"), True),)

    def to_dict(self):
        return {
            "id": self.id,
            "feature_id": self.feature_id,
            "depends_on_feature_id": self.depends_on_feature_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }


def _live_feature_dependency_query():
    return (
        FeatureDependency.select(FeatureDependency, Feature)
        .join(Feature, on=(FeatureDependency.depends_on_feature == Feature.id))
        .where(Feature.deleted_at.is_null() & FeatureDependency.deleted_at.is_null())
        .order_by(FeatureDependency.id)
    )


def _live_feature_dependencies_for(feature_id):
    return list(
        _live_feature_dependency_query().where(
            FeatureDependency.feature_id == feature_id
        )
    )
