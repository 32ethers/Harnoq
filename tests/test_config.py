"""Tests for Config loading from environment."""
from app.core.config import Config


def test_config_defaults_when_no_env():
    # Clear any ambient env that the dev shell might have set.
    cfg = Config()
    assert cfg.db_backend == "sqlite"
    assert cfg.sqlite_path == "harnoq.db"
    assert cfg.create_schema_on_start is True
    assert cfg.testing is False
    assert cfg.postgres["port"] == 5432


def test_config_reads_env(monkeypatch):
    monkeypatch.setenv("HARNOQ_DB_BACKEND", "postgres")
    monkeypatch.setenv("HARNOQ_SQLITE_PATH", "/tmp/x.db")
    monkeypatch.setenv("HARNOQ_CREATE_SCHEMA", "0")
    monkeypatch.setenv("HARNOQ_TESTING", "1")
    monkeypatch.setenv("HARNOQ_DB_PORT", "5433")
    monkeypatch.setenv("HARNOQ_DB_NAME", "harnoq_ci")

    cfg = Config()

    assert cfg.db_backend == "postgres"
    assert cfg.sqlite_path == "/tmp/x.db"
    assert cfg.create_schema_on_start is False
    assert cfg.testing is True
    assert cfg.postgres["port"] == 5433
    assert cfg.postgres["name"] == "harnoq_ci"
