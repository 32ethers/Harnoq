import secrets
from datetime import timedelta, timezone

import jwt
from flask import current_app, g, has_request_context, request
from werkzeug.security import check_password_hash, generate_password_hash

from app.core.errors import ApiError, ValidationError
from app.core.time import utcnow
from app.models.user import AccessCredential, LoginFailure, User

_DUMMY_HASH = generate_password_hash(secrets.token_urlsafe(32))


def current_user():
    return getattr(g, "current_user", None) if has_request_context() else None


def require_admin():
    user = current_user()
    if user is None or user.role != "admin":
        raise ApiError("administrator access required", 403)
    return user


def login(username, password):
    if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password) > 256:
        raise ApiError("invalid username or password", 401)
    username = username.lower()
    remote = (request.remote_addr or "local") if has_request_context() else "local"
    cutoff = utcnow() - timedelta(minutes=10)
    recent = LoginFailure.select().where(LoginFailure.created_at >= cutoff)
    if recent.where(LoginFailure.username == username).count() >= 12 or recent.where(LoginFailure.remote_address == remote).count() >= 60:
        raise ApiError("too many login attempts; retry later", 429)
    user = User.get_or_none(User.username == username.lower()) if isinstance(username, str) else None
    matched = check_password_hash(user.password_hash if user else _DUMMY_HASH, password if isinstance(password, str) else "")
    if not matched or user is None or not user.is_active or user.deleted_at is not None:
        LoginFailure.create(username=username, remote_address=remote)
        raise ApiError("invalid username or password", 401)
    LoginFailure.delete().where((LoginFailure.username == username) | (LoginFailure.created_at < cutoff)).execute()
    return user


def issue(user, *, kind="web", name="", days=30):
    config = current_app.config["HARNOQ_CONFIG"]
    now = utcnow()
    try:
        expires = now + (timedelta(hours=config.jwt_hours) if kind == "web" else timedelta(days=days))
    except OverflowError:
        raise ValidationError("expiration date is outside the supported date range") from None
    jti, csrf = secrets.token_urlsafe(24), secrets.token_urlsafe(24)
    AccessCredential.create(user=user, jti=jti, kind=kind, name=name, expires_at=expires)
    token = jwt.encode({
        "sub": user.user_number, "jti": jti, "kind": kind, "csrf": csrf,
        "iss": "harnoq", "aud": "harnoq", "iat": now.replace(tzinfo=timezone.utc),
        "exp": expires.replace(tzinfo=timezone.utc),
    }, current_app.config["JWT_SECRET"], algorithm="HS256")
    return token, csrf, expires


def authenticate(token):
    # Claims never choose the algorithm or grant a role; live records do.
    try:
        claims = jwt.decode(token, current_app.config["JWT_SECRET"], algorithms=["HS256"],
                            audience="harnoq", issuer="harnoq",
                            options={"require": ["sub", "jti", "kind", "iat", "exp", "csrf"]})
        user = User.get_or_none(User.user_number == claims["sub"])
        credential = AccessCredential.get_or_none(AccessCredential.jti == claims["jti"])
        if (user is None or not user.is_active or user.deleted_at is not None
                or credential is None or credential.user_id != user.id
                or credential.revoked_at is not None or credential.expires_at <= utcnow()
                or credential.kind != claims["kind"]):
            raise ApiError("credential is revoked or unavailable", 401)
        return user, credential, claims
    except (jwt.InvalidTokenError, TypeError, KeyError):
        raise ApiError("invalid or expired JWT", 401) from None
