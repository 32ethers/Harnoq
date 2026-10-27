"""SourceRequirement model — the sole requirement layer.

Stores the requirement as free text (``raw_content``), captured verbatim from
the user and then deepened by conversation (edit / append). There is no separate
"normalize into structured REQ" stage anymore: this is live content you keep
editing (while draft) until you confirm it.

Confirmation is a pure status flip — mirroring the shared 3-state lifecycle
(draft ⇄ needs_clarification → confirmed, see
app/services/lifecycle.py) used by DesignCase/ProjectConstitution — with NO
required-field checklist: raw_content stays free text, never structurally
validated. The gate exists only so the pipeline knows "the requirement
discussion is done", not to force a shape on the content. All INPUT-* in a
project must be confirmed before the project can advance past the requirement
stage (see app/api/skills.py::_current_stage).

A SourceRequirement carries no FEATURE/DESIGN link: it is a holistic
requirement document, not an atomic item, so a formal "which FEATURE/DESIGN
covers this" relationship would be either vacuous (every downstream object
trivially "covers" the same document) or force back REQ-item granularity.
Traceability downstream is prose (a FEATURE's name/description, a DESIGN's
problem_statement), established by reading this text at the design stage —
not a DB relationship.
"""
import json

from peewee import CharField, ForeignKeyField, IntegerField, TextField

from app.db.base import SoftDeleteModel, database_proxy
from app.models.project import Project

SOURCE_REQUIREMENT_TYPES = (
    "initial_document",
    "new_feature",
    "requirement_change",
    "bug_report",
    "test_feedback",
    "meeting_minutes",
    "manual_note",
)

# Shared 4-state lifecycle (see app/services/lifecycle.py). No REQUIRED_FOR_
# CONFIRM checklist (unlike DesignCase) — confirm() is a bare status flip.
SOURCE_REQUIREMENT_STATUSES = (
    "draft",
    "needs_clarification",
    "confirmed",
)


class SourceRequirement(SoftDeleteModel):
    project = ForeignKeyField(Project, backref="source_requirements", on_delete="CASCADE")
    source_requirement_number = CharField(null=True)  # external id, per-project e.g. INPUT-12
    title = CharField(null=False)
    status = CharField(default="draft", choices=[(v, v) for v in SOURCE_REQUIREMENT_STATUSES])
    source_type = CharField(choices=[(v, v) for v in SOURCE_REQUIREMENT_TYPES])
    raw_content = TextField(default="")
    attachments = TextField(default="[]")  # JSON-encoded list
    source = TextField(default="")
    submitted_by = CharField(null=True)

    class Meta:
        database = database_proxy
        table_name = "source_requirement"
        indexes = ((("project", "source_requirement_number"), True),)

    def to_dict(self):
        from app.services import work_owner_service
        try:
            attachments = json.loads(self.attachments) if self.attachments else []
        except (ValueError, TypeError):
            attachments = []
        return {
            "id": self.id,
            "source_requirement_number": self.source_requirement_number,
            "project_id": self.project_id,
            "title": self.title,
            "status": self.status,
            "source_type": self.source_type,
            "raw_content": self.raw_content,
            "attachments": attachments,
            "source": self.source,
            "submitted_by": self.submitted_by,
            **work_owner_service.metadata(self),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }


class SourceRequirementVersion(SoftDeleteModel):
    source_requirement = ForeignKeyField(
        SourceRequirement, backref="versions", on_delete="CASCADE"
    )
    version_number = IntegerField()
    change_type = CharField()
    restored_from_version_number = IntegerField(null=True)
    title = CharField(null=False)
    source_type = CharField(choices=[(v, v) for v in SOURCE_REQUIREMENT_TYPES])
    raw_content = TextField(default="")
    attachments = TextField(default="[]")
    source = TextField(default="")
    submitted_by = CharField(null=True)

    class Meta:
        database = database_proxy
        table_name = "source_requirement_version"
        indexes = ((("source_requirement", "version_number"), True),)

    def to_dict(self):
        try:
            attachments = json.loads(self.attachments) if self.attachments else []
        except (ValueError, TypeError):
            attachments = []
        return {
            "id": self.id,
            "source_requirement_id": self.source_requirement_id,
            "version_number": self.version_number,
            "change_type": self.change_type,
            "restored_from_version_number": self.restored_from_version_number,
            "title": self.title,
            "source_type": self.source_type,
            "raw_content": self.raw_content,
            "attachments": attachments,
            "source": self.source,
            "submitted_by": self.submitted_by,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }
