import sqlite3

from app.db.rebuild_migration import CleanupPlan, inspect_cleanup, rebuild_database


def _old_db(path):
    db = sqlite3.connect(path)
    db.executescript(
        """
        PRAGMA foreign_keys=OFF;
        CREATE TABLE project (id INTEGER PRIMARY KEY, project_number TEXT);
        CREATE TABLE feature (id INTEGER PRIMARY KEY, project_id INTEGER, feature_number TEXT);
        CREATE TABLE design_case (id INTEGER PRIMARY KEY, project_id INTEGER, design_number TEXT, feature_id INTEGER);
        CREATE TABLE development_task (id INTEGER PRIMARY KEY, project_id INTEGER, task_number TEXT, design_case_id INTEGER, deleted_at TEXT);
        CREATE TABLE delivery_item (id INTEGER PRIMARY KEY, project_id INTEGER, item_number TEXT);
        CREATE TABLE change_request (id INTEGER PRIMARY KEY, project_id INTEGER, change_request_number TEXT);
        CREATE TABLE design_case_version (id INTEGER PRIMARY KEY, design_case_id INTEGER);
        CREATE TABLE feature_version (id INTEGER PRIMARY KEY, feature_id INTEGER);
        CREATE TABLE feature_dependency (id INTEGER PRIMARY KEY, feature_id INTEGER, depends_on_feature_id INTEGER);
        CREATE TABLE basis_link (id INTEGER PRIMARY KEY, project_id INTEGER, downstream_type TEXT, downstream_id INTEGER, upstream_type TEXT, upstream_id INTEGER);
        CREATE TABLE integration_inventory_validation (id INTEGER PRIMARY KEY, project_id INTEGER);
        CREATE TABLE integration_completion_audit (id INTEGER PRIMARY KEY, project_id INTEGER);
        CREATE TABLE system_inventory_validation (id INTEGER PRIMARY KEY, project_id INTEGER);
        CREATE TABLE number_sequence (id INTEGER PRIMARY KEY, project_id INTEGER, prefix TEXT, next_value INTEGER);
        INSERT INTO project VALUES (1, 'P1');
        INSERT INTO feature VALUES (1, 1, 'P1-FEATURE-1'), (2, 1, 'P1-FEATURE-10');
        INSERT INTO design_case VALUES (1, 1, 'P1-DESIGN-1', 1), (2, 1, 'P1-DESIGN-15', 2);
        INSERT INTO development_task VALUES (1, 1, 'P1-TASK-1', 1, NULL), (2, 1, 'P1-TASK-2', 1, NULL), (3, 1, 'P1-TASK-19', 2, NULL);
        INSERT INTO delivery_item VALUES (1, 1, 'P1-INTEG-1'), (56, 1, 'P1-INTEG-56');
        INSERT INTO change_request VALUES (1, 1, 'P1-CR-1');
        INSERT INTO design_case_version VALUES (1, 2);
        INSERT INTO feature_version VALUES (1, 2);
        INSERT INTO feature_dependency VALUES (1, 2, 1);
        INSERT INTO basis_link VALUES (1, 1, 'development_task', 3, 'design_case', 2);
        INSERT INTO integration_inventory_validation VALUES (1, 1);
        INSERT INTO integration_completion_audit VALUES (1, 1);
        INSERT INTO system_inventory_validation VALUES (1, 1);
        INSERT INTO number_sequence VALUES
          (1, 1, 'FEATURE', 11), (2, 1, 'DESIGN', 16),
          (3, 1, 'TASK', 20), (4, 1, 'INTEG', 57);
        """
    )
    db.commit()
    db.close()


def test_preview_is_read_only_and_reports_exact_boundary(tmp_path):
    source = tmp_path / "old.db"
    _old_db(source)
    plan = CleanupPlan(
        "P1",
        feature_numbers=("P1-FEATURE-10",),
        design_numbers=("P1-DESIGN-15",),
        task_numbers=("P1-TASK-19",),
        delivery_item_ids=(1,),
        clear_inventory_validation=True,
        change_request_task_map=(("P1-CR-1", "P1-TASK-2"),),
    )
    preview = inspect_cleanup(source, plan)
    assert preview.task_numbers == ("P1-TASK-19",)
    assert preview.delivery_item_ids == (1,)
    assert preview.inventory_validation_rows == 1
    with sqlite3.connect(source) as db:
        assert db.execute("SELECT COUNT(*) FROM delivery_item").fetchone()[0] == 2


def test_rebuild_never_overwrites_and_backfills_new_schema(tmp_path):
    source = tmp_path / "old.db"
    target = tmp_path / "rebuilt.db"
    _old_db(source)
    plan = CleanupPlan(
        "P1",
        feature_numbers=("P1-FEATURE-10",),
        design_numbers=("P1-DESIGN-15",),
        task_numbers=("P1-TASK-19",),
        delivery_item_ids=(1,),
        clear_inventory_validation=True,
        change_request_task_map=(("P1-CR-1", "P1-TASK-2"),),
    )
    rebuild_database(source, target, plan=plan)
    with sqlite3.connect(target) as db:
        columns = {row[1] for row in db.execute("PRAGMA table_info(development_task)")}
        assert {"source_kind", "feature_id"} <= columns
        assert db.execute("SELECT feature_id FROM development_task WHERE id=1").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM development_task WHERE task_number='P1-TASK-19'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM design_case WHERE design_number='P1-DESIGN-15'").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM design_case_version WHERE design_case_id=2").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM basis_link WHERE upstream_type='design_case' AND upstream_id=2").fetchone()[0] == 0
        assert db.execute("SELECT design_case_id, source_kind FROM development_task WHERE id=2").fetchone() == (None, "change_request")
        assert db.execute("SELECT task_id FROM change_request WHERE id=1").fetchone()[0] == 2
        assert db.execute("SELECT COUNT(*) FROM delivery_item WHERE id=1").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM integration_inventory_validation").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM integration_completion_audit").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM system_inventory_validation").fetchone()[0] == 0
        assert db.execute("SELECT next_value FROM number_sequence WHERE prefix='INTEG'").fetchone()[0] == 57
        assert db.execute("PRAGMA foreign_key_check").fetchall() == []
        for table, column in (
            ("development_task", "feature_id"),
            ("change_request", "task_id"),
            ("task_dependency", "task_id"),
            ("task_dependency", "depends_on_task_id"),
        ):
            indexes = {
                row[2]
                for row in db.execute(f"PRAGMA index_info('{table}_{column}')")
            }
            assert column in indexes
