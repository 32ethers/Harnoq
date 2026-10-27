"""Database access for project glossary terms."""
from app.models.glossary_term import GlossaryTerm
from app.repositories import soft_delete as _soft


def create(project_id, **fields):
    return GlossaryTerm.create(project=project_id, **fields)


def get(term_id):
    return GlossaryTerm.get_or_none(
        (GlossaryTerm.id == term_id) & GlossaryTerm.deleted_at.is_null()
    )


def list_by_project(project_id):
    return list(
        GlossaryTerm.select()
        .where(
            (GlossaryTerm.project_id == project_id) & GlossaryTerm.deleted_at.is_null()
        )
        .order_by(GlossaryTerm.id)
    )


def get_by_native(project_id, term_native, *, exclude_term_id=None):
    query = GlossaryTerm.select().where(
        (GlossaryTerm.project_id == project_id)
        & (GlossaryTerm.term_native == term_native)
        & GlossaryTerm.deleted_at.is_null()
    )
    if exclude_term_id is not None:
        query = query.where(GlossaryTerm.id != exclude_term_id)
    return query.first()


def get_by_en(project_id, term_en, *, exclude_term_id=None):
    query = GlossaryTerm.select().where(
        (GlossaryTerm.project_id == project_id)
        & (GlossaryTerm.term_en == term_en)
        & GlossaryTerm.deleted_at.is_null()
    )
    if exclude_term_id is not None:
        query = query.where(GlossaryTerm.id != exclude_term_id)
    return query.first()


def save(term):
    term.save()
    return term


def soft_delete(term):
    return _soft.soft_delete(term)
