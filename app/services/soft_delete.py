"""Generic soft-delete service operations shared by the spine entities.

Each entity's service exposes thin ``delete``/``restore``/``purge`` wrappers
over these helpers so the NotFound semantics stay identical everywhere:

* ``delete`` operates on a *live* row (``repo.get``) and moves it to the bin.
* ``restore`` and ``purge`` operate on a row already *in the bin*
  (``repo.get_deleted``). Requiring a prior soft delete makes permanent
  deletion a deliberate two-step action, which pairs with the UI's
  type-the-name confirmation.
"""
from app.core.errors import NotFoundError


def delete(repo, entity_id, label):
    entity = repo.get(entity_id)
    if entity is None:
        raise NotFoundError(f"{label} {entity_id} not found")
    return repo.soft_delete(entity)


def restore(repo, entity_id, label):
    entity = repo.get_deleted(entity_id)
    if entity is None:
        raise NotFoundError(f"{label} {entity_id} not in recycle bin")
    return repo.restore(entity)


def purge(repo, entity_id, label):
    entity = repo.get_deleted(entity_id)
    if entity is None:
        raise NotFoundError(f"{label} {entity_id} not in recycle bin")
    repo.purge(entity)
