def _project(client):
    return client.post("/projects", json={"name": "Delivery run"}).get_json()


def test_stage_run_uses_business_route_and_rejects_missing_upstream(client):
    project = _project(client)
    response = client.post(f"/projects/{project['project_number']}/delivery-stage-runs/integration", json={
        "branch": "delivery/integration", "code_revision": "abc123", "design_revision": "design-v4",
    })
    assert response.status_code == 409
    assert "capability" in response.get_json()["error"]


def test_stage_run_route_does_not_accept_raw_project_id(client):
    project = _project(client)
    response = client.post(f"/projects/{project['id']}/delivery-stage-runs/integration", json={
        "code_revision": "abc", "design_revision": "d",
    })
    assert response.status_code == 404


def test_integration_cannot_create_a_source_task(client):
    project = _project(client)
    response = client.post(f"/projects/{project['project_number']}/tasks/from-sources", json={
        "source_kind": "integration", "item_numbers": ["P1-INTEG-1"],
        "title": "Old delivery task", "task_type": "test", "goal": "Old flow",
        "acceptance_criteria": "Old flow",
    })
    assert response.status_code == 400
    assert "source_kind must be change_request" in response.get_json()["error"]
    assert client.get(f"/projects/{project['id']}/tasks").get_json() == []


def test_integration_findings_are_read_directly_and_closed_in_one_run(client):
    from tests.test_integration_inventory_validator import _integration_ready_project, _assembly_manifest

    project, design = _integration_ready_project(client, selected_solution="BatchJob and LedgerStore")
    manifest = _assembly_manifest(design)
    manifest["declared_parts"].append({
        "key": "PART-2", "design_number": design["design_number"],
        "name": "LedgerStore", "declaration": "LedgerStore",
    })
    manifest["design_coverage"][0]["declared_part_keys"].append("PART-2")
    manifest["assembly_rows"].append({
        "key": "PART-2", "reachable": False, "entry_keys": [], "path": [],
        "gap_key": "GAP-2", "missing_edge": "BatchJob -> LedgerStore",
        "evidence_keys": ["E1"],
    })
    manifest["proposed_items"].append({
        "key": "GAP-2", "kind": "assembly", "subject": "BatchJob does not write to LedgerStore",
        "source": f"{design['design_number']} selected_solution; missing edge: BatchJob -> LedgerStore",
        "found_in_round": 1,
    })
    receipt = client.post("/skills/integration-inventory-validate", json={
        "project_number": project["project_number"], "manifest": manifest,
    }).get_json()
    assert receipt["valid"], receipt["errors"]
    for proposed in manifest["proposed_items"]:
        created = client.post("/skills/delivery-item-create", json={
            "project_number": project["project_number"],
            **{key: proposed[key] for key in ("kind", "subject", "source", "found_in_round")},
            "validation_id": receipt["validation_id"], "source_row_key": proposed["key"],
        })
        assert created.status_code == 200, created.get_json()
    item_numbers = [row["item_number"] for row in client.get(f"/projects/{project['id']}/delivery-items").get_json()]
    runs = f"/projects/{project['project_number']}/delivery-stage-runs"
    started = client.post(f"{runs}/integration", json={
        "code_revision": "before", "design_revision": "confirmed-basis",
    })
    assert started.status_code == 201, started.get_json()
    for item_number in item_numbers:
        item_read = client.get(
            f"/projects/{project['project_number']}/integration-items/{item_number}"
        )
        assert item_read.status_code == 200
        finding = item_read.get_json()
        assert finding["item_number"] == item_number
        assert finding["subject"] and finding["source"]
        assert "id" not in finding and "project_id" not in finding
    assert client.get(
        f"/projects/{project['project_number']}/integration-items/P1-INTEG-999"
    ).status_code == 404
    close = client.post(f"{runs}/{started.get_json()['run_number']}/close")
    assert close.status_code == 409
    assert "open items" in close.get_json()["error"]
    assert not any(row["source_kind"] == "integration" for row in client.get(f"/projects/{project['id']}/tasks").get_json())
    gate = client.get(f"/projects/{project['id']}/delivery-gate").get_json()
    assert gate["capability_outstanding"] is False
    assert gate["integration_owed"] is True
    assert "Integration completion audit basis changed" not in gate["integration_inventory_errors"]


