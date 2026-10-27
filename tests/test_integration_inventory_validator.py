"""Server-side backstop for Integration Round-1 self-audit."""

import hashlib

from tests.test_delivery_gate import (
    _complete_task,
    _make_confirmed_design,
    _make_feature,
    _make_project,
)


def _integration_ready_project(client, *, selected_solution=None):
    project = _make_project(client, name="Reconciliation service")
    feature = _make_feature(client, project["id"], "Nightly reconciliation")
    design = _make_confirmed_design(client, project["id"], feature["id"])
    reopened = client.post(f"/design-cases/{design['id']}/reopen")
    assert reopened.status_code == 200
    patch = {"design_scope": "BatchJob emits reconciliation records to LedgerStore."}
    if selected_solution is not None:
        patch["selected_solution"] = selected_solution
    design = client.patch(
        f"/design-cases/{design['id']}",
        json=patch,
    ).get_json()
    design = client.post(f"/design-cases/{design['id']}/confirm").get_json()
    _complete_task(client, design)
    return project, design


def _evidence(exit_code=0, expected=None):
    return [{
        "key": "E1",
        "role": "source-scan",
        "command": "rg -n BatchJob src",
        "exit_code": exit_code,
        "expected_exit_codes": expected if expected is not None else [0],
        "stdout": "src/job.py:12:class BatchJob",
        "stderr": "",
    }]


def _assembly_manifest(design):
    design_number = design["design_number"]
    return {
        "found_in_round": 1,
        "evidence": _evidence(),
        "external_resources": {
            "scan_evidence_keys": ["E1"],
            "design_audits": [{"design_number": design_number, "resource_keys": [],
                               "no_resources_reason": "This batch job uses only its own local records."}],
            "resources": [],
        },
        "production_entries": [],
        "design_coverage": [{
            "key": design_number,
            "design_number": design_number,
            "selected_solution_sha256": hashlib.sha256(
                design["selected_solution"].encode()
            ).hexdigest(),
            "design_scope_sha256": hashlib.sha256(
                design["design_scope"].encode()
            ).hexdigest(),
            "declared_part_keys": ["PART-1"],
            "seam_keys": [],
        }],
        "source_scans": [{
            "key": "SCAN-1",
            "language": "python",
            "roots": ["src"],
            "scan_evidence_key": "E1",
            "collision_keys": [],
            "different_name_candidate_keys": [],
        }],
        "declared_parts": [{
            "key": "PART-1",
            "design_number": design_number,
            "name": "BatchJob",
            "declaration": design["selected_solution"],
        }],
        "assembly_rows": [{
            "key": "PART-1",
            "reachable": False,
            "entry_keys": [],
            "path": [],
            "gap_key": "GAP-1",
            "missing_edge": "production scheduler -> BatchJob",
            "evidence_keys": ["E1"],
        }],
        "seams": [],
        "identifier_collisions": [],
        "duplicate_candidates": [],
        "proposed_items": [{
            "key": "GAP-1",
            "kind": "assembly",
            "subject": "BatchJob has no operational production path.",
            "source": (
                f"{design_number} selected_solution; missing edge: "
                "production scheduler -> BatchJob"
            ),
            "found_in_round": 1,
        }],
    }


def test_validator_computes_a_valid_exact_partition(client):
    project, design = _integration_ready_project(client)
    response = client.post(
        "/skills/integration-inventory-validate",
        json={
            "project_number": project["project_number"],
            "manifest": _assembly_manifest(design),
        },
    )

    assert response.status_code == 200, response.get_data(as_text=True)
    payload = response.get_json()
    assert payload["valid"] is True
    assert len(payload["validation_id"]) == 64
    assert payload["counts"]["declared_parts"] == 1
    assert payload["counts"]["assembly_gaps"] == 1
    assert payload["counts"]["expected_items"] == 1
    assert payload["counts"]["proposed_items"] == 1
    assert payload["audit"]["all_checks_pass"] is True
    assert payload["next_step"].startswith("Receipt valid: create every bound INTEG item")
    assert "one Integration run" in payload["next_step"]
    assert "Group related findings, repair them on one branch" in payload["next_step"]


