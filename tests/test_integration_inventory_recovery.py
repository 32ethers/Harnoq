"""Inventory recovery and delta close-out exercise the real full audit."""
from copy import deepcopy

import pytest
import yaml

from app.repositories import integration_completion_audit_repo, design_case_repo
from app.services import delivery_gate
from tests.test_integration_completion_acceptance import _zero_gap_manifest, _submit
from tests.test_integration_inventory_validator import _assembly_manifest, _integration_ready_project


def _get(client, project):
    response = client.post("/skills/integration-inventory-get", json={"project_number": project["project_number"]})
    assert response.status_code == 200, response.get_json()
    return response.get_json()


def _revise(client, project, saved, **updates):
    return client.post("/skills/integration-inventory-revise", json={
        "project_number": project["project_number"],
        "base_validation_id": saved["validation_id"],
        "reviewed_revision_token": saved["revision_token"],
        "evidence_updates": [], **updates,
    })


def test_get_recovers_exact_saved_manifest_without_writing(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    result = _submit(client, project, manifest)
    audit = integration_completion_audit_repo.get_active(project["id"])
    before = (audit.manifest_json, audit.created_at, audit.completed_at)
    saved = _get(client, project)
    assert saved["manifest"] == manifest
    assert saved["validation_id"] == result["validation_id"]
    assert not saved["basis_changed"]
    assert saved["completion_errors"]
    assert saved["design_changes"] == saved["item_changes"] == []
    assert "_live_items" not in saved["manifest"]
    audit = integration_completion_audit_repo.get_active(project["id"])
    assert before == (audit.manifest_json, audit.created_at, audit.completed_at)


def test_missing_inventory_gives_initial_submission_instruction(client):
    project, _ = _integration_ready_project(client)
    saved = _get(client, project)
    assert saved["has_inventory"] is False and saved["manifest"] is None
    assert "initial complete manifest" in saved["next_step"]


def test_revision_reconciles_created_items_and_round_without_recreating_work(client):
    project, design = _integration_ready_project(client)
    manifest = _assembly_manifest(design)
    initial = _submit(client, project, manifest)
    before_create = _get(client, project)
    proposal = manifest["proposed_items"][0]
    response = client.post("/skills/integration-item-create", json={
        "project_number": project["project_number"],
        **{k: v for k, v in proposal.items() if k != "key"},
        "validation_id": initial["validation_id"], "source_row_key": proposal["key"],
    })
    assert response.status_code == 200, response.get_json()
    assert _revise(client, project, before_create).status_code == 409
    saved = _get(client, project)
    assert saved["item_changes"] and saved["expected_found_in_round"] == 2
    result = _revise(client, project, saved).get_json()
    assert result["valid"] and not result["integration_complete"]
    assert result["pending_creation_keys"] == []
    assert "create the bound INTEG items" not in result["next_step"]
    recovered = _get(client, project)
    assert recovered["manifest"]["found_in_round"] == 2
    assert recovered["manifest"]["proposed_items"][0]["existing_item_number"] == saved["current_items"][0]["item_number"]


def test_stale_revision_and_cross_project_receipt_are_rejected(client):
    project, design = _integration_ready_project(client)
    _submit(client, project, _assembly_manifest(design))
    saved = _get(client, project)
    changed = deepcopy(saved["manifest"])
    changed["evidence"][0]["stdout"] += "\nnew scan result"
    _submit(client, project, changed)
    assert _revise(client, project, saved).status_code == 409
    other, other_design = _integration_ready_project(client)
    _submit(client, other, _assembly_manifest(other_design))
    assert _revise(client, other, saved).status_code == 409


def test_design_changes_are_reported_and_cannot_be_rebased_by_token_alone(client):
    project, design = _integration_ready_project(client)
    _submit(client, project, _assembly_manifest(design))
    row = design_case_repo.get(design["id"])
    row.design_scope += " A new consumer is required."
    design_case_repo.save(row)
    saved = _get(client, project)
    assert saved["design_changes"][0]["design_number"] == design["design_number"]
    result = _revise(client, project, saved).get_json()
    assert not result["valid"]
    assert any("design_scope" in error for error in result["errors"]), result["errors"]


@pytest.mark.parametrize("update", [
    {"section_updates": {"_live_items": []}},
    {"section_updates": {"found_in_round": 99}},
    {"section_updates": {"external_resources": None}},
    {"evidence_updates": [{"key": "X"}, {"key": "X"}]},
])
def test_malformed_updates_are_rejected_without_changing_saved_input(client, update):
    project, design = _integration_ready_project(client)
    _submit(client, project, _assembly_manifest(design))
    saved = _get(client, project)
    assert _revise(client, project, saved, **update).status_code == 400
    assert _get(client, project)["manifest"] == saved["manifest"]


def test_openapi_revision_sections_match_original_manifest_contract():
    from pathlib import Path
    from app.services.integration_inventory_recovery import SECTION_FIELDS

    spec = yaml.safe_load(Path("skills/openapi.yaml").read_text())
    schemas = spec["components"]["schemas"]
    fields = schemas["IntegrationInventorySectionUpdates"]["properties"]
    assert set(fields) == SECTION_FIELDS
    for field, schema in fields.items():
        assert schema == schemas["IntegrationInventoryManifest"]["properties"][field]
    assert spec["paths"]["/skills/integration-inventory-get"]["post"]["operationId"] == "harnoq_integration_inventory_get"


@pytest.mark.parametrize('command', [
    'pytest tests/test_reconciliation.py::test_path -q',
    r'& "C:\project\.venv\Scripts\python.exe" -m pytest tests/test_reconciliation.py::test_path -q',
])
def test_repaired_history_can_leave_proposals_and_close_with_structured_qa(client, command):
    """A repaired finding survives a zero-gap audit without a historical mapping."""
    import json
    project, design = _integration_ready_project(client)
    initial_manifest = _assembly_manifest(design)
    initial = _submit(client, project, initial_manifest)
    proposal = initial_manifest['proposed_items'][0]
    created = client.post('/skills/integration-item-create', json={
        'project_number': project['project_number'],
        **{k: v for k, v in proposal.items() if k != 'key'},
        'validation_id': initial['validation_id'], 'source_row_key': proposal['key'],
    })
    assert created.status_code == 200, created.get_json()
    from app.repositories import delivery_item_repo
    item = delivery_item_repo.list_by_project(project['id'])[0]
    runs = f"/projects/{project['project_number']}/delivery-stage-runs"
    run = client.post(f'{runs}/integration', json={
        'branch': 'integration', 'code_revision': 'merged', 'design_revision': 'confirmed',
    }).get_json()
    proof = client.patch(f'/delivery-items/{item.id}', json={
        'status': 'covered', 'covered_by': run['run_number'],
        'evidence_command': command, 'evidence_output': '1 passed',
        'evidence_exit_code': 0, 'verified_revision': 'merged',
        'repair_record': 'Connected the scheduler to BatchJob and verified delivery.',
    })
    assert proof.status_code == 200, proof.get_json()
    saved = _get(client, project)
    assert saved['required_reruns'][0]['command'] == command
    fixed = _zero_gap_manifest(design, round_number=2)
    rerun = next(row for row in fixed['evidence'] if row['key'] == 'RERUN')
    rerun['command'] = command
    failed = _revise(client, project, saved, evidence_updates=[rerun | {'exit_code': 1}])
    assert failed.status_code == 400
    assert _get(client, project)['validation_id'] == saved['validation_id']
    response = _revise(client, project, saved,
        evidence_updates=fixed['evidence'],
        section_updates={k: v for k, v in fixed.items() if k not in ('found_in_round', 'evidence')})
    result = response.get_json()
    assert response.status_code == 200 and result['valid'], result
    assert not result['completion_errors'], result
    assert _get(client, project)['manifest']['proposed_items'] == []
    assert delivery_item_repo.get(item.id).status == 'covered'
    report = {'role': 'qa', 'item_numbers': [item.item_number], 'command': command,
              'output': '1 passed', 'exit_code': 0, 'code_revision': 'merged'}
    updated = client.post(f"{runs}/{run['run_number']}/update", json={'evidence': report})
    assert updated.status_code == 200, updated.get_json()
    assert json.loads(updated.get_json()['evidence']) == report
    closed = client.post(f"{runs}/{run['run_number']}/close", json={'merge_reference': 'PR-1'})
    assert closed.status_code == 200, closed.get_json()
