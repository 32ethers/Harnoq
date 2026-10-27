import re

from peewee import IntegrityError, SqliteDatabase
from werkzeug.security import generate_password_hash

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.time import utcnow
from app.db.base import database_proxy
from app.models.user import AccessCredential, User
from app.services.numbering import allocate


def get(number, *, include_deleted=False):
    query = User.user_number == number
    if not include_deleted:
        query &= User.deleted_at.is_null()
    user = User.get_or_none(query)
    if user is None:
        raise NotFoundError("user not found")
    return user


def list_users():
    return list(User.select().where(User.deleted_at.is_null()).order_by(User.id))


def _password_hash(password):
    if not isinstance(password, str) or not password or len(password) > 256:
        raise ValidationError("password is required (maximum 256 characters)")
    return generate_password_hash(password)


def create(data):
    username = data.get("username", "")
    if not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{3,64}", username):
        raise ValidationError("username must contain 3 to 64 letters, digits, dots, underscores or hyphens")
    name = data.get("display_name")
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        raise ValidationError("display_name is required (maximum 128 characters)")
    role = data.get("role", "member")
    if role not in ("admin", "member"):
        raise ValidationError("role must be admin or member")
    active = data.get("is_active", True)
    if not isinstance(active, bool):
        raise ValidationError("is_active must be a boolean")
    hashed = _password_hash(data.get("password"))
    try:
        with database_proxy.atomic():
            return User.create(
                user_number=f"USER-{allocate('USER')}", username=username.lower(),
                display_name=name.strip(), password_hash=hashed, role=role, is_active=active,
            )
    except IntegrityError:
        raise ConflictError("username already exists") from None


def revoke_all(user):
    AccessCredential.update(revoked_at=utcnow()).where(
        (AccessCredential.user == user) & AccessCredential.revoked_at.is_null()
    ).execute()


def update(number, data):
    transaction = database_proxy.atomic("IMMEDIATE") if isinstance(database_proxy.obj, SqliteDatabase) else database_proxy.atomic()
    with transaction:
        user = get(number)
        role = data.get("role", user.role)
        active = data.get("is_active", user.is_active)
        if role not in ("admin", "member") or not isinstance(active, bool):
            raise ValidationError("invalid role or is_active")
        if user.role == "admin" and user.is_active and (role != "admin" or not active):
            administrators = User.select(User.id).where(
                (User.role == "admin") & User.is_active & User.deleted_at.is_null()
            ).order_by(User.id)
            # Lock the candidate set so simultaneous demotions cannot remove every admin.
            if not isinstance(database_proxy.obj, SqliteDatabase):
                administrators = administrators.for_update()
            if len(list(administrators)) <= 1:
                raise ConflictError("cannot remove or disable the last active administrator")
        name = data.get("display_name", user.display_name)
        if not isinstance(name, str) or not name.strip() or len(name) > 128:
            raise ValidationError("invalid display_name")
        if "password" in data:
            user.password_hash = _password_hash(data["password"])
            revoke_all(user)
        if role != user.role or not active:
            revoke_all(user)
        user.role, user.is_active, user.display_name = role, active, name.strip()
        user.save()
        return user


def delete(number):
    with database_proxy.atomic():
        user = update(number, {"is_active": False})
        user.deleted_at = utcnow()
        user.save()
        return user
