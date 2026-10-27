import os
import secrets
from pathlib import Path

from flask import g, redirect, request, url_for

from app.core.errors import ApiError
from app.models.user import AuditRecord
from app.services import auth_service


def init_auth(app):
    config = app.config["HARNOQ_CONFIG"]
    secret = config.jwt_secret
    if not secret:
        if config.testing:
            secret = secrets.token_urlsafe(48)
        else:
            path = Path(app.instance_path) / "jwt-secret"
            path.parent.mkdir(parents=True, exist_ok=True)
            try:
                descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                secret = path.read_text().strip()
            else:
                secret = secrets.token_urlsafe(48)
                with os.fdopen(descriptor, "w") as output:
                    output.write(secret)
    if len(secret.encode()) < 32:
        raise ValueError("HARNOQ_JWT_SECRET must contain at least 32 bytes")
    app.config["JWT_SECRET"] = secret

    @app.before_request
    def authenticate_request():
        g.current_user = None
        if not config.auth_required or request.method == "OPTIONS":
            return None
        public = request.endpoint in ("static", "health.healthz", "web.login_page", "auth.login")
        public = public or (request.method == "GET" and request.path in ("/healthz", "/openapi.json", "/openapi.yaml"))
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("Origin")
            if origin and origin.rstrip("/") != request.host_url.rstrip("/") and origin not in config.allowed_origins:
                raise ApiError("origin is not allowed", 403)
        if public:
            return None
        authorization = request.headers.get("Authorization", "")
        if authorization and not authorization.lower().startswith("bearer "):
            raise ApiError("Bearer JWT required", 401)
        bearer = authorization.lower().startswith("bearer ")
        token = authorization[7:] if bearer else request.cookies.get("harnoq_access")
        try:
            if not token:
                raise ApiError("login required", 401)
            user, credential, claims = auth_service.authenticate(token)
        except ApiError:
            if request.method == "GET" and (request.path == "/" or request.path.startswith("/ui/")):
                return redirect(url_for("web.login_page", next=request.full_path.rstrip("?")))
            raise
        g.current_user, g.credential, g.jwt_claims = user, credential, claims
        if not bearer and request.method not in ("GET", "HEAD", "OPTIONS"):
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), claims["csrf"]):
                raise ApiError("invalid CSRF token", 403)
        if user.role != "admin" and request.method not in ("GET", "HEAD", "OPTIONS") and request.view_args:
            from app.services import development_task_service, project_service, task_owner_service
            args = request.view_args
            task = None
            if "task_id" in args:
                task = development_task_service.get(args["task_id"])
            elif "task_number" in args and "project_number" in args:
                project = project_service.get_by_number(args["project_number"])
                task = development_task_service.get_by_number(project.id, args["task_number"])
            if task:
                task_owner_service.check_mutation(task)

    @app.after_request
    def attribute_mutation(response):
        user = auth_service.current_user()
        if user and request.method in ("POST", "PATCH", "PUT", "DELETE") and 200 <= response.status_code < 300:
            payload = response.get_json(silent=True) if response.is_json else None
            payload = payload if isinstance(payload, dict) else {}
            target = payload.get("task", payload)
            number = next((target[key] for key in (
                "user_number", "task_number", "feature_number", "design_number",
                "source_requirement_number", "item_number", "run_number", "object_id", "project_number"
            ) if isinstance(target, dict) and isinstance(target.get(key), str) and target[key]), "")
            # Record attribution, never request bodies containing credentials.
            AuditRecord.create(actor=user, action=request.endpoint or request.path, target_number=number)
        if config.auth_required:
            response.headers["Cache-Control"] = "no-store"
        return response
