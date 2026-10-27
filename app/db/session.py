"""Database construction and per-request connection lifecycle."""
from peewee import Database, PostgresqlDatabase, SqliteDatabase

from ..core.config import Config
from .base import database_proxy
from .schema import create_schema


#: WAL lets readers run while a write is in flight, which is the normal state
#: of a local web app serving several browser tabs at once — under the default
#: rollback journal they block each other.
_SQLITE_PRAGMAS = {"foreign_keys": 1, "journal_mode": "wal"}

#: A test database is created, used and thrown away inside one test, so
#: durability buys nothing there and costs everything: under the defaults each
#: CREATE TABLE is its own fsync, and every test builds the WHOLE schema
#: (create_schema discovers all models, not the two a given test touches). That
#: fixed cost, times the suite, is why a run that uses ~50s of CPU took ~30min
#: of wall clock — the rest was the process asleep in D state waiting on the
#: filesystem journal. Nothing here is safe against a crash; nothing here needs
#: to be.
_SQLITE_TEST_PRAGMAS = {
    "foreign_keys": 1,
    "journal_mode": "memory",
    "synchronous": 0,
}


def create_database(config: Config) -> Database:
    if config.db_backend == "sqlite":
        return SqliteDatabase(
            config.sqlite_path,
            pragmas=_SQLITE_TEST_PRAGMAS if config.testing else _SQLITE_PRAGMAS,
        )
    if config.db_backend == "postgres":
        pg = config.postgres
        return PostgresqlDatabase(
            pg["name"],
            host=pg["host"],
            port=pg["port"],
            user=pg["user"],
            password=pg["password"],
        )
    raise ValueError(f"unsupported db_backend: {config.db_backend!r}")


def init_db(app, config: Config) -> None:
    """Bind a database to the proxy and manage connections per request."""
    db = create_database(config)
    database_proxy.initialize(db)
    app.config["DATABASE"] = db

    @app.before_request
    def _open_db() -> None:
        # Opening must not raise into the request. When the DB is unavailable
        # the handler is responsible for reporting a degraded state (e.g.
        # /healthz -> 503), so a failed connect is swallowed here and surfaces
        # when the handler actually touches the database.
        if db.is_closed():
            try:
                db.connect(reuse_if_open=True)
            except Exception:
                pass

    @app.teardown_request
    def _close_db(exception: object) -> None:
        if not db.is_closed():
            db.close()

    if config.create_schema_on_start:
        create_schema(db)