def test_validated_integration_can_close_without_a_task_or_empty_pr(client):
    from tests.test_integration_inventory_validator import _integration_ready_project
    from tests.test_integration_completion_acceptance import _submit, _zero_gap_manifest

    project, design = _integration_ready_project(client)
    audit = _submit(client, project, _zero_gap_manifest(design))
    assert audit["valid"], audit["errors"]
    path = f"/projects/{project['project_number']}/delivery-stage-runs"
    started = client.post(f"{path}/integration", json={
        "code_revision": "abc123", "design_revision": "confirmed-design-basis",
    })
    assert started.status_code == 201, started.get_json()
    run = started.get_json()
    assert run["run_number"] and "id" not in run and "project_id" not in run
    closed = client.post(f"{path}/{run['run_number']}/close")
    assert closed.status_code == 200, closed.get_json()
    assert closed.get_json()["status"] == "closed"
    gate = client.post("/skills/project-context", json={"project_number": project["project_number"]}).get_json()["delivery_gate"]
    assert gate["integration_owed"] is False
    assert gate["system_owed"] is True
    system = client.post(f"{path}/system", json={
        "code_revision": "abc123", "design_revision": "confirmed-design-basis",
    })
    assert system.status_code == 201, system.get_json()
    system_number = system.get_json()["run_number"]
    assert client.post(f"{path}/{system_number}/close").status_code == 200
    assert client.post(f"{path}/{system_number}/close").status_code == 409
    final_gate = client.post("/skills/project-context", json={"project_number": project["project_number"]}).get_json()["delivery_gate"]
    assert final_gate["integration_owed"] is False
    assert final_gate["system_owed"] is False


def test_integration_code_change_requires_finding_and_qa_report(client):
    from tests.test_integration_inventory_validator import _integration_ready_project
    from tests.test_integration_completion_acceptance import _submit, _zero_gap_manifest

    project, design = _integration_ready_project(client)
    assert _submit(client, project, _zero_gap_manifest(design))["valid"]
    path = f"/projects/{project['project_number']}/delivery-stage-runs"
    run = client.post(f"{path}/integration", json={
        "branch": "integration/repair", "code_revision": "before",
        "design_revision": "confirmed-basis",
    }).get_json()
    close = client.post(f"{path}/{run['run_number']}/close", json={
        "merge_reference": "merged-revision",
    })
    assert close.status_code == 409
    assert "recorded INTEG" in close.get_json()["error"]


def test_old_closed_integration_receipt_reopens_when_entry_proof_is_insufficient(client):
    import json
    from app.repositories import integration_completion_audit_repo
    from tests.test_integration_inventory_validator import _integration_ready_project
    from tests.test_integration_completion_acceptance import _submit, _zero_gap_manifest

    project, design = _integration_ready_project(client)
    assert _submit(client, project, _zero_gap_manifest(design))["valid"]
    path = f"/projects/{project['project_number']}/delivery-stage-runs"
    run = client.post(f"{path}/integration", json={
        "code_revision": "merged", "design_revision": "confirmed-basis",
    }).get_json()
    assert client.post(f"{path}/{run['run_number']}/close").status_code == 200
    audit = integration_completion_audit_repo.get_active(project["id"])
    manifest = json.loads(audit.manifest_json)
    manifest["assembly_rows"][0]["evidence_keys"] = ["LAUNCH", "OBSERVE"]
    audit.manifest_json = json.dumps(manifest)
    integration_completion_audit_repo.save(audit)

    gate = client.get(f"/projects/{project['id']}/delivery-gate").get_json()
    assert gate["integration_owed"] is True
    assert any("entry health alone is insufficient" in error for error in gate["integration_inventory_errors"])


def test_stage_run_post_update_does_not_route_run_number_as_stage(client):
    from tests.test_integration_inventory_validator import _integration_ready_project
    from tests.test_integration_completion_acceptance import _submit, _zero_gap_manifest

    project, design = _integration_ready_project(client)
    assert _submit(client, project, _zero_gap_manifest(design))["valid"]
    path = f"/projects/{project['project_number']}/delivery-stage-runs"
    started = client.post(f"{path}/integration", json={
        "code_revision": "before-merge", "design_revision": "confirmed-basis",
    })
    assert started.status_code == 201, started.get_json()
    run_number = started.get_json()["run_number"]
    updated = client.post(f"{path}/{run_number}/update", json={
        "code_revision": "merged-main",
    })
    assert updated.status_code == 200, updated.get_json()
    assert updated.get_json()["run_number"] == run_number
    assert updated.get_json()["stage"] == "integration"
    assert updated.get_json()["code_revision"] == "merged-main"
    legacy_post = client.post(f"{path}/{run_number}", json={
        "code_revision": "merged-main-again",
    })
    assert legacy_post.status_code == 200, legacy_post.get_json()
    assert legacy_post.get_json()["code_revision"] == "merged-main-again"


