"""Safe, testable rebuild migration for the multi-source task schema.

The application deliberately does not alter an existing database at startup.
This module is the explicit operator step for an old SQLite database: it
copies the file, adds the new columns/tables on the copy, backfills the
structural links, applies an explicit cleanup plan, and only then lets the
normal model schema be used.  The source is never opened for writing.

The cleanup plan is data, not policy hidden in SQL.  Production callers must
print/approve ``inspect_cleanup`` before calling ``rebuild_database``.
Tests use temporary copies, so a migration regression cannot touch the live
database by accident.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class CleanupPlan:
    """Rows intentionally removed from one project during a rebuild.

    Numbers are business numbers; delivery item ids are accepted only for the
    one-time historical cleanup boundary where that exact id range is part of
    the operator's approval.  An empty tuple means "remove none", never
    "remove everything".
    """

    project_number: str
    feature_numbers: tuple[str, ...] = ()
    design_numbers: tuple[str, ...] = ()
    task_numbers: tuple[str, ...] = ()
    delivery_item_ids: tuple[int, ...] = ()
    clear_inventory_validation: bool = False
    # A historical task may have been created under a DESIGN and later split
    # out as CR work.  Map CR business number -> task business number before
    # the one-live-task index is installed.
    change_request_task_map: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CleanupPreview:
    project_number: str
    feature_numbers: tuple[str, ...]
    design_numbers: tuple[str, ...]
    task_numbers: tuple[str, ...]
    delivery_item_ids: tuple[int, ...]
    inventory_validation_rows: int


def approved_integration_retest_plan() -> CleanupPlan:
    """Return the reviewed cleanup boundary for the current live database.

    This is intentionally an explicit operator plan, never invoked by app
    startup.  Capability history and CR rows remain; only the abandoned
    integration attempt and its delivery receipts are removed.  Historical
    CR-derived tasks are detached from their DESIGN before the one-to-one
    design constraint is installed.
    """
    return CleanupPlan(
        project_number="P1",
        feature_numbers=("P1-FEATURE-10", "P1-FEATURE-11"),
        design_numbers=("P1-DESIGN-15", "P1-DESIGN-16", "P1-DESIGN-17"),
        task_numbers=("P1-TASK-19",),
        delivery_item_ids=tuple(range(1, 56)),
        clear_inventory_validation=True,
        change_request_task_map=(
            ("P1-CR-6", "P1-TASK-14"),
            ("P1-CR-7", "P1-TASK-15"),
            ("P1-CR-9", "P1-TASK-17"),
            ("P1-CR-10", "P1-TASK-18"),
        ),
    )


def _connect_readonly(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.resolve()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _table_exists(db: sqlite3.Connection, table: str) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _numbers(db: sqlite3.Connection, table: str, column: str, project_id: int, requested):
    if not requested or not _table_exists(db, table):
        return ()
    rows = db.execute(
        f"SELECT {column} FROM {table} WHERE project_id=? AND {column} IN ({','.join('?' for _ in requested)})",
        (project_id, *requested),
    ).fetchall()
    return tuple(row[0] for row in rows)


def inspect_cleanup(source_path: str | Path, plan: CleanupPlan) -> CleanupPreview:
    """Return exactly what a plan would remove; never writes the source."""
    path = Path(source_path)
    if not path.is_file():
        raise FileNotFoundError(path)
    with _connect_readonly(path) as db:
        project = db.execute(
            "SELECT id FROM project WHERE project_number=?", (plan.project_number,)
        ).fetchone()
        if project is None:
            raise ValueError(f"project not found: {plan.project_number}")
        project_id = project[0]
        features = _numbers(db, "feature", "feature_number", project_id, plan.feature_numbers)
        designs = _numbers(db, "design_case", "design_number", project_id, plan.design_numbers)
        tasks = _numbers(db, "development_task", "task_number", project_id, plan.task_numbers)
        item_ids = tuple(
            row[0]
            for row in db.execute(
                "SELECT id FROM delivery_item WHERE project_id=? AND id IN ({})".format(
                    ",".join("?" for _ in plan.delivery_item_ids) or "NULL"
                ),
                (project_id, *plan.delivery_item_ids),
            )
        ) if _table_exists(db, "delivery_item") and plan.delivery_item_ids else ()
        inventory = (
            db.execute(
                "SELECT COUNT(*) FROM integration_inventory_validation WHERE project_id=?",
                (project_id,),
            ).fetchone()[0]
            if plan.clear_inventory_validation and _table_exists(db, "integration_inventory_validation")
            else 0
        )
    return CleanupPreview(plan.project_number, features, designs, tasks, item_ids, inventory)


def _add_column(db: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
    columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")


def _delete_task_children(db: sqlite3.Connection, task_ids: tuple[int, ...]) -> None:
    if not task_ids:
        return
    marks = ",".join("?" for _ in task_ids)
    for table in ("agent_run", "pull_request_record", "task_event", "task_dependency"):
        if _table_exists(db, table):
            column = "task_id"
            if table == "task_dependency":
                db.execute(
                    f"DELETE FROM {table} WHERE task_id IN ({marks}) OR depends_on_task_id IN ({marks})",
                    (*task_ids, *task_ids),
                )
            else:
                db.execute(f"DELETE FROM {table} WHERE {column} IN ({marks})", task_ids)


def _delete_polymorphic_links(
    db: sqlite3.Connection, *, object_type: str, object_ids: tuple[int, ...]
) -> None:
    """Remove basis links whose either endpoint is being physically removed."""
    if not object_ids or not _table_exists(db, "basis_link"):
        return
    marks = ",".join("?" for _ in object_ids)
    db.execute(
        f"DELETE FROM basis_link WHERE (downstream_type=? AND downstream_id IN ({marks})) "
        f"OR (upstream_type=? AND upstream_id IN ({marks}))",
        (object_type, *object_ids, object_type, *object_ids),
    )


def _delete_subrows(db: sqlite3.Connection, table: str, column: str, ids: tuple[int, ...]) -> None:
    if not ids or not _table_exists(db, table):
        return
    marks = ",".join("?" for _ in ids)
    db.execute(f"DELETE FROM {table} WHERE {column} IN ({marks})", ids)


def _resolve_ids(db, table: str, number_column: str, project_id: int, numbers) -> tuple[int, ...]:
    if not numbers or not _table_exists(db, table):
        return ()
    marks = ",".join("?" for _ in numbers)
    return tuple(
        row[0]
        for row in db.execute(
            f"SELECT id FROM {table} WHERE project_id=? AND {number_column} IN ({marks})",
            (project_id, *numbers),
        )
    )


def _attach_change_request_tasks(
    db: sqlite3.Connection, project_id: int, mappings: tuple[tuple[str, str], ...]
) -> None:
    """Convert historical CR-derived DESIGN tasks before uniqueness checks."""
    for cr_number, task_number in mappings:
        cr = db.execute(
            "SELECT id FROM change_request WHERE project_id=? AND change_request_number=?",
            (project_id, cr_number),
        ).fetchone()
        task = db.execute(
            "SELECT id, design_case_id FROM development_task WHERE project_id=? AND task_number=?",
            (project_id, task_number),
        ).fetchone()
        if cr is None or task is None:
            raise ValueError(f"cannot map historical {cr_number} -> {task_number}")
        feature = db.execute(
            "SELECT feature_id FROM design_case WHERE id=(SELECT design_case_id FROM development_task WHERE id=?)",
            (task[0],),
        ).fetchone()
        feature_id = feature[0] if feature else None
        db.execute(
            "UPDATE development_task SET design_case_id=NULL, feature_id=?, source_kind='change_request' WHERE id=?",
            (feature_id, task[0]),
        )
        db.execute("UPDATE change_request SET task_id=? WHERE id=?", (task[0], cr[0]))


def _reset_sequences(db: sqlite3.Connection, project_id: int) -> None:
    if not _table_exists(db, "number_sequence"):
        return
    for prefix, table, column in (
        ("FEATURE", "feature", "feature_number"),
        ("DESIGN", "design_case", "design_number"),
        ("TASK", "development_task", "task_number"),
        ("INTEG", "delivery_item", "item_number"),
        ("SYS", "delivery_item", "item_number"),
    ):
        if not _table_exists(db, table):
            continue
        values = [
            int(match.group(1))
            for (value,) in db.execute(
                f"SELECT {column} FROM {table} WHERE project_id=?", (project_id,)
            )
            if (match := re.search(rf"-{prefix}-(\d+)$", value or ""))
        ]
        next_value = max(values, default=0) + 1
        db.execute(
            "UPDATE number_sequence SET next_value=? WHERE project_id=? AND prefix=?",
            (next_value, project_id, prefix),
        )


def rebuild_database(
    source_path: str | Path,
    target_path: str | Path,
    *,
    plan: CleanupPlan,
    dry_run: bool = False,
) -> CleanupPreview:
    """Rebuild an old SQLite file into a new file and return the preview.

    ``dry_run`` performs validation only.  A real run refuses to overwrite an
    existing target and refuses identical source/target paths.  No caller can
    accidentally mutate ``harnoq.db`` through this function.
    """
    source = Path(source_path).resolve()
    target = Path(target_path).resolve()
    if source == target:
        raise ValueError("migration target must differ from source")
    preview = inspect_cleanup(source, plan)
    if dry_run:
        return preview
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    # SQLite's backup API includes committed WAL pages.  Copying only the main
    # file is unsafe for a live database that has a ``-wal`` sidecar.
    with _connect_readonly(source) as source_db, sqlite3.connect(target) as target_db:
        source_db.backup(target_db)
    try:
        with sqlite3.connect(target) as db:
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("BEGIN")
            _add_column(db, "development_task", "source_kind", "TEXT NOT NULL DEFAULT 'design'")
            _add_column(db, "development_task", "feature_id", "INTEGER REFERENCES feature(id) ON DELETE SET NULL")
            _add_column(db, "change_request", "task_id", "INTEGER REFERENCES development_task(id) ON DELETE SET NULL")
            db.execute("""
                UPDATE development_task
                SET feature_id=(SELECT feature_id FROM design_case d WHERE d.id=development_task.design_case_id)
                WHERE feature_id IS NULL AND design_case_id IS NOT NULL
            """)
            db.execute("""
                CREATE TABLE IF NOT EXISTS task_dependency (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at DATETIME NOT NULL,
                    updated_at DATETIME NOT NULL,
                    task_id INTEGER NOT NULL REFERENCES development_task(id) ON DELETE CASCADE,
                    depends_on_task_id INTEGER NOT NULL REFERENCES development_task(id) ON DELETE CASCADE,
                    UNIQUE(task_id, depends_on_task_id)
                )
            """)
            # Match Peewee's indexed ForeignKeyFields on both the rebuilt
            # legacy tables and the newly created dependency table.
            for table, column in (
                ("development_task", "feature_id"),
                ("change_request", "task_id"),
                ("task_dependency", "task_id"),
                ("task_dependency", "depends_on_task_id"),
            ):
                db.execute(
                    f"CREATE INDEX IF NOT EXISTS {table}_{column} "
                    f"ON {table}({column})"
                )
            project_id = db.execute(
                "SELECT id FROM project WHERE project_number=?", (plan.project_number,)
            ).fetchone()[0]
            _attach_change_request_tasks(db, project_id, plan.change_request_task_map)
            marks = lambda xs: ",".join("?" for _ in xs) or "NULL"
            task_ids = tuple(row[0] for row in db.execute(
                f"SELECT id FROM development_task WHERE project_id=? AND task_number IN ({marks(plan.task_numbers)})",
                (project_id, *plan.task_numbers),
            )) if plan.task_numbers else ()
            _delete_task_children(db, task_ids)
            _delete_polymorphic_links(db, object_type="development_task", object_ids=task_ids)
            if plan.delivery_item_ids:
                db.execute(
                    f"DELETE FROM delivery_item WHERE project_id=? AND id IN ({marks(plan.delivery_item_ids)})",
                    (project_id, *plan.delivery_item_ids),
                )
            if task_ids:
                db.execute(f"DELETE FROM development_task WHERE id IN ({marks(task_ids)})", task_ids)
            if plan.design_numbers:
                design_ids = _resolve_ids(db, "design_case", "design_number", project_id, plan.design_numbers)
                _delete_subrows(db, "design_case_version", "design_case_id", design_ids)
                _delete_polymorphic_links(db, object_type="design_case", object_ids=design_ids)
                db.execute(
                    f"DELETE FROM design_case WHERE project_id=? AND design_number IN ({marks(plan.design_numbers)})",
                    (project_id, *plan.design_numbers),
                )
            if plan.feature_numbers:
                feature_ids = _resolve_ids(db, "feature", "feature_number", project_id, plan.feature_numbers)
                _delete_subrows(db, "feature_version", "feature_id", feature_ids)
                _delete_subrows(db, "feature_dependency", "feature_id", feature_ids)
                _delete_subrows(db, "feature_dependency", "depends_on_feature_id", feature_ids)
                _delete_polymorphic_links(db, object_type="feature", object_ids=feature_ids)
                db.execute(
                    f"DELETE FROM feature WHERE project_id=? AND feature_number IN ({marks(plan.feature_numbers)})",
                    (project_id, *plan.feature_numbers),
                )
            if plan.clear_inventory_validation and _table_exists(db, "integration_inventory_validation"):
                db.execute("DELETE FROM integration_inventory_validation WHERE project_id=?", (project_id,))
            if plan.clear_inventory_validation and _table_exists(db, "integration_completion_audit"):
                db.execute("DELETE FROM integration_completion_audit WHERE project_id=?", (project_id,))
            if plan.clear_inventory_validation and _table_exists(db, "system_inventory_validation"):
                db.execute("DELETE FROM system_inventory_validation WHERE project_id=?", (project_id,))
            _reset_sequences(db, project_id)
            db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS development_task_one_live_design "
                "ON development_task(design_case_id) WHERE design_case_id IS NOT NULL AND deleted_at IS NULL"
            )
            db.execute("COMMIT")
            db.execute("PRAGMA foreign_keys=ON")
            violations = db.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise ValueError(f"migration produced foreign-key violations: {violations[:5]}")
    except Exception:
        target.unlink(missing_ok=True)
        raise
    return preview
