"""Harnoq application factory."""
import click
import os
from flask import Flask, jsonify, request

from .api import register_blueprints
from .core.config import Config, load_config
from .core.errors import ApiError
from .core.i18n import init_i18n
from .core.authentication import init_auth
from .db.schema import create_schema
from .db.session import init_db
from .demo.phase1_flow import run_phase1_demo


def create_app(config: Config | None = None) -> Flask:
    app = Flask(__name__)
    app.config["HARNOQ_CONFIG"] = config if config is not None else load_config()

    init_db(app, app.config["HARNOQ_CONFIG"])
    register_blueprints(app)
    init_i18n(app)
    init_auth(app)

    @app.errorhandler(ApiError)
    def _handle_api_error(error: ApiError):
        return jsonify({"error": error.message}), error.status_code

    @app.before_request
    def _handle_cors_preflight():
        if request.method == "OPTIONS":
            return ("", 204)

    @app.after_request
    def _add_cors_headers(response):
        origin = request.headers.get("Origin")
        requested_headers = request.headers.get("Access-Control-Request-Headers")
        allowed = app.config["HARNOQ_CONFIG"].allowed_origins
        if origin and (not app.config["HARNOQ_CONFIG"].auth_required or origin in allowed or origin.rstrip("/") == request.host_url.rstrip("/")):
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Vary"] = "Origin"
        elif not origin:
            response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Headers"] = (
            requested_headers or "Content-Type, Authorization"
        )
        response.headers["Access-Control-Allow-Methods"] = (
            "GET, POST, PATCH, DELETE, OPTIONS"
        )
        return response

    @app.cli.command("create-schema")
    def create_schema_command() -> None:
        """Create any missing table from the models (see app/db/schema.py)."""
        create_schema(app.config["DATABASE"])
        click.echo("schema up to date")

    @app.cli.command("create-admin")
    @click.option("--username", prompt=True)
    @click.option("--display-name", prompt=True)
    @click.password_option()
    def create_admin(username, display_name, password):
        from .services import user_service
        with app.config["DATABASE"].connection_context():
            user = user_service.create({"username": username, "display_name": display_name, "password": password, "role": "admin"})
        click.echo(f"administrator created: {user.user_number}")

    @app.cli.command("link-legacy-tasks")
    @click.option("--user", "user_number", required=True, help="USER-* account to own unlinked historical tasks.")
    @click.option("--project", "project_number", help="Limit to one P* project; omitted means all projects.")
    def link_legacy_tasks_command(user_number, project_number):
        """Associate historical tasks, including deleted rows, without changing their lifecycle."""
        from .services import project_service, task_owner_service
        try:
            with app.config["DATABASE"].connection_context():
                project_id = project_service.get_by_number(project_number).id if project_number else None
                result = task_owner_service.link_legacy_tasks(user_number, project_id=project_id)
        except ApiError as exc:
            raise click.ClickException(exc.message) from None
        click.echo(f"{result['user_number']}: linked {result['linked_tasks']} historical tasks; restored {result['completion_events']} completion events")

    @app.cli.command("link-legacy-features")
    @click.option("--user", "user_number", required=True, help="USER-* account to own unlinked historical features.")
    @click.option("--project", "project_number", help="Limit to one P* project; omitted means all projects.")
    def link_legacy_features_command(user_number, project_number):
        """Associate historical features without changing their version history."""
        from .services import feature_owner_service, project_service
        try:
            with app.config["DATABASE"].connection_context():
                project_id = project_service.get_by_number(project_number).id if project_number else None
                result = feature_owner_service.link_legacy_features(user_number, project_id=project_id)
        except ApiError as exc:
            raise click.ClickException(exc.message) from None
        click.echo(f"{result['user_number']}: linked {result['linked_features']} historical features")

    @app.cli.command("link-legacy-work")
    @click.option("--user", "user_number", required=True, help="USER-* account for unlinked requirements, delivery items and stage runs.")
    @click.option("--project", "project_number", help="Limit to one P* project; omitted means all projects.")
    def link_legacy_work_command(user_number, project_number):
        """Associate historical work without changing content, statuses or evidence."""
        from .services import project_service, work_owner_service
        try:
            with app.config["DATABASE"].connection_context():
                project_id = project_service.get_by_number(project_number).id if project_number else None
                result = work_owner_service.link_legacy(user_number, project_id=project_id)
        except ApiError as exc:
            raise click.ClickException(exc.message) from None
        counts = ", ".join(f"{kind}={count}" for kind, count in result["linked"].items())
        click.echo(f"{result['user_number']}: linked historical work ({counts})")

    @app.cli.command("demo-phase1")
    def demo_phase1_command() -> None:
        """Run the Phase 1 closed-loop demo (offline, no git-host network).

        Drives one task from requirement to ``completed`` against a Flask test
        client bound to the configured DB. PR linking/merging is pure manual
        record-keeping (Harnoq never calls a git-hosting-provider API), so
        this runs with no network and no pre-existing data. Prints one line
        per step.
        """
        test_client = app.test_client()
        token = os.environ.get("HARNOQ_API_TOKEN")
        if app.config["HARNOQ_CONFIG"].auth_required and not token:
            raise click.ClickException("set HARNOQ_API_TOKEN to a personal JWT before running demo-phase1")
        if token:
            test_client.environ_base["HTTP_AUTHORIZATION"] = f"Bearer {token}"
        try:
            steps = run_phase1_demo(test_client)
        except Exception as exc:  # pragma: no cover - surfaced for operators
            click.echo(f"phase1 closed loop: FAILED ({exc})")
            raise
        for step in steps:
            click.echo(f"✓ {step['step']} -> {step['status']}")
        click.echo("phase1 closed loop: OK")

    return app