def test_missing_user_resource_blocks_stage_close(client):
    from tests.test_integration_inventory_validator import _integration_ready_project
    from tests.test_integration_completion_acceptance import _submit, _zero_gap_manifest

    project, design = _integration_ready_project(client)
    assert _submit(client, project, _zero_gap_manifest(design))["valid"]
    path = f"/projects/{project['project_number']}/delivery-stage-runs"
    run = client.post(f"{path}/integration", json={
        "code_revision": "abc123", "design_revision": "confirmed-design-basis",
    }).get_json()
    blocked = client.patch(f"{path}/{run['run_number']}", json={
        "blocked_reason": "Need the user's test archive to verify the real import path",
    })
    assert blocked.status_code == 200
    assert blocked.get_json()["status"] == "blocked"
    response = client.post(f"{path}/{run['run_number']}/close")
    assert response.status_code == 409
    assert "resource" in response.get_json()["error"]
    assert client.patch(f"{path}/{run['run_number']}", json={"blocked_reason": ""}).get_json()["status"] == "active"
    assert client.post(f"{path}/{run['run_number']}/close").status_code == 200


def test_system_code_change_needs_exact_integration_rerun(client):
    import json
    from tests.test_integration_inventory_validator import _integration_ready_project
    from tests.test_integration_completion_acceptance import _submit, _zero_gap_manifest

    project, design = _integration_ready_project(client)
    assert _submit(client, project, _zero_gap_manifest(design))["valid"]
    path = f"/projects/{project['project_number']}/delivery-stage-runs"
    integration = client.post(f"{path}/integration", json={
        "code_revision": "old", "design_revision": "design",
    }).get_json()
    assert client.post(f"{path}/{integration['run_number']}/close").status_code == 200
    system = client.post(f"{path}/system", json={
        "branch": "delivery/system", "code_revision": "new", "design_revision": "design",
    }).get_json()
    close_path = f"{path}/{system['run_number']}/close"
    assert client.post(close_path, json={"merge_reference": "PR-1"}).status_code == 400
    rerun = json.dumps([{
        "command": "pytest -q tests/test_contract.py", "output": "1 passed",
        "exit_code": 0, "code_revision": "new",
    }])
    closed = client.post(close_path, json={
        "merge_reference": "PR-1", "integration_rerun_evidence": rerun,
    })
    assert closed.status_code == 200, closed.get_json()


def test_delivery_context_indexes_long_prose_and_rejects_drift(client):
    from app.models.design_case import DesignCase
    from tests.test_delivery_gate import _confirm_source_requirement_and_constitution
    from tests.test_integration_inventory_validator import _integration_ready_project

    project, design = _integration_ready_project(client)
    _confirm_source_requirement_and_constitution(client, project["id"])
    row = DesignCase.get_by_id(design["id"])
    row.selected_solution = "long design text " * 500
    row.save()
    body = {"project_number": project["project_number"], "page": 1, "page_size": 1}
    response = client.post("/skills/project-context", json=body)
    assert response.status_code == 200
    context = response.get_json()
    assert context["current_stage"] == "integration"
    first_token = context["context_page"]["snapshot_token"]
    while not context["design_cases"]:
        assert context["context_page"]["has_more"]
        context = client.post("/skills/project-context", json=body | {
            "page": context["context_page"]["next_page"],
            "snapshot_token": first_token,
        }).get_json()
    indexed = context["design_cases"][0]
    assert "selected_solution" not in indexed
    assert indexed["body_fields_to_fetch"]["selected_solution"]["bytes"] > 1200
    token = first_token
    assert client.post("/skills/project-context", json=body | {"page": 2}).status_code == 400
    row.selected_solution += "changed"
    row.save()
    stale = client.post("/skills/project-context", json=body | {
        "page": 2, "snapshot_token": token,
    })
    assert stale.status_code == 409


