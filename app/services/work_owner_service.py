from peewee import SqliteDatabase

from app.core.errors import ConflictError, ValidationError
from app.db.base import database_proxy
from app.models.delivery_item import DeliveryItem
from app.models.delivery_stage_run import DeliveryStageRun
from app.models.source_requirement import SourceRequirement
from app.models.work_owner import WorkAssignment, WorkOwner
from app.models.user import User
from app.services import auth_service, user_service

_TARGETS = {SourceRequirement: "source_requirement", DeliveryItem: "delivery_item", DeliveryStageRun: "stage_run"}


def owner(entity):
    return WorkOwner.get_or_none(getattr(WorkOwner, _TARGETS[type(entity)]) == entity.id)


def metadata(entity):
    if hasattr(entity, "_assigned_user_cache"):
        user = entity._assigned_user_cache
    else:
        row = owner(entity)
        user = row.user.to_dict() if row else None
    return {"assignee": user["display_name"] if user else None,
            "assignee_number": user["user_number"] if user else None, "assignee_user": user}


def preload(entities):
    entities = list(entities)
    if not entities:
        return entities
    column = getattr(WorkOwner, _TARGETS[type(entities[0])])
    users = {getattr(row, column.name + "_id"): row.user.to_dict()
             for row in WorkOwner.select(WorkOwner, User).join(User).where(column.in_([entity.id for entity in entities]))}
    for entity in entities:
        entity._assigned_user_cache = users.get(entity.id)
    return entities


def apply(entity, data, *, default_to_actor=False):
    if "assignee_number" not in data and not default_to_actor:
        return
    actor = auth_service.current_user()
    if "assignee_number" not in data:
        if actor is None:
            return
        value = actor.user_number
    else:
        value = data["assignee_number"]
    if value is not None and (not isinstance(value, str) or not value.startswith("USER-")):
        raise ValidationError("assignee_number must be a USER-* number or null")
    with database_proxy.atomic():
        existing = owner(entity)
        user = user_service.get(value, include_deleted=True) if value else None
        previous_id, next_id = existing.user_id if existing else None, user.id if user else None
        if previous_id == next_id:
            return
        if actor and not (default_to_actor and existing is None and user and user.id == actor.id):
            auth_service.require_admin()
        if user and (not user.is_active or user.deleted_at is not None):
            raise ValidationError("cannot assign work to an inactive or deleted user")
        if isinstance(entity, DeliveryStageRun) and existing and entity.status in ("closed", "abandoned"):
            raise ConflictError("finished stage run ownership cannot be changed")
        target = _TARGETS[type(entity)]
        WorkAssignment.create(**{target: entity}, actor=actor, owner=user,
                              previous_owner=existing.user if existing else None,
                              action="assigned" if user else "unassigned")
        if user:
            WorkOwner.insert(**{target: entity}, user=user).on_conflict(
                conflict_target=[getattr(WorkOwner, target)], update={WorkOwner.user: user.id}
            ).execute()
        elif existing:
            existing.delete_instance()
        entity._assigned_user_cache = user.to_dict() if user else None


def assign(entity, data):
    auth_service.require_admin()
    if "assignee_number" not in data:
        raise ValidationError("assignee_number is required; use null to clear")
    # Ownership is administrative metadata, independent of content locks and coverage evidence.
    apply(entity, data)
    return entity


def link_legacy(user_number, *, project_id=None):
    transaction = database_proxy.atomic("IMMEDIATE") if isinstance(database_proxy.obj, SqliteDatabase) else database_proxy.atomic()
    counts = {}
    with transaction:
        user = user_service.get(user_number)
        if not user.is_active:
            raise ValidationError("cannot link historical work to an inactive user")
        for model, target in _TARGETS.items():
            owner_column, event_column = getattr(WorkOwner, target), getattr(WorkAssignment, target)
            entities = model.select().where(
                ~model.id.in_(WorkOwner.select(owner_column).where(owner_column.is_null(False)))
                & ~model.id.in_(WorkAssignment.select(event_column).where(event_column.is_null(False)))
            ).order_by(model.id)
            if project_id is not None:
                entities = entities.where(model.project == project_id)
            if not isinstance(database_proxy.obj, SqliteDatabase):
                entities = entities.for_update()
            counts[target] = 0
            for entity in entities:
                WorkOwner.create(**{target: entity}, user=user)
                WorkAssignment.create(**{target: entity}, owner=user, action="legacy_assigned")
                counts[target] += 1
    return {"user_number": user.user_number, "linked": counts}
