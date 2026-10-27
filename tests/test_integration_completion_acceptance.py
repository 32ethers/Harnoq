"""Independent acceptance of Integration rounds that need no new code."""
from copy import deepcopy

import pytest

from app.repositories import delivery_item_repo
from app.services import delivery_gate, integration_inventory_validator
from tests.test_integration_inventory_validator import (
    _assembly_manifest, _integration_ready_project,
)


def _zero_gap_manifest(design, *, round_number=1, rerun=True):
    manifest = _assembly_manifest(design)
    manifest["found_in_round"] = round_number
    manifest["evidence"].extend([
        {"key": "LAUNCH", "role": "entry-launch", "command": "python -m report_service",
         "exit_code": 0, "expected_exit_codes": [0], "stdout": "listening", "stderr": ""},
        {"key": "OBSERVE", "role": "entry-observation", "command": "curl http://127.0.0.1:8080/health",
         "exit_code": 0, "expected_exit_codes": [0], "stdout": "ready", "stderr": ""},
    ])
    if rerun:
        manifest["evidence"].append({
            "key": "RERUN", "role": "test",
            "command": "pytest tests/test_reconciliation.py::test_path -q",
            "exit_code": 0, "expected_exit_codes": [0], "stdout": "1 passed", "stderr": "",
        })
    manifest["production_entries"] = [{
        "key": "ENTRY-1", "name": "report service", "entry_type": "service",
        "operational_invocation": "python -m report_service",
        "observable_boundary": "health endpoint returns ready", "executes_delivered_behavior": True,
        "launch_evidence_key": "LAUNCH", "observation_evidence_key": "OBSERVE",
    }]
    manifest["assembly_rows"] = [{
        "key": "PART-1", "reachable": True, "entry_keys": ["ENTRY-1"],
        "path": ["ENTRY-1 -> BatchJob"], "evidence_keys": ["LAUNCH", "OBSERVE", "RERUN"],
    }]
    manifest["proposed_items"] = []
    return manifest


def _submit(client, project, manifest):
    response = client.post("/skills/integration-inventory-validate", json={
        "project_number": project["project_number"], "manifest": manifest,
    })
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def test_receipt_and_audit_rollback_together(client, monkeypatch):
    from app.repositories import integration_completion_audit_repo
    from app.models.integration_inventory_validation import IntegrationInventoryValidation
    project, design = _integration_ready_project(client)

    def failed_audit(*args, **kwargs):
        raise RuntimeError("simulated audit persistence failure")

    monkeypatch.setattr(integration_completion_audit_repo, "replace_active", failed_audit)
    with pytest.raises(RuntimeError, match="simulated audit"):
        integration_inventory_validator.validate(project["id"], _assembly_manifest(design))
    assert not IntegrationInventoryValidation.select().where(
        IntegrationInventoryValidation.project_id == project["id"]
    ).exists()
