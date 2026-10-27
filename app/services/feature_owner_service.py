from peewee import SqliteDatabase

from app.core.errors import ValidationError
from app.db.base import database_proxy
from app.models.feature import Feature
from app.models.feature_owner import FeatureAssignment, FeatureOwner, FeatureVersionOwner
from app.models.user import User
from app.services import auth_service, user_service


def owner(feature_id):
    return FeatureOwner.get_or_none(FeatureOwner.feature == feature_id)


def identity(feature_id):
    row = owner(feature_id)
    return row.user.to_dict() if row else None


def apply(feature, data):
    if "assignee_number" not in data and "assignee" not in data:
        return
    if "assignee_number" in data and "assignee" in data:
        raise ValidationError("provide assignee_number only, not both assignee fields")
    existing = owner(feature.id)
    value = data.get("assignee_number", data.get("assignee"))
    actor = auth_service.current_user()
    if "assignee_number" not in data and actor is None:
        if existing:
            existing.delete_instance()
        feature.assignee = value or None
        return
    if value is not None and (not isinstance(value, str) or not value.strip()):
        raise ValidationError("assignee_number must be a USER-* number or null")
    user = None
    if value is not None:
        if "assignee_number" in data or value.startswith("USER-"):
            user = user_service.get(value, include_deleted=True)
        else:
            # Old clients may send an exact account name; never accept arbitrary person/agent labels.
            user = User.get_or_none((User.username == value.lower()) & User.deleted_at.is_null())
            if user is None:
                raise ValidationError("assignee must identify a registered account; use assignee_number")
    previous_id, next_id = existing.user_id if existing else None, user.id if user else None
    if previous_id != next_id and actor is not None:
        auth_service.require_admin()
    if user and previous_id != next_id and (not user.is_active or user.deleted_at is not None):
        raise ValidationError("cannot assign work to an inactive user")
    if previous_id != next_id:
        FeatureAssignment.create(feature=feature, actor=actor, owner=user,
                                 previous_owner=existing.user if existing else None,
                                 action="assigned" if user else "unassigned")
    if user:
        FeatureOwner.insert(feature=feature, user=user).on_conflict(
            conflict_target=[FeatureOwner.feature], update={FeatureOwner.user: user.id}
        ).execute()
        feature.assignee = user.display_name
    else:
        if existing:
            existing.delete_instance()
        feature.assignee = None


def snapshot(feature, version):
    row = owner(feature.id)
    if row:
        FeatureVersionOwner.create(version=version, user=row.user)


def restore(feature, version):
    row = FeatureVersionOwner.get_or_none(FeatureVersionOwner.version == version)
    if row:
        apply(feature, {"assignee_number": row.user.user_number})
        feature.assignee = version.assignee
    else:
        existing = owner(feature.id)
        if existing:
            if auth_service.current_user() is not None:
                auth_service.require_admin()
            FeatureAssignment.create(feature=feature, actor=auth_service.current_user(),
                                     previous_owner=existing.user, action="unassigned")
            existing.delete_instance()
        # A pre-user snapshot has only text; restoring it must not guess a user's identity.
        feature.assignee = version.assignee


def link_legacy_features(user_number, *, project_id=None):
    transaction = database_proxy.atomic("IMMEDIATE") if isinstance(database_proxy.obj, SqliteDatabase) else database_proxy.atomic()
    with transaction:
        user = user_service.get(user_number)
        if not user.is_active:
            raise ValidationError("cannot link historical work to an inactive user")
        tracked = FeatureAssignment.select(FeatureAssignment.feature)
        features = Feature.select().where(
            ~Feature.id.in_(FeatureOwner.select(FeatureOwner.feature))
            & ~Feature.id.in_(tracked)
        ).order_by(Feature.id)
        if project_id is not None:
            features = features.where(Feature.project_id == project_id)
        if not isinstance(database_proxy.obj, SqliteDatabase):
            features = features.for_update()
        linked = 0
        for feature in features:
            FeatureOwner.create(feature=feature, user=user)
            FeatureAssignment.create(feature=feature, owner=user, action="legacy_assigned")
            # Keep the legacy display cache usable by clients while they reload the new account relation.
            Feature.update(assignee=user.display_name).where(Feature.id == feature.id).execute()
            linked += 1
    return {"user_number": user.user_number, "linked_features": linked}
