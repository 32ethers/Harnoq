"""Database access for DeliveryItem."""
from app.models.delivery_item import DeliveryItem
from app.repositories import soft_delete as _soft
from app.services.numbering import normalize_number


def create(project_id, **fields):
    return DeliveryItem.create(project=project_id, **fields)


def get(item_id):
    return DeliveryItem.get_or_none(
        (DeliveryItem.id == item_id) & DeliveryItem.deleted_at.is_null()
    )


def get_by_number(project_id, item_number):
    item_number = normalize_number(project_id, item_number)
    return DeliveryItem.get_or_none(
        (DeliveryItem.project_id == project_id)
        & (DeliveryItem.item_number == item_number)
        & DeliveryItem.deleted_at.is_null()
    )


def list_by_project(project_id, kinds=None):
    """All live delivery items for a project, oldest first.

    ``kinds`` filters to a subset (the integration/system pages each show a
    fixed subset of KINDS); omitted or empty means no filter.
    """
    query = DeliveryItem.select().where(
        (DeliveryItem.project_id == project_id) & DeliveryItem.deleted_at.is_null()
    )
    if kinds:
        query = query.where(DeliveryItem.kind.in_(list(kinds)))
    return list(query.order_by(DeliveryItem.id))



def save(item):
    item.save()
    return item


def soft_delete(item):
    return _soft.soft_delete(item)
