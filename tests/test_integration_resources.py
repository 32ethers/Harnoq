"""External dependencies must work on the actual configured product path."""
import json

import pytest

from app.repositories import integration_completion_audit_repo
from app.services import delivery_gate, integration_inventory_validator
from tests.test_integration_completion_acceptance import _submit, _zero_gap_manifest
from tests.test_integration_inventory_validator import _integration_ready_project


def _manifest(design, status="verified"):
    manifest = _zero_gap_manifest(design)
    manifest["evidence"].extend([
        {"key": "READY", "role": "resource-check", "command": "python -m report_service check-reference",
         "exit_code": 0, "stdout": "Reference data version 2 readable at configured location", "stderr": ""},
        {"key": "CALL", "role": "resource-call", "command": "python -m report_service validate sample-record.json",
         "exit_code": 0, "stdout": "Record validated with reference data version 2", "stderr": ""},
    ])
    manifest["external_resources"] = {
        "scan_evidence_keys": ["E1"],
        "design_audits": [{"design_number": design["design_number"], "resource_keys": ["reference-data"]}],
        "resources": [{
            "key": "reference-data", "name": "Validation reference data",
            "source": f"{design['design_number']} selected_solution and runtime configuration",
            "configuration": "reference_data_path=/opt/report-data/reference-v2",
            "status": status, "entry_keys": ["ENTRY-1"], "uses_test_double": False,
            "operation": "Validate a record through the production entry using configured reference data",
            "observed_result": "Record accepted using reference data version 2",
            "readiness_evidence_key": "READY", "call_evidence_key": "CALL",
        }],
    }
    return manifest


@pytest.mark.parametrize("problem", ["double", "missing_call", "failed_call", "wrong_role", "wrong_entry", "no_output"])
def test_ready_or_mock_only_does_not_prove_successful_invocation(client, problem):
    project, design = _integration_ready_project(client)
    manifest = _manifest(design)
    resource = manifest["external_resources"]["resources"][0]
    if problem == "double":
        resource["uses_test_double"] = True
    elif problem == "missing_call":
        resource.pop("call_evidence_key")
    elif problem == "failed_call":
        manifest["evidence"][-1]["exit_code"] = 1
        manifest["evidence"][-1]["expected_exit_codes"] = [1]
    elif problem == "wrong_role":
        resource["call_evidence_key"] = "RERUN"
    elif problem == "wrong_entry":
        resource["entry_keys"] = ["UNDECLARED-ENTRY"]
    else:
        manifest["evidence"][-1]["stdout"] = ""
    result = _submit(client, project, manifest)
    assert not result["valid"]
    assert not result["integration_complete"]
    assert delivery_gate.gate_status(project["id"])["integration_owed"]


@pytest.mark.parametrize("problem", ["omitted_audit", "missing_design", "foreign_design", "unmapped_resource", "no_scan"])
def test_external_resource_inventory_is_explicit_and_complete(client, problem):
    project, design = _integration_ready_project(client)
    manifest = _manifest(design)
    resources = manifest["external_resources"]
    if problem == "omitted_audit":
        manifest.pop("external_resources")
    elif problem == "missing_design":
        resources["design_audits"] = []
    elif problem == "foreign_design":
        resources["design_audits"][0]["design_number"] = "P999-DESIGN-1"
    elif problem == "unmapped_resource":
        resources["resources"][0]["key"] = "different-key"
    else:
        resources["scan_evidence_keys"] = []
    assert not _submit(client, project, manifest)["valid"]
