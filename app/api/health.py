"""Health check endpoint."""
from flask import Blueprint, current_app, jsonify

bp = Blueprint("health", __name__)


def _database_ok() -> bool:
    db = current_app.config["DATABASE"]
    try:
        db.execute_sql("SELECT 1")
    except Exception:
        return False
    return True


@bp.get("/healthz")
def healthz():
    db_ok = _database_ok()
    payload = {
        "status": "ok" if db_ok else "degraded",
        "database": "connected" if db_ok else "unavailable",
    }
    return jsonify(payload), (200 if db_ok else 503)
