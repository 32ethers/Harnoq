from flask import Blueprint, current_app, g, jsonify, request

from app.core.errors import ApiError, ValidationError
from app.core.http import json_object
from app.core.time import utcnow
from app.models.user import AccessCredential
from app.services import auth_service, user_service

bp = Blueprint("auth", __name__)


@bp.post("/auth/login")
def login():
    data = json_object()
    user = auth_service.login(data.get("username"), data.get("password"))
    token, csrf, expires = auth_service.issue(user)
    response = jsonify({"user": user.to_dict(), "access_token": token, "token_type": "Bearer", "expires_at": expires.isoformat(), "csrf_token": csrf})
    response.set_cookie("harnoq_access", token, httponly=True, secure=request.is_secure,
                        samesite="Lax", max_age=current_app.config["HARNOQ_CONFIG"].jwt_hours * 3600)
    g.current_user = user
    return response


@bp.get("/auth/me")
def me():
    user = auth_service.current_user()
    if user is None:
        raise ApiError("login required", 401)
    return jsonify({"user": user.to_dict(), "csrf_token": g.jwt_claims["csrf"]})


@bp.post("/auth/logout")
def logout():
    g.credential.revoked_at = utcnow()
    g.credential.save()
    response = jsonify({"status": "logged_out"})
    response.delete_cookie("harnoq_access")
    return response


@bp.post("/auth/password")
def change_password():
    data = json_object()
    user = auth_service.current_user()
    auth_service.login(user.username, data.get("current_password"))
    user_service.update(user.user_number, {"password": data.get("password")})
    response = jsonify({"status": "password_changed"})
    response.delete_cookie("harnoq_access")
    return response


@bp.get("/auth/tokens")
def tokens():
    return jsonify([row.to_dict() for row in AccessCredential.select().where(
        (AccessCredential.user == auth_service.current_user()) & (AccessCredential.kind == "api")
    ).order_by(AccessCredential.id.desc())])


@bp.post("/auth/tokens")
def create_token():
    data = json_object()
    days, name = data.get("days", 30), data.get("name", "")
    if not isinstance(days, int) or isinstance(days, bool) or days < 1:
        raise ValidationError("days must be a positive integer")
    if not isinstance(name, str) or not name.strip() or len(name) > 128:
        raise ValidationError("token name is required (maximum 128 characters)")
    token, _, expires = auth_service.issue(auth_service.current_user(), kind="api", name=name.strip(), days=days)
    return jsonify({"access_token": token, "expires_at": expires.isoformat(), "token_type": "Bearer"}), 201


@bp.delete("/auth/tokens/<credential_number>")
def revoke_token(credential_number):
    credential = AccessCredential.get_or_none(
        (AccessCredential.jti == credential_number.removeprefix("TOKEN-"))
        & (AccessCredential.user == auth_service.current_user()) & (AccessCredential.kind == "api")
    )
    if credential is None:
        raise ApiError("credential not found", 404)
    credential.revoked_at = utcnow()
    credential.save()
    return jsonify({"status": "revoked"})
