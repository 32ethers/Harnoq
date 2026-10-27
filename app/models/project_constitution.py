"""Project constitution: a per-project governance record harvested from the
confirmed INPUT-* set and consumed by every downstream stage (feature / design /
task). One record per project (1:1), not decomposed into items; it has a
confirm gate (design waits on it) but no multi-round lifecycle."""
from peewee import (
    CharField,
    DateTimeField,
    ForeignKeyField,
    IntegerField,
    TextField,
)

from app.db.base import SoftDeleteModel, database_proxy
from app.models.project import Project

CONSTITUTION_STATUSES = (
    "draft",
    "needs_clarification",
    "confirmed",
)

# Content fields captured in each version snapshot. status/confirmed_at are
# lifecycle, not content, so they are excluded (mirrors DesignCase).
_CONTENT_FIELDS = (
    "system_nature",
    "fixed_stack",
    "quality_bar",
    "ui_conventions",
    "nfr_baseline",
    "boundaries",
)


class ProjectConstitution(SoftDeleteModel):
    project = ForeignKeyField(
        Project,
        backref="constitution",
        on_delete="CASCADE",
        unique=True,  # 1:1 — exactly one constitution per project
    )
    status = CharField(
        default="draft", choices=[(v, v) for v in CONSTITUTION_STATUSES]
    )
    system_nature = TextField(default="")   # 系统性质与目标
    fixed_stack = TextField(default="")      # 固定技术栈
    quality_bar = TextField(default="")      # 质量线与测试标准
    ui_conventions = TextField(default="")   # 界面与交互规范
    nfr_baseline = TextField(default="")     # 非功能基线
    boundaries = TextField(default="")       # 边界: always / ask-first / never
    confirmed_at = DateTimeField(null=True)

    class Meta:
        database = database_proxy
        table_name = "project_constitution"

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "status": self.status,
            "system_nature": self.system_nature,
            "fixed_stack": self.fixed_stack,
            "quality_bar": self.quality_bar,
            "ui_conventions": self.ui_conventions,
            "nfr_baseline": self.nfr_baseline,
            "boundaries": self.boundaries,
            "confirmed_at": self.confirmed_at.isoformat()
            if self.confirmed_at
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }


class ProjectConstitutionVersion(SoftDeleteModel):
    project_constitution = ForeignKeyField(
        ProjectConstitution, backref="versions", on_delete="CASCADE"
    )
    version_number = IntegerField()
    change_type = CharField()
    restored_from_version_number = IntegerField(null=True)
    system_nature = TextField(default="")
    fixed_stack = TextField(default="")
    quality_bar = TextField(default="")
    ui_conventions = TextField(default="")
    nfr_baseline = TextField(default="")
    boundaries = TextField(default="")

    class Meta:
        database = database_proxy
        table_name = "project_constitution_version"
        indexes = ((("project_constitution", "version_number"), True),)

    def to_dict(self):
        return {
            "id": self.id,
            "project_constitution_id": self.project_constitution_id,
            "version_number": self.version_number,
            "change_type": self.change_type,
            "restored_from_version_number": self.restored_from_version_number,
            "system_nature": self.system_nature,
            "fixed_stack": self.fixed_stack,
            "quality_bar": self.quality_bar,
            "ui_conventions": self.ui_conventions,
            "nfr_baseline": self.nfr_baseline,
            "boundaries": self.boundaries,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }
