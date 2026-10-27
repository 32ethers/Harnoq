from app.db.base import database_proxy
from app.models.integration_completion_audit import IntegrationCompletionAudit


def replace_active(project_id, validation_id, found_in_round, fingerprint, manifest_json, proposals_json, completed_at=None):
    with database_proxy.atomic():
        existing = IntegrationCompletionAudit.get_or_none(
            (IntegrationCompletionAudit.project_id == project_id)
            & (IntegrationCompletionAudit.validation_id == validation_id)
        )
        IntegrationCompletionAudit.update(active=False).where(IntegrationCompletionAudit.project_id == project_id).execute()
        if existing is not None:
            existing.active = True
            existing.found_in_round = found_in_round
            existing.basis_fingerprint = fingerprint
            existing.manifest_json = manifest_json
            existing.proposed_items_json = proposals_json
            if completed_at is not None and existing.completed_at is None:
                existing.completed_at = completed_at
            existing.save()  # never refresh an established completion time
            return existing
        return IntegrationCompletionAudit.create(project=project_id, validation_id=validation_id, found_in_round=found_in_round, basis_fingerprint=fingerprint, manifest_json=manifest_json, proposed_items_json=proposals_json, completed_at=completed_at)


def get_active(project_id):
    return IntegrationCompletionAudit.select().where(
        (IntegrationCompletionAudit.project_id == project_id) & (IntegrationCompletionAudit.active == True)  # noqa: E712
    ).order_by(IntegrationCompletionAudit.id.desc()).first()


def save(row):
    row.save()
    return row
