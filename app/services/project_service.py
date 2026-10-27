"""Project business logic."""
from app.core.errors import NotFoundError, ValidationError
from app.models.project import PROJECT_STATUSES
from app.repositories import project_repo
from app.services import numbering, soft_delete as _softdel

_LABEL = "project"


def _validate_name(name):
    if not isinstance(name, str) or not name.strip():
        raise ValidationError("name is required")


def create(data):
    _validate_name(data.get("name"))
    description = data.get("description") or ""
    repo_url = data.get("repo_url") or ""
    project = project_repo.create(
        name=data["name"].strip(), description=description, repo_url=repo_url
    )
    return numbering.assign_number(project, "P", "project_number")


def get(project_id):
    project = project_repo.get(project_id)
    if project is None:
        raise NotFoundError(f"project {project_id} not found")
    return project


def get_by_number(project_number):
    project = project_repo.get_by_number(project_number)
    if project is None:
        raise NotFoundError(f"project {project_number} not found")
    return project


def list_all():
    return project_repo.list_all()


def update(project_id, data):
    project = get(project_id)
    if "name" in data:
        _validate_name(data["name"])
        project.name = data["name"].strip()
    if "description" in data:
        project.description = data["description"] or ""
    if "repo_url" in data:
        project.repo_url = data["repo_url"] or ""
    if "status" in data:
        if data["status"] not in PROJECT_STATUSES:
            raise ValidationError(f"invalid status: {data['status']}")
        project.status = data["status"]
    return project_repo.save(project)


def delete(project_id):
    return _softdel.delete(project_repo, project_id, _LABEL)


def restore(project_id):
    return _softdel.restore(project_repo, project_id, _LABEL)


def purge(project_id):
    _softdel.purge(project_repo, project_id, _LABEL)
