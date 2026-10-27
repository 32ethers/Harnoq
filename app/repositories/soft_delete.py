"""Shared soft-delete data access for the recycle-bin feature.

These helpers operate on any :class:`~app.db.base.SoftDeleteModel` so each
entity's repository doesn't re-implement the same ``deleted_at`` bookkeeping.
Per-entity repos still own their normal ``get``/``list`` queries (which filter
``deleted_at IS NULL``); this module owns the deletion lifecycle and the
recycle-bin reads.

Soft delete is *shallow*: only the target row is stamped. Children keep their
own ``deleted_at`` state, so restoring a parent brings back exactly what was
removed. Purge, by contrast, is a real delete that relies on the SQLite
``ON DELETE CASCADE`` foreign keys to remove the whole subtree.
"""
from app.core.time import utcnow


def soft_delete(entity):
    """Move a live row to the recycle bin. No-op if already deleted."""
    if entity.deleted_at is None:
        entity.deleted_at = utcnow()
        entity.save()
    return entity


def restore(entity):
    """Bring a soft-deleted row back to live state."""
    if entity.deleted_at is not None:
        entity.deleted_at = None
        entity.save()
    return entity


def purge(entity):
    """Permanently delete a row and its subtree via ON DELETE CASCADE."""
    entity.delete_instance()


def get_deleted(model, entity_id):
    """Fetch a single soft-deleted row by id, or ``None``."""
    return model.get_or_none(
        (model.id == entity_id) & (model.deleted_at.is_null(False))
    )


def list_deleted_by_project(model, project_id):
    """List a project's soft-deleted rows for one entity type."""
    return list(
        model.select()
        .where(
            (model.project_id == project_id) & (model.deleted_at.is_null(False))
        )
        .order_by(model.id)
    )
