"""Persistence for server-issued Integration inventory validation receipts."""

from app.db.base import database_proxy
from app.models.integration_inventory_validation import IntegrationInventoryValidation


def replace_active(project_id, validation_id, found_in_round, proposed_items_json):
    with database_proxy.atomic():
        IntegrationInventoryValidation.update(active=False).where(
            IntegrationInventoryValidation.project_id == project_id
        ).execute()
        row = IntegrationInventoryValidation.get_or_none(
            (IntegrationInventoryValidation.project_id == project_id)
            & (IntegrationInventoryValidation.validation_id == validation_id)
        )
        if row is None:
            return IntegrationInventoryValidation.create(
                project=project_id,
                validation_id=validation_id,
                found_in_round=found_in_round,
                proposed_items_json=proposed_items_json,
                active=True,
            )
        row.found_in_round = found_in_round
        row.proposed_items_json = proposed_items_json
        row.active = True
        row.save()
        return row


def get_active(project_id, validation_id):
    return IntegrationInventoryValidation.get_or_none(
        (IntegrationInventoryValidation.project_id == project_id)
        & (IntegrationInventoryValidation.validation_id == validation_id)
        & (IntegrationInventoryValidation.active == True)  # noqa: E712
    )


def save(row):
    row.save()
    return row
