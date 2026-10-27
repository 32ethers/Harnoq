"""Persistence for System inventory receipts and their creation consumption."""
from app.db.base import database_proxy
from app.models.system_inventory_validation import SystemInventoryValidation


def replace_active(project_id, validation_id, found_in_round, fingerprint, manifest_json, proposed_items_json):
    with database_proxy.atomic():
        SystemInventoryValidation.update(active=False).where(
            SystemInventoryValidation.project_id == project_id
        ).execute()
        return SystemInventoryValidation.create(
            project=project_id, validation_id=validation_id,
            found_in_round=found_in_round, basis_fingerprint=fingerprint,
            manifest_json=manifest_json, proposed_items_json=proposed_items_json,
        )


def get_active(project_id, validation_id=None):
    query = SystemInventoryValidation.select().where(
        (SystemInventoryValidation.project_id == project_id)
        & (SystemInventoryValidation.active == True)  # noqa: E712
    )
    if validation_id is not None:
        query = query.where(SystemInventoryValidation.validation_id == validation_id)
    return query.order_by(SystemInventoryValidation.id.desc()).first()


def save(row):
    row.save()
    return row