def test_validator_rejects_an_omitted_cross_design_seam(client):
    from tests.test_delivery_gate import _make_feature, _make_confirmed_design, _complete_task

    project, design = _integration_ready_project(client)
    feature = _make_feature(client, project["id"], "Ledger persistence")
    other = _make_confirmed_design(client, project["id"], feature["id"])
    _complete_task(client, other)
    assert client.post(f"/design-cases/{design['id']}/reopen").status_code == 200
    changed = client.patch(f"/design-cases/{design['id']}", json={
        "design_scope": f"BatchJob consumes records from {other['design_number']} LedgerStore."
    })
    assert changed.status_code == 200, changed.get_json()
    design = client.post(f"/design-cases/{design['id']}/confirm").get_json()
    receipt = client.post("/skills/integration-inventory-validate", json={
        "project_number": project["project_number"], "manifest": _assembly_manifest(design),
    }).get_json()
    assert not receipt["valid"]
    assert any("seams missing explicit design_scope adjacencies" in error for error in receipt["errors"])


def test_validator_rejects_an_assembly_path_that_only_names_an_inventory_key(client):
    from tests.test_integration_completion_acceptance import _zero_gap_manifest

    project, design = _integration_ready_project(client)
    manifest = _zero_gap_manifest(design)
    manifest["assembly_rows"][0]["path"] = ["ENTRY-1 -> PART-1"]
    receipt = client.post("/skills/integration-inventory-validate", json={
        "project_number": project["project_number"], "manifest": manifest,
    }).get_json()
    assert not receipt["valid"]
    assert any("path must identify the declared part" in error for error in receipt["errors"])


def test_validator_rejects_health_only_evidence_for_a_reachable_part(client):
    from tests.test_integration_completion_acceptance import _zero_gap_manifest

    project, design = _integration_ready_project(client)
    manifest = _zero_gap_manifest(design)
    manifest["assembly_rows"][0]["evidence_keys"] = ["LAUNCH", "OBSERVE"]
    receipt = client.post("/skills/integration-inventory-validate", json={
        "project_number": project["project_number"], "manifest": manifest,
    }).get_json()
    assert not receipt["valid"]
    assert any("entry health alone is insufficient" in error for error in receipt["errors"])


def test_validator_rejects_internally_consistent_empty_inventory(client):
    project, _design = _integration_ready_project(client)
    manifest = {
        "found_in_round": 1,
        "evidence": [],
        "production_entries": [],
        "design_coverage": [],
        "source_scans": [],
        "declared_parts": [],
        "assembly_rows": [],
        "seams": [],
        "identifier_collisions": [],
        "duplicate_candidates": [],
        "proposed_items": [],
    }

    payload = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()

    assert payload["valid"] is False
    assert any("design_coverage missing" in error for error in payload["errors"])
    assert any("source_scans must contain" in error for error in payload["errors"])


