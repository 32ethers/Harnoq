"""Project model — the root of all phase1 data."""
from peewee import CharField, TextField

from app.db.base import SoftDeleteModel, database_proxy

PROJECT_STATUSES = ("active", "archived")


class Project(SoftDeleteModel):
    project_number = CharField(null=True, unique=True)  # external id, e.g. P12
    name = CharField(null=False)
    description = TextField(default="")
    status = CharField(default="active", choices=[(v, v) for v in PROJECT_STATUSES])
    repo_url = CharField(default="")

    class Meta:
        database = database_proxy
        table_name = "project"

    def to_dict(self):
        return {
            "id": self.id,
            "project_number": self.project_number,
            "name": self.name,
            "description": self.description,
            "status": self.status,
            "repo_url": self.repo_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "deleted_at": self.deleted_at.isoformat() if self.deleted_at else None,
        }
