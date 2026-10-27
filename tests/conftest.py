"""Shared pytest fixtures.

Each test gets a Flask app backed by an isolated file-based SQLite database
under ``tmp_path``. File-based (not ``:memory:``) so data survives the
per-request connect/close cycle that ``init_db`` installs.

``testing=True`` is what swaps in the throwaway SQLite pragmas (see
app/db/session.py). It is not decoration: without it every test pays a real
fsync per CREATE TABLE for a database it is about to delete.
"""
import pytest

from app import create_app
from app.core.config import Config


@pytest.fixture
def app(tmp_path):
    db_path = tmp_path / "test.db"
    config = Config(
        db_backend="sqlite",
        sqlite_path=str(db_path),
        create_schema_on_start=True,
        testing=True,
        auth_required=False,
    )
    flask_app = create_app(config=config)
    yield flask_app
    db = flask_app.config["DATABASE"]
    if not db.is_closed():
        db.close()


@pytest.fixture
def client(app):
    return app.test_client()
