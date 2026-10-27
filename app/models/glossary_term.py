"""Project-level terminology that prevents wording and translation drift."""
from peewee import CharField, ForeignKeyField, TextField

from app.db.base import SoftDeleteModel, database_proxy
from app.models.project import Project


class GlossaryTerm(SoftDeleteModel):
    project = ForeignKeyField(Project, backref="glossary_terms", on_delete="CASCADE")
    term_native = CharField()
    term_en = CharField()
    definition_en = TextField(default="")
    avoid = TextField(default="")

    class Meta:
        database = database_proxy
        table_name = "glossary_term"

    def to_dict(self):
        return {
            "id": self.id,
            "project_id": self.project_id,
            "term_native": self.term_native,
            "term_en": self.term_en,
            "definition_en": self.definition_en,
            "avoid": self.avoid,
        }