def test_validator_rejects_build_command_as_operational_entry(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    manifest["evidence"] = [{
        "key": "E1",
        "role": "source-scan",
        "command": "npm run build",
        "exit_code": 0,
        "expected_exit_codes": [0],
        "stdout": "bundle created",
        "stderr": "",
    }]
    manifest["production_entries"] = [{
        "key": "ENTRY-1",
        "name": "client bundle",
        "entry_type": "browser",
        "operational_invocation": "npm run build",
        "observable_boundary": "bundle file exists",
        "executes_delivered_behavior": True,
        "launch_evidence_key": "E1",
        "observation_evidence_key": "E1",
    }]
    manifest["assembly_rows"][0] |= {
        "reachable": True,
        "entry_keys": ["ENTRY-1"],
        "path": ["ENTRY-1 -> BatchJob"],
    }
    manifest["proposed_items"] = []

    payload = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()

    assert payload["valid"] is False
    assert any("build/test command" in error for error in payload["errors"])
    assert payload["audit"]["all_checks_pass"] is False
    assert payload["next_step"].startswith("Correct every returned error")


def test_validator_binds_entry_invocation_to_launch_and_observation(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    manifest["evidence"].extend([
        {
            "key": "LAUNCH", "role": "entry-launch",
            "command": "python -m report_service", "exit_code": 0,
            "expected_exit_codes": [0], "stdout": "started", "stderr": "",
        },
        {
            "key": "OBSERVE", "role": "entry-observation",
            "command": "curl http://127.0.0.1:8080/health", "exit_code": 0,
            "expected_exit_codes": [0], "stdout": "ready", "stderr": "",
        },
    ])
    manifest["production_entries"] = [{
        "key": "ENTRY-1",
        "name": "report service",
        "entry_type": "service",
        "operational_invocation": "python -m different_service",
        "observable_boundary": "health endpoint returns ready",
        "executes_delivered_behavior": True,
        "launch_evidence_key": "LAUNCH",
        "observation_evidence_key": "OBSERVE",
    }]

    payload = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()

    assert payload["valid"] is False
    assert any("must exactly match its launch evidence command" in error for error in payload["errors"])


def test_validator_rejects_missing_atomic_row_and_compressed_payload(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    manifest["declared_parts"].append({
        "key": "PART-2",
        "design_number": design["design_number"],
        "name": "RetryLedger",
        "declaration": design["selected_solution"],
    })
    manifest["proposed_items"][0]["subject"] = "Same as above; template applies."

    payload = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()

    assert payload["valid"] is False
    assert any("missing declared part keys" in error for error in payload["errors"])
    assert any("compressed or refers elsewhere" in error for error in payload["errors"])


def test_validator_binds_part_declaration_to_current_design_text(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    manifest["declared_parts"][0]["declaration"] = "An invented component declaration."

    payload = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()

    assert payload["valid"] is False
    assert any("copied verbatim" in error for error in payload["errors"])


def test_validator_reconciles_shared_missing_edge_once(client):
    project, design = _integration_ready_project(
        client,
        selected_solution="Part A: BatchJob.\nPart B: AuditWriter.",
    )
    manifest = _assembly_manifest(design)
    manifest["declared_parts"][0]["declaration"] = "Part A: BatchJob."
    manifest["design_coverage"][0]["declared_part_keys"].append("PART-2")
    manifest["declared_parts"].append({
        "key": "PART-2",
        "design_number": design["design_number"],
        "name": "AuditWriter",
        "declaration": "Part B: AuditWriter.",
    })
    manifest["assembly_rows"].append({
        "key": "PART-2",
        "reachable": False,
        "entry_keys": [],
        "path": [],
        "gap_key": "GAP-1",
        "missing_edge": "production scheduler -> BatchJob",
        "evidence_keys": ["E1"],
    })

    payload = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()

    assert payload["valid"] is True, payload["errors"]
    assert payload["counts"]["unreachable_parts"] == 2
    assert payload["counts"]["assembly_gaps"] == 1
    assert payload["counts"]["expected_items"] == 1


def test_validator_does_not_create_work_for_a_satisfied_seam(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    command = "pytest -q tests/test_flow.py::test_batch_to_ledger"
    manifest["evidence"].append({
        "key": "SEAM-TEST",
        "role": "test",
        "command": command,
        "exit_code": 0,
        "expected_exit_codes": [0],
        "stdout": "1 passed",
        "stderr": "",
    })
    manifest["design_coverage"][0]["seam_keys"] = ["SEAM-1"]
    manifest["seams"] = [{
        "key": "SEAM-1",
        "declaring_design_number": design["design_number"],
        "producer_design_number": design["design_number"],
        "consumer_design_number": design["design_number"],
        "producer": "BatchJob.records",
        "consumer": "LedgerStore.commit",
        "declaration": design["design_scope"],
        "wiring": "BatchJob calls LedgerStore.commit in the scheduled flow.",
        "wired": True,
        "integration_verified": True,
        "test": command,
        "test_evidence_key": "SEAM-TEST",
        "evidence_keys": ["E1", "SEAM-TEST"],
    }]

    payload = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()

    assert payload["valid"] is True, payload["errors"]
    assert payload["counts"]["seams"] == 1
    assert payload["counts"]["seam_gaps"] == 0
    assert payload["counts"]["expected_items"] == 1


def test_validator_requires_work_for_an_unverified_seam(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    manifest["design_coverage"][0]["seam_keys"] = ["SEAM-1"]
    manifest["seams"] = [{
        "key": "SEAM-1",
        "declaring_design_number": design["design_number"],
        "producer_design_number": design["design_number"],
        "consumer_design_number": design["design_number"],
        "producer": "BatchJob.records",
        "consumer": "LedgerStore.commit",
        "declaration": design["design_scope"],
        "wiring": "The call exists, but no direct producer-consumer test exists.",
        "wired": True,
        "integration_verified": False,
        "test": None,
        "test_evidence_key": None,
        "evidence_keys": ["E1"],
    }]

    rejected = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()
    assert rejected["valid"] is False
    assert any("missing mapping keys" in error for error in rejected["errors"])

    manifest["proposed_items"].append({
        "key": "SEAM-1",
        "kind": "seam",
        "subject": "BatchJob to LedgerStore lacks direct integration verification.",
        "source": (
            f"{design['design_number']} design_scope: "
            "BatchJob.records -> LedgerStore.commit"
        ),
        "found_in_round": 1,
    })
    accepted = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()
    assert accepted["valid"] is True, accepted["errors"]
    assert accepted["counts"]["seam_gaps"] == 1


def test_validator_accepts_expected_negative_search_evidence(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    manifest["evidence"] = _evidence(exit_code=1, expected=[0, 1])

    payload = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()

    assert payload["valid"] is True


def test_validator_requires_matching_existing_item_to_be_reconciled(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    proposed = manifest["proposed_items"][0]
    existing = client.post(
        f"/projects/{project['id']}/delivery-items",
        json={
            "kind": proposed["kind"],
            "subject": proposed["subject"],
            "source": proposed["source"],
            "found_in_round": 1,
        },
    ).get_json()
    manifest["found_in_round"] = 2
    proposed["found_in_round"] = 2

    rejected = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()
    assert rejected["valid"] is False
    assert any("set existing_item_number" in error for error in rejected["errors"])

    proposed["existing_item_number"] = existing["item_number"]
    proposed["found_in_round"] = 1
    accepted = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()
    assert accepted["valid"] is True, accepted["errors"]


def test_validator_requires_full_convergence_proof_and_mapping(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    manifest["evidence"].extend([
        {"key": "E2", "role": "contract", "command": "rg -n Protocol src", "exit_code": 0, "expected_exit_codes": [0], "stdout": "protocol", "stderr": ""},
        {"key": "E3", "role": "caller", "command": "rg -n normalize callers", "exit_code": 0, "expected_exit_codes": [0], "stdout": "callers", "stderr": ""},
        {"key": "E4", "role": "owner", "command": "rg -n canonical_owner src", "exit_code": 0, "expected_exit_codes": [0], "stdout": "owner", "stderr": ""},
        {"key": "E5", "role": "test", "command": "pytest -q tests/test_contract.py::test_normalization", "exit_code": 0, "expected_exit_codes": [0], "stdout": "1 passed", "stderr": ""},
    ])
    manifest["duplicate_candidates"] = [{
        "key": "DUP-1",
        "discovery": "exact-name",
        "collision_keys": ["COLLISION-1"],
        "implementations": [
            {"module": "billing.input", "identifier": "normalize"},
            {"module": "billing.storage", "identifier": "normalize"},
        ],
        "responsibility": "Normalize billing references.",
        "verdict": "converge",
        "canonical_owner": "billing.contract",
        "caller_semantics": "Both callers require the same protocol normalization.",
        "divergence_is_defect": False,
        "boundary_fit": "The protocol contract owns normalization.",
        "baseline_evidence_key": "E5",
        "rationale": "The function bodies happen to match.",
        "evidence_keys": ["E1"],
        "contract_evidence_keys": ["E2"],
        "caller_evidence_keys": ["E3"],
        "owner_evidence_keys": ["E4"],
    }]
    manifest["identifier_collisions"] = [{
        "key": "COLLISION-1",
        "identifier": "normalize",
        "implementations": [
            {"module": "billing.input", "identifier": "normalize"},
            {"module": "billing.storage", "identifier": "normalize"},
        ],
        "evidence_keys": ["E1"],
    }]
    manifest["source_scans"][0]["collision_keys"] = ["COLLISION-1"]

    payload = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()

    assert payload["valid"] is False
    assert any("divergence_is_defect" in error for error in payload["errors"])
    assert any("missing mapping keys" in error for error in payload["errors"])


def test_validator_requires_every_identifier_collision_to_be_analyzed(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    manifest["identifier_collisions"] = [{
        "key": "COLLISION-1",
        "identifier": "parse_reference",
        "implementations": [
            {"module": "invoice.importer", "identifier": "parse_reference"},
            {"module": "invoice.exporter", "identifier": "parse_reference"},
        ],
        "evidence_keys": ["E1"],
    }]
    manifest["source_scans"][0]["collision_keys"] = ["COLLISION-1"]

    payload = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()

    assert payload["valid"] is False
    assert any(
        "must map to exactly one responsibility candidate" in error
        for error in payload["errors"]
    )
    assert payload["audit"]["collisions_are_exact_partition"] is False


def test_integration_create_requires_and_consumes_validation_receipt(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    proposed = manifest["proposed_items"][0]

    unvalidated = client.post(
        "/skills/delivery-item-create",
        json={"project_number": project["project_number"], **{
            key: proposed[key] for key in ("kind", "subject", "source", "found_in_round")
        }},
    )
    assert unvalidated.status_code == 400
    assert "validation_id and source_row_key" in unvalidated.get_json()["error"]

    validation = client.post(
        "/skills/integration-inventory-validate",
        json={"project_number": project["project_number"], "manifest": manifest},
    ).get_json()
    assert validation["valid"] is True
    create_payload = {
        "project_number": project["project_number"],
        **{key: proposed[key] for key in ("kind", "subject", "source", "found_in_round")},
        "validation_id": validation["validation_id"],
        "source_row_key": proposed["key"],
    }
    changed = create_payload | {"subject": "A changed subject that was not reviewed."}
    mismatch = client.post("/skills/delivery-item-create", json=changed)
    assert mismatch.status_code == 409
    assert "differs from the server-validated" in mismatch.get_json()["error"]

    created = client.post("/skills/delivery-item-create", json=create_payload)
    assert created.status_code == 200, created.get_data(as_text=True)

    repeated = client.post("/skills/delivery-item-create", json=create_payload)
    assert repeated.status_code == 409
    assert "not the active valid" in repeated.get_json()["error"]


def test_openapi_documents_inventory_validator(client):
    spec = client.get("/openapi.json").get_json()
    operation = spec["paths"]["/skills/integration-inventory-validate"]["post"]
    assert operation["operationId"] == "harnoq_integration_inventory_validate"
    schema = spec["components"]["schemas"]["IntegrationInventoryManifest"]
    assert set(schema["required"]) == {
        "found_in_round", "evidence", "production_entries", "design_coverage",
        "source_scans", "declared_parts",
        "assembly_rows", "seams", "identifier_collisions",
        "duplicate_candidates", "proposed_items", "external_resources",
    }
