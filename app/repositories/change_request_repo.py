"""Database access for ChangeRequest."""
from app.models.change_request import ChangeRequest
from app.repositories import soft_delete as _soft
from app.services.numbering import normalize_number


def create(project_id, target_type, target_id, reason, also_changes="[]",
           target_versions="[]"):
    return ChangeRequest.create(
        project=project_id,
        target_type=target_type,
        target_id=target_id,
        also_changes=also_changes,
        target_versions=target_versions,
        reason=reason,
    )


def get(change_request_id):
    return ChangeRequest.get_or_none(
        (ChangeRequest.id == change_request_id) & (ChangeRequest.deleted_at.is_null())
    )


def get_by_number(project_id, change_request_number):
    change_request_number = normalize_number(project_id, change_request_number)
    return ChangeRequest.get_or_none(
        (ChangeRequest.project_id == project_id)
        & (ChangeRequest.change_request_number == change_request_number)
        & (ChangeRequest.deleted_at.is_null())
    )


def get_deleted_by_number(project_id, change_request_number):
    change_request_number = normalize_number(project_id, change_request_number)
    return ChangeRequest.get_or_none(
        (ChangeRequest.project_id == project_id)
        & (ChangeRequest.change_request_number == change_request_number)
        & (ChangeRequest.deleted_at.is_null(False))
    )


def list_by_project(project_id):
    return list(
        ChangeRequest.select()
        .where(
            (ChangeRequest.project_id == project_id)
            & (ChangeRequest.deleted_at.is_null())
        )
        .order_by(ChangeRequest.id)
    )


def list_by_task(task_id):
    return list(
        ChangeRequest.select()
        .where((ChangeRequest.task == task_id) & ChangeRequest.deleted_at.is_null())
        .order_by(ChangeRequest.id)
    )


def clear_task(task_id):
    return ChangeRequest.update(task=None).where(ChangeRequest.task == task_id).execute()


def save(change_request):
    change_request.save()
    return change_request


def soft_delete(change_request):
    return _soft.soft_delete(change_request)


def restore(change_request):
    return _soft.restore(change_request)


def purge(change_request):
    _soft.purge(change_request)


def get_deleted(change_request_id):
    return _soft.get_deleted(ChangeRequest, change_request_id)


def list_deleted_by_project(project_id):
    return _soft.list_deleted_by_project(ChangeRequest, project_id)
