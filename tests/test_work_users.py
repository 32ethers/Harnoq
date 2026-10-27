from datetime import datetime
from types import SimpleNamespace

import pytest
from peewee import IntegrityError

from app.models.delivery_item import DeliveryItem
from app.models.delivery_stage_run import DeliveryStageRun
from app.models.source_requirement import SourceRequirement, SourceRequirementVersion
from app.models.user import AuditRecord
from app.models.work_owner import WorkAssignment, WorkOwner
from app.services import source_requirement_service, user_service, work_owner_service
from tests.test_users_auth import _client, identities


def _project(client):
    return client.post("/projects", json={"name": "Work users"}).get_json()


def _requirement(client, project, **data):
    response = client.post(f"/projects/{project['id']}/source-requirements", json={
        "title": "Requirement", "source_type": "manual_note", "raw_content": "Original content", **data,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def _item(client, project, kind="seam", **data):
    response = client.post(f"/projects/{project['id']}/delivery-items", json={
        "kind": kind, "subject": f"Finding {kind}", **({"driver": "browser"} if kind == "journey" else {}), **data,
    })
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_new_requirements_and_both_delivery_stages_default_to_jwt_user(app, identities):
    member, _ = _client(app, "alice")
    project = _project(member)
    entities = [_requirement(member, project, submitted_by="External submitter"),
                _item(member, project), _item(member, project, "journey")]
    for entity in entities:
        assert entity["assignee_number"] == identities["alice"].user_number
        assert entity["assignee"] == "Alice"
        assert entity["assignee_user"]["username"] == "alice"
        assert "id" not in entity["assignee_user"]
    assert entities[0]["submitted_by"] == "External submitter"
    with app.app_context():
        events = list(WorkAssignment.select())
        assert len(events) == 3
        assert all(event.actor_id == event.owner_id == identities["alice"].id for event in events)


def test_confirmed_requirement_assignment_preserves_content_lock_and_versions(app, identities):
    admin, _ = _client(app, "admin")
    member, _ = _client(app, "alice")
    requirement = _requirement(admin, _project(admin))
    path = f"/source-requirements/{requirement['id']}"
    assert admin.post(path + "/confirm").status_code == 200
    before = admin.get(path).get_json()
    versions = admin.get(path + "/versions").get_json()
    assigned = admin.post(path + "/assignee", json={"assignee_number": identities["alice"].user_number})
    assert assigned.status_code == 200
    after = assigned.get_json()
    assert after["assignee"] == "Alice"
    for field in ("raw_content", "status", "updated_at", "current_version_number"):
        assert after[field] == before[field]
    assert admin.get(path + "/versions").get_json() == versions
    assert member.patch(path, json={"raw_content": "Override"}).status_code == 409
    assert member.post(path + "/assignee", json={"assignee_number": None}).status_code == 403
    assert admin.post(path + "/assignee", json={"assignee_number": None}).get_json()["assignee"] is None


@pytest.mark.parametrize("kind", ["seam", "journey"])
def test_delivery_assignment_preserves_evidence_and_rejects_invalid_users(app, identities, kind):
    admin, _ = _client(app, "admin")
    item = _item(admin, _project(admin), kind)
    path = f"/delivery-items/{item['id']}/assignee"
    result = admin.post(path, json={"assignee_number": identities["alice"].user_number})
    assert result.status_code == 200
    after = result.get_json()
    assert {key: value for key, value in after.items() if not key.startswith("assignee")} == {
        key: value for key, value in item.items() if not key.startswith("assignee")
    }
    assert admin.post(path, json={"assignee_number": "alice"}).status_code == 400
    assert admin.post(path, json={"assignee_number": "USER-99999"}).status_code == 404
    assert admin.post(path, json={}).status_code == 400
    admin.patch(f"/users/{identities['other'].user_number}", json={"is_active": False})
    assert admin.post(path, json={"assignee_number": identities["other"].user_number}).status_code == 400


def test_creation_cannot_assign_another_users_work_without_admin_and_rolls_back(app, identities):
    member, _ = _client(app, "alice")
    project = _project(member)
    payload = {"title": "Forbidden", "source_type": "manual_note", "assignee_number": identities["other"].user_number}
    assert member.post(f"/projects/{project['id']}/source-requirements", json=payload).status_code == 403
    assert member.get(f"/projects/{project['id']}/source-requirements").get_json() == []
    payload = {"kind": "seam", "subject": "Forbidden", "assignee_number": identities["other"].user_number}
    assert member.post(f"/projects/{project['id']}/delivery-items", json=payload).status_code == 403
    assert member.get(f"/projects/{project['id']}/delivery-items").get_json() == []


def test_stage_run_jwt_owner_is_visible_in_api_and_project_context(app, identities):
    from tests.test_integration_inventory_validator import _integration_ready_project
    admin, _ = _client(app, "admin")
    project, _ = _integration_ready_project(admin)
    base = f"/projects/{project['project_number']}/delivery-stage-runs"
    result = admin.post(base + "/integration", json={"code_revision": "abc", "design_revision": "design-basis"})
    assert result.status_code == 201, result.get_json()
    assert result.get_json()["assignee_user"]["username"] == "admin"
    runs = admin.get(base + "?stage=integration").get_json()
    assert len(runs) == 1 and runs[0]["assignee_number"] == identities["admin"].user_number
    assert admin.get(base + "?stage=unknown").status_code == 400


def test_legacy_work_migration_preserves_records_and_skips_cleared_user_history(app, client):
    project = _project(client)
    requirement = _requirement(client, project)
    item = _item(client, project)
    cleared = _item(client, project, "journey")
    with app.app_context():
        user = user_service.create({"username": "legacy_owner", "display_name": "Legacy", "password": "1"})
        run = DeliveryStageRun.create(project=project["id"], run_number="P1-RUN-1", stage="integration", status="closed")
        model = SourceRequirement.get_by_id(requirement["id"])
        model.status, model.deleted_at = "confirmed", datetime(2025, 1, 1)
        model.save()
        cleared_model = DeliveryItem.get_by_id(cleared["id"])
        work_owner_service.apply(cleared_model, {"assignee_number": user.user_number})
        work_owner_service.apply(cleared_model, {"assignee_number": None})
        before = {model: [row.__data__.copy() for row in model.select().order_by(model.id)]
                  for model in (SourceRequirement, SourceRequirementVersion, DeliveryItem, DeliveryStageRun)}
    result = app.test_cli_runner().invoke(args=["link-legacy-work", "--user", user.user_number, "--project", "P1"])
    assert result.exit_code == 0, result.output
    with app.app_context():
        for model, rows in before.items():
            assert [row.__data__ for row in model.select().order_by(model.id)] == rows
        assert WorkOwner.select().count() == 3
        assert work_owner_service.owner(cleared_model) is None
        assert work_owner_service.metadata(run)["assignee_number"] == user.user_number
        assert work_owner_service.link_legacy(user.user_number)["linked"] == {"source_requirement": 0, "delivery_item": 0, "stage_run": 0}
        with pytest.raises(IntegrityError), app.config["DATABASE"].atomic():
            WorkOwner.create(user=user)
        with pytest.raises(IntegrityError), app.config["DATABASE"].atomic():
            WorkAssignment.create(source_requirement=requirement["id"], delivery_item=item["id"], owner=user, action="assigned")


def test_mcp_assignment_and_listing_show_registered_users_and_audit_actual_actor(app, identities, monkeypatch):
    from harnoq_mcp import server
    admin, login = _client(app, "admin")
    project = _project(admin)
    requirement, item = _requirement(admin, project), _item(admin, project)
    incoming = SimpleNamespace(headers={"Authorization": "Bearer " + login["access_token"]})
    monkeypatch.setattr(server.mcp, "get_context", lambda: SimpleNamespace(request_context=SimpleNamespace(request=incoming)))
    monkeypatch.setattr(server, "_wsgi_app", app)
    target = identities["alice"].user_number
    result = server.harnoq_source_requirement_assign("P1", requirement["source_requirement_number"], target)
    assert result["assignee_user"]["username"] == "alice" and "id" not in result
    result = server.harnoq_delivery_item_assign("P1", item["item_number"], target)
    assert result["assignee"] == "Alice" and "id" not in result
    listed = server.harnoq_integration_item_list("P1")
    assert listed["delivery_items"][0]["assignee_number"] == target
    with app.app_context():
        record = AuditRecord.select().order_by(AuditRecord.id.desc()).get()
        assert record.actor_id == identities["admin"].id
        assert record.target_number == "P1"
        assigned = AuditRecord.get(AuditRecord.action == "users.assign_delivery_item_by_number")
        assert assigned.target_number == item["item_number"]
