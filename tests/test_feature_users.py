from datetime import datetime
from types import SimpleNamespace

import pytest

from app.models.feature import Feature, FeatureVersion
from app.models.feature_owner import FeatureAssignment, FeatureOwner, FeatureVersionOwner
from app.services import feature_owner_service, feature_service, user_service
from tests.test_users_auth import _client, identities


def _feature(client, **fields):
    project = client.post("/projects", json={"name": "P"}).get_json()
    response = client.post(f"/projects/{project['id']}/features", json={"name": "F", **fields})
    assert response.status_code == 201, response.get_json()
    return response.get_json()


def test_feature_owner_is_registered_account_and_survives_version_restore(app, identities):
    admin, _ = _client(app, "admin")
    alice, other = identities["alice"].user_number, identities["other"].user_number
    admin.patch(f"/users/{alice}", json={"display_name": "Same Name"})
    admin.patch(f"/users/{other}", json={"display_name": "Same Name"})
    feature = _feature(admin, assignee_number=alice)
    assert feature["assignee_user"]["username"] == "alice"
    assert "id" not in feature["assignee_user"]
    path = f"/features/{feature['id']}"
    reassigned = admin.patch(path, json={"assignee_number": other}).get_json()
    assert reassigned["current_version_number"] == 2
    assert reassigned["assignee_number"] == other
    versions = admin.get(path + "/versions").get_json()
    assert {version["assignee_number"] for version in versions} == {alice, other}
    restored = admin.post(path + "/versions/1/restore").get_json()
    assert restored["assignee_number"] == alice
    assert restored["current_version_number"] == 3
    admin.patch(f"/users/{alice}", json={"display_name": "Renamed"})
    assert admin.get(path).get_json()["assignee"] == "Renamed"
    assert admin.get(path + "/versions").get_json()[-1]["assignee"] == "Same Name"
    cleared = admin.post(path + "/assignee", json={"assignee_number": None}).get_json()
    assert cleared["assignee"] is None and cleared["assignee_number"] is None
    assert admin.post(path + "/versions/1/restore").get_json()["assignee_number"] == alice


def test_feature_assignment_permissions_and_validation_are_atomic(app, identities):
    admin, _ = _client(app, "admin")
    member, _ = _client(app, "alice")
    feature = _feature(admin, assignee_number=identities["alice"].user_number)
    path = f"/features/{feature['id']}"
    update = {"name": "Edited", "assignee_number": identities["alice"].user_number}
    assert member.patch(path, json=update).status_code == 200
    response = member.patch(path, json={"name": "Forbidden", "assignee_number": identities["other"].user_number})
    assert response.status_code == 403
    assert admin.get(path).get_json()["name"] == "Edited"
    assert admin.patch(path, json={"assignee": "invented-agent"}).status_code == 400
    assert admin.patch(path, json={"assignee_number": "USER-99999"}).status_code == 404
    admin.patch(f"/users/{identities['other'].user_number}", json={"is_active": False})
    assert admin.patch(path, json={"assignee_number": identities["other"].user_number}).status_code == 400
    with app.app_context():
        count = Feature.select().count()
    assert admin.post(f"/projects/{feature['project_id']}/features", json={"name": "Invalid", "assignee": "invented-agent"}).status_code == 400
    with app.app_context():
        assert Feature.select().count() == count


def test_legacy_feature_link_preserves_content_and_versions_and_skips_user_history(app, client):
    features = [_feature(client) for _ in range(3)]
    with app.app_context():
        user = user_service.create({"username": "legacy_owner", "display_name": "Legacy", "password": "1"})
        deleted = Feature.get_by_id(features[0]["id"])
        deleted.deleted_at = datetime(2025, 1, 1)
        deleted.save()
        feature_service.update(features[1]["id"], {"assignee_number": user.user_number})
        feature_service.update(features[2]["id"], {"assignee_number": user.user_number})
        feature_service.update(features[2]["id"], {"assignee_number": None})
        before = [feature.__data__.copy() for feature in Feature.select().order_by(Feature.id)]
        versions_before = [version.__data__.copy() for version in FeatureVersion.select().order_by(FeatureVersion.id)]
    result = app.test_cli_runner().invoke(args=["link-legacy-features", "--user", user.user_number])
    assert result.exit_code == 0 and "linked 1 historical features" in result.output
    with app.app_context():
        after = [feature.__data__ for feature in Feature.select().order_by(Feature.id)]
        assert after[0]["assignee"] == user.display_name
        before[0]["assignee"] = user.display_name
        assert after == before
        assert [version.__data__ for version in FeatureVersion.select().order_by(FeatureVersion.id)] == versions_before
        assert feature_owner_service.owner(deleted.id).user_id == user.id
        assert feature_owner_service.owner(features[2]["id"]) is None
        assert feature_owner_service.link_legacy_features(user.user_number)["linked_features"] == 0
        assert FeatureAssignment.select().where(FeatureAssignment.action == "legacy_assigned").count() == 1


def test_restoring_pre_user_feature_snapshot_keeps_text_without_guessing_identity(app, identities):
    admin, _ = _client(app, "admin")
    feature = _feature(admin)
    path = f"/features/{feature['id']}"
    admin.patch(path, json={"assignee_number": identities["alice"].user_number})
    result = admin.post(path + "/versions/1/restore")
    assert result.status_code == 200
    assert result.get_json()["assignee_number"] is None
    with app.app_context():
        assert FeatureVersionOwner.select().count() == 1


def test_mcp_feature_assignment_uses_user_number_and_audits_operator(app, identities, monkeypatch):
    from harnoq_mcp import server
    admin, login = _client(app, "admin")
    feature = _feature(admin)
    request = SimpleNamespace(headers={"Authorization": "Bearer " + login["access_token"]})
    monkeypatch.setattr(server.mcp, "get_context", lambda: SimpleNamespace(request_context=SimpleNamespace(request=request)))
    monkeypatch.setattr(server, "_wsgi_app", app)
    result = server.harnoq_feature_assign("P1", feature["feature_number"], identities["alice"].user_number)
    assert result["status"] == "succeeded"
    assert server.harnoq_feature_get("P1", feature["feature_number"])["feature"]["assignee_user"]["username"] == "alice"
    with app.app_context():
        assignment = FeatureAssignment.select().order_by(FeatureAssignment.id.desc()).get()
        assert assignment.actor_id == identities["admin"].id
        assert assignment.owner_id == identities["alice"].id
    _, login = _client(app, "alice")
    request.headers = {"Authorization": "Bearer " + login["access_token"]}
    with pytest.raises(server.HarnoqError, match="403"):
        server.harnoq_feature_assign("P1", feature["feature_number"], None)
