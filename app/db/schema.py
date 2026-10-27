"""Create the schema from the models.

Harnoq used to carry a migration runner and 64 incremental scripts. It was
deleted (see docs/cn/design-evolution.md#storage-ui) because it served a deployment model this project does
not have: every schema change here is followed by rebuilding the database and
re-testing by hand, so nothing was ever migrated in place. What the scripts
actually did was let a fresh database replay the whole history to arrive at
the schema the models already describe — verified before deleting them, by
building one database each way and diffing: 19 tables, zero column
differences.

Their cost was not just the 3,600 lines plus 1,600 lines of tests. 26 of the
64 existed only to build an entity and later tear it down (an item layer, a
task-plan layer, a git-host integration, a retired review state), and a script
that imported a live model to create its tables would break every test the
moment that model was deleted — which is exactly what happened during
the removal of RequirementItem.

The consequence, stated plainly: adding or changing a column now means
rebuilding the database. That is already the workflow. If this project ever
acquires an instance whose data cannot be rebuilt, incremental migration has
to come back — from the models, which stay the source of truth either way.
"""
import importlib
import pkgutil

from peewee import Database

from .base import BaseModel


def _concrete_models() -> list[type[BaseModel]]:
    """Every persisted model, discovered from ``app.models``.

    Discovery rather than a hand-kept list: a model that was defined but never
    added to such a list produces a missing table at runtime instead of an
    import error, and that is a bad way to find out.
    """
    import app.models

    found: dict[type, None] = {}
    for module_info in pkgutil.iter_modules(app.models.__path__):
        module = importlib.import_module(f"app.models.{module_info.name}")
        for name in dir(module):
            attr = getattr(module, name)
            if not isinstance(attr, type) or not issubclass(attr, BaseModel):
                continue
            # The bases in app.db.base are abstract: they carry no table of
            # their own, and they live outside app.models.
            if attr.__module__.startswith("app.models."):
                found[attr] = None
    return list(found)


def create_schema(database: Database) -> None:
    """Create any table that does not exist yet. Existing tables are untouched."""
    database.connect(reuse_if_open=True)
    database.create_tables(_concrete_models())
    # A design may have at most one *live* execution task.  This is partial
    # rather than a model-level all-row unique index so a soft-deleted task can
    # be replaced, while the old task remains auditable in the recycle bin.
    database.execute_sql(
        "CREATE UNIQUE INDEX IF NOT EXISTS development_task_one_live_design "
        "ON development_task(design_case_id) "
        "WHERE design_case_id IS NOT NULL AND deleted_at IS NULL"
    )
