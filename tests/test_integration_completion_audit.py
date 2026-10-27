"""Completion audits are distinct from Integration create receipts."""
from app.services import integration_inventory_validator
from tests.test_integration_inventory_validator import _assembly_manifest, _integration_ready_project


def test_create_receipt_with_new_gap_is_not_a_completion_audit(client):
    project, design = _integration_ready_project(client)
    result = integration_inventory_validator.validate(project["id"], _assembly_manifest(design))
    assert result["valid"]
    status = integration_inventory_validator.completion_status(project["id"])
    assert not status["complete"]
    assert "not a final" in status["errors"][0]


def test_basis_is_part_of_integration_receipt_identity(client):
    project, design = _integration_ready_project(client)
    first = integration_inventory_validator.validate(project["id"], _assembly_manifest(design))
    assert first["valid"]
    changed = _assembly_manifest(design)
    changed["evidence"][0]["stdout"] = "src/job.py:13:class BatchJob"
    second = integration_inventory_validator.validate(project["id"], changed)
    # Manifest content is binding too; a retry is stable only when both it and
    # the current capability/design basis are unchanged.
    assert second["valid"]
    assert first["validation_id"] != second["validation_id"]
