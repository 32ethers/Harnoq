"""Business rules for project glossary terms.

Glossary changes intentionally do not participate in the planning lifecycle or
trigger downstream review propagation: this is a high-frequency correction
surface, not a governance gate.
"""
from app.core.errors import NotFoundError, ValidationError
from app.repositories import glossary_term_repo, project_repo
from app.services import soft_delete as _softdel

_LABEL = "glossary term"


def _ensure_project(project_id):
    if project_repo.get(project_id) is None:
        raise NotFoundError(f"project {project_id} not found")


def _required(data, field):
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field} is required")
    return value.strip()


def _optional_text(data, field):
    value = data.get(field, "")
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    return value


def _check_unique(project_id, term_native, term_en, *, exclude_term_id=None):
    if glossary_term_repo.get_by_native(
        project_id, term_native, exclude_term_id=exclude_term_id
    ) is not None:
        raise ValidationError("term_native must be unique within a project")
    if glossary_term_repo.get_by_en(
        project_id, term_en, exclude_term_id=exclude_term_id
    ) is not None:
        raise ValidationError("term_en must be unique within a project")


def create(project_id, data):
    _ensure_project(project_id)
    term_native = _required(data, "term_native")
    term_en = _required(data, "term_en")
    _check_unique(project_id, term_native, term_en)
    return glossary_term_repo.create(
        project_id,
        term_native=term_native,
        term_en=term_en,
        definition_en=_optional_text(data, "definition_en"),
        avoid=_optional_text(data, "avoid"),
    )


def get(term_id):
    term = glossary_term_repo.get(term_id)
    if term is None:
        raise NotFoundError(f"{_LABEL} {term_id} not found")
    return term


def list_by_project(project_id):
    _ensure_project(project_id)
    return glossary_term_repo.list_by_project(project_id)


def update(term_id, data):
    term = get(term_id)
    term_native = _required(data, "term_native") if "term_native" in data else term.term_native
    term_en = _required(data, "term_en") if "term_en" in data else term.term_en
    _check_unique(term.project_id, term_native, term_en, exclude_term_id=term.id)
    term.term_native = term_native
    term.term_en = term_en
    for field in ("definition_en", "avoid"):
        if field in data:
            setattr(term, field, _optional_text(data, field))
    return glossary_term_repo.save(term)


def delete(term_id):
    return _softdel.delete(glossary_term_repo, term_id, _LABEL)