def test_stage_close_rejects_changed_capability_basis(client):
    from app.models.design_case import DesignCase
    from tests.test_integration_inventory_validator import _integration_ready_project
    from tests.test_integration_completion_acceptance import _submit, _zero_gap_manifest

    project, design = _integration_ready_project(client)
    assert _submit(client, project, _zero_gap_manifest(design))["valid"]
    path = f"/projects/{project['project_number']}/delivery-stage-runs"
    run = client.post(f"{path}/integration", json={
        "code_revision": "abc123", "design_revision": "design",
    }).get_json()
    row = DesignCase.get_by_id(design["id"])
    row.design_scope += "\nNew contract decision."
    row.save()
    rejected = client.post(f"{path}/{run['run_number']}/close")
    assert rejected.status_code == 409
    assert "basis changed" in rejected.get_json()["error"]
    abandoned = client.post(f"{path}/{run['run_number']}/abandon", json={
        "reason": "Confirmed design basis changed and requires a fresh inventory",
    })
    assert abandoned.status_code == 200
    assert abandoned.get_json()["status"] == "abandoned"
    assert client.post(f"{path}/{run['run_number']}/close").status_code == 409
    restarted = client.post(f"{path}/integration", json={
        "code_revision": "next", "design_revision": "next-design",
    })
    assert restarted.status_code == 201, restarted.get_json()


def test_binding_delivery_methods_route_integration_findings_to_run():
    from app.api.stage_methods import _stage_methods

    methods = _stage_methods()
    assert "create no TASK" in methods["integration"]["rounds"][1]
    assert "run.evidence" in methods["integration"]["repair_policy"]
    assert "TASK" not in methods["system"]["write"]
    assert "user" in methods["system"]["repair_policy"]


def test_direct_item_coverage_requires_active_run_and_current_executable_evidence(client):
    import json
    from tests.test_integration_inventory_validator import _integration_ready_project, _assembly_manifest

    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    receipt = client.post("/skills/integration-inventory-validate", json={
        "project_number": project["project_number"], "manifest": manifest,
    }).get_json()
    assert receipt["valid"], receipt["errors"]
    proposed = manifest["proposed_items"][0]
    created = client.post("/skills/delivery-item-create", json={
        "project_number": project["project_number"],
        **{key: proposed[key] for key in ("kind", "subject", "source", "found_in_round")},
        "validation_id": receipt["validation_id"], "source_row_key": proposed["key"],
    })
    assert created.status_code == 200, created.get_json()
    item = client.get(f"/projects/{project['id']}/delivery-items").get_json()[0]
    item_path = f"/delivery-items/{item['id']}"
    proof = {
        "status": "covered", "covered_by": "pytest -q tests/test_entry.py::test_user_path",
        "evidence_command": "pytest -q tests/test_entry.py::test_user_path",
        "evidence_output": "1 passed", "evidence_exit_code": 0,
        "verified_revision": "rev-1", "repair_record": "Entry was unwired; connected it; test passed.",
    }
    assert client.patch(item_path, json=proof).status_code == 409
    runs = f"/projects/{project['project_number']}/delivery-stage-runs"
    started = client.post(f"{runs}/integration", json={
        "branch": "integration/repair", "code_revision": "rev-1", "design_revision": "confirmed-basis",
    })
    assert started.status_code == 201, started.get_json()
    assert client.patch(item_path, json=proof | {"evidence_exit_code": 1}).status_code == 400
    assert client.patch(item_path, json=proof | {"verified_revision": "old"}).status_code == 409
    covered = client.patch(item_path, json=proof)
    assert covered.status_code == 200, covered.get_json()
    assert covered.get_json()["stage_run_number"] == started.get_json()["run_number"]
    assert covered.get_json()["repair_record"] == proof["repair_record"]
    close_path = f"{runs}/{started.get_json()['run_number']}/close"
    assert "JSON qa report" in client.post(close_path, json={"merge_reference": "PR-1"}).get_json()["error"]
    report = {"role": "qa", "item_numbers": [], "command": "pytest -q tests/test_entry.py",
              "output": "1 passed", "exit_code": 0, "code_revision": "rev-1"}
    missing = client.post(close_path, json={"merge_reference": "PR-1", "evidence": json.dumps(report)})
    assert missing.status_code == 400
    assert "item_numbers" in missing.get_json()["error"]
    report["item_numbers"] = [item["item_number"]]
    ready_for_audit = client.post(close_path, json={"merge_reference": "PR-1", "evidence": json.dumps(report)})
    assert ready_for_audit.status_code == 409
    assert "final settled audit" in ready_for_audit.get_json()["error"]
