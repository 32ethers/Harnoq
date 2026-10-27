"""Business numbers come from a monotonic per-(project, prefix) counter that
never rewinds, so a purged number is never re-issued to a new, unrelated
object (the incident where a fresh plan came back as the already-deleted
``PLAN-1``). Counters are scoped per project (see docs/cn/design-evolution.md §6): each project's
INPUT/DESIGN/TASK numbering starts at 1 independently.
"""
from app.models.number_sequence import NumberSequence
from app.services import numbering, project_service, source_requirement_service


def test_allocate_is_monotonic_and_per_prefix(app):
    with app.app_context():
        assert numbering.allocate("PLAN") == 1
        assert numbering.allocate("PLAN") == 2
        assert numbering.allocate("REQ") == 1  # counters are independent
        assert numbering.allocate("PLAN") == 3


def test_allocate_is_scoped_per_project(app):
    with app.app_context():
        assert numbering.allocate("TASK", project_id=1) == 1
        assert numbering.allocate("TASK", project_id=1) == 2
        assert numbering.allocate("TASK", project_id=2) == 1  # independent project
        assert numbering.allocate("TASK", project_id=1) == 3
        assert numbering.allocate("TASK") == 1  # global (no project_id) also independent


def test_number_not_reused_after_purge(app):
    """The real incident's mechanism: purging the highest row frees its rowid
    (SQLite reuses it), so the number MUST come from the counter, not the id."""
    with app.app_context():
        project = project_service.create({"name": "P"})
        payload = {"title": "x", "source_type": "new_feature"}

        first = source_requirement_service.create(project.id, payload)
        second = source_requirement_service.create(project.id, payload)
        assert first.source_requirement_number == "P1-INPUT-1"
        assert second.source_requirement_number == "P1-INPUT-2"

        # Hard-purge the highest row: rowid 2 is now free for reuse.
        source_requirement_service.delete(second.id)
        source_requirement_service.purge(second.id)

        third = source_requirement_service.create(project.id, payload)
        # Old (id-derived) numbering would re-issue INPUT-2 here; the counter
        # must hand out INPUT-3 instead — a number is used exactly once, ever.
        assert third.source_requirement_number == "P1-INPUT-3"
        assert third.source_requirement_number != second.source_requirement_number


def test_counter_survives_and_advances_independently_of_rows(app):
    with app.app_context():
        numbering.allocate("TASK", project_id=7)
        numbering.allocate("TASK", project_id=7)
        # Even with no TASK rows in existence, the counter keeps its high-water.
        assert (
            NumberSequence.get(
                (NumberSequence.prefix == "TASK") & (NumberSequence.project_id == 7)
            ).next_value
            == 3
        )
