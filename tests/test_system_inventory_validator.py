"""Server-owned System inventory receipt and SYS creation binding."""
import pytest

from app.core.errors import ConflictError, ValidationError
from app.repositories import delivery_item_repo
from app.services import (
    delivery_gate, delivery_item_service, project_constitution_service,
    system_inventory_validator,
)
from tests.test_delivery_gate import _capability_task, _drive_to_completed


def _project(client):
    return client.post("/projects", json={"name": "System audit"}).get_json()


def _confirmed_input(client, project):
    row = client.post(
        f"/projects/{project['id']}/source-requirements",
        json={"title": "note", "source_type": "manual_note", "raw_content": "A person records a note."},
    ).get_json()
    return client.post(f"/source-requirements/{row['id']}/confirm").get_json()


def _manifest(input_number, *, key="journey:note", found_in_round=1):
    return {
        "found_in_round": found_in_round,
        "input_audits": [{"input_number": input_number, "journey_keys": [key]}],
        "nfr_audits": [],
        "proposed_items": [{
            "key": key, "kind": "journey", "subject": "Record one note and observe it.",
            "source": f"{input_number} raw_content", "driver": "browser automation",
            "found_in_round": found_in_round,
        }],
        "rerun_evidence": [],
    }


def _empty_manifest(input_number):
    return {
        "found_in_round": 1,
        "input_audits": [{"input_number": input_number, "journey_keys": [], "no_journey_reason": "No executable user action."}],
        "nfr_audits": [], "proposed_items": [], "rerun_evidence": [],
    }


def test_system_inventory_requires_full_input_audit_and_binds_create(client):
    project = _project(client)
    input_row = _confirmed_input(client, project)
    missing = system_inventory_validator.validate(project["id"], {
        "found_in_round": 1, "input_audits": [], "nfr_audits": [],
        "proposed_items": [], "rerun_evidence": [],
    })
    assert not missing["valid"]
    manifest = _manifest(input_row["source_requirement_number"])
    validated = system_inventory_validator.validate(project["id"], manifest)
    assert validated["valid"], validated["errors"]
    with pytest.raises(ValidationError):
        system_inventory_validator.authorize_create(
            project["id"], validated["validation_id"], None, manifest["proposed_items"][0]
        )
    with pytest.raises(ConflictError, match="payload differs"):
        system_inventory_validator.authorize_create(
            project["id"], validated["validation_id"], "journey:note",
            manifest["proposed_items"][0] | {"subject": "Changed after validation."},
        )
    receipt = system_inventory_validator.authorize_create(
        project["id"], validated["validation_id"], "journey:note", manifest["proposed_items"][0]
    )
    item = delivery_item_service.create(project["id"], manifest["proposed_items"][0])
    system_inventory_validator.record_created(receipt, "journey:note", item.item_number)
    with pytest.raises(ConflictError):
        system_inventory_validator.authorize_create(
            project["id"], validated["validation_id"], "journey:note", manifest["proposed_items"][0]
        )


def test_system_inventory_basis_change_invalidates_receipt(client):
    project = _project(client)
    input_row = _confirmed_input(client, project)
    validated = system_inventory_validator.validate(
        project["id"], _manifest(input_row["source_requirement_number"])
    )
    assert validated["valid"]
    assert not system_inventory_validator.status(project["id"])["complete"]
    # Confirmation input is immutable, so a new confirmed record is the
    # meaningful baseline change and must make the old audit stale.
    _confirmed_input(client, project)
    assert "basis changed" in system_inventory_validator.status(project["id"])["errors"][0]


def test_journey_and_nfr_driver_is_a_service_invariant(client):
    project = _project(client)
    with pytest.raises(ValidationError, match="driver is required"):
        delivery_item_service.create(project["id"], {
            "kind": "journey", "subject": "Observe a whole path.", "source": "INPUT-1",
        })


def test_duplicate_nfr_commitments_have_distinct_required_occurrence_keys(client):
    project = _project(client)
    project_constitution_service.update(project["id"], {
        "nfr_baseline": "| Metric | Target | Applies to |\n| --- | --- | --- |\n| latency | 1 second | all requests |\n| latency | 1 second | all requests |",
    })
    initial = system_inventory_validator.validate(project["id"], {
        "found_in_round": 1, "input_audits": [], "nfr_audits": [],
        "proposed_items": [], "rerun_evidence": [],
    })
    keys = initial["required_nfr_keys"]
    assert len(keys) == 2 and keys[0] != keys[1]
    one = {
        "found_in_round": 1, "input_audits": [],
        "nfr_audits": [{"nfr_key": keys[0], "proposed_item_key": "nfr:one"}],
        "proposed_items": [{"key": "nfr:one", "kind": "nfr", "subject": "Measure latency.", "source": "nfr_baseline", "driver": "subprocess assertions", "found_in_round": 1}],
        "rerun_evidence": [],
    }
    assert not system_inventory_validator.validate(project["id"], one)["valid"]
    one["nfr_audits"].append({"nfr_key": keys[1], "proposed_item_key": "nfr:two"})
    one["proposed_items"].append({"key": "nfr:two", "kind": "nfr", "subject": "Measure repeated latency commitment.", "source": "nfr_baseline", "driver": "subprocess assertions", "found_in_round": 1})
    assert system_inventory_validator.validate(project["id"], one)["valid"]
