"""Application configuration loaded from environment with sensible defaults."""
import os


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class Config:
    """Runtime configuration. Fields can be overridden for tests.

    Backend defaults to SQLite (file at ``HARNOQ_SQLITE_PATH``) for local
    development. PostgreSQL is supported via ``db_backend="postgres"`` and the
    ``postgres`` mapping; the data structure stays backend-agnostic.
    """

    def __init__(
        self,
        *,
        db_backend: str | None = None,
        sqlite_path: str | None = None,
        postgres: dict | None = None,
        create_schema_on_start: bool | None = None,
        testing: bool | None = None,
        auth_required: bool = True,
        jwt_secret: str | None = None,
        jwt_hours: int = 8,
        allowed_origins: tuple[str, ...] | None = None,
    ) -> None:
        self.db_backend = (
            db_backend
            if db_backend is not None
            else os.environ.get("HARNOQ_DB_BACKEND", "sqlite")
        )
        self.sqlite_path = (
            sqlite_path
            if sqlite_path is not None
            else os.environ.get("HARNOQ_SQLITE_PATH", "harnoq.db")
        )
        self.postgres = postgres if postgres is not None else {
            "host": os.environ.get("HARNOQ_DB_HOST", "localhost"),
            "port": int(os.environ.get("HARNOQ_DB_PORT", "5432")),
            "user": os.environ.get("HARNOQ_DB_USER", "harnoq"),
            "password": os.environ.get("HARNOQ_DB_PASSWORD", ""),
            "name": os.environ.get("HARNOQ_DB_NAME", "harnoq"),
        }
        self.create_schema_on_start = (
            create_schema_on_start
            if create_schema_on_start is not None
            else _env_bool("HARNOQ_CREATE_SCHEMA", True)
        )
        self.testing = (
            testing
            if testing is not None
            else _env_bool("HARNOQ_TESTING", False)
        )
        if not auth_required and not self.testing:
            raise ValueError("authentication can only be disabled in isolated tests")
        self.auth_required = auth_required
        self.jwt_secret = jwt_secret or os.environ.get("HARNOQ_JWT_SECRET")
        self.jwt_hours = jwt_hours
        if not isinstance(jwt_hours, int) or isinstance(jwt_hours, bool) or not 1 <= jwt_hours <= 24:
            raise ValueError("jwt_hours must be between 1 and 24")
        self.allowed_origins = allowed_origins if allowed_origins is not None else tuple(
            origin.strip() for origin in os.environ.get("HARNOQ_ALLOWED_ORIGINS", "").split(",") if origin.strip()
        )


def load_config() -> Config:
    return Config()
