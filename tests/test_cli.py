"""Tests for the Flask CLI commands registered by create_app."""
from app import create_app
from app.core.config import Config


def test_create_schema_cli_builds_the_tables(tmp_path):
    db_path = tmp_path / "cli.db"
    config = Config(
        db_backend="sqlite",
        sqlite_path=str(db_path),
        create_schema_on_start=False,  # so the tables are absent until the command
        testing=True,
    )
    app = create_app(config=config)
    db = app.config["DATABASE"]
    assert db.table_exists("project") is False

    result = app.test_cli_runner().invoke(args=["create-schema"])

    assert result.exit_code == 0
    assert "schema up to date" in result.output
    assert db.table_exists("project") is True


def test_create_schema_is_safe_to_run_twice(tmp_path):
    """Creates what is missing, leaves what exists alone.

    That is the whole contract now that incremental migration is gone: startup
    calls it on every boot, so it has to be a no-op against a database that is
    already built and must never disturb a table holding rows.
    """
    config = Config(
        db_backend="sqlite",
        sqlite_path=str(tmp_path / "twice.db"),
        create_schema_on_start=True,
        testing=True,
    )
    app = create_app(config=config)
    from app.models.project import Project

    Project.create(name="kept", project_number="P1")

    result = app.test_cli_runner().invoke(args=["create-schema"])

    assert result.exit_code == 0
    assert [row.name for row in Project.select()] == ["kept"]
